import logging
import os
from functools import lru_cache
from uuid import uuid4

from django.core.cache import cache
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIConversation
from .dialogue_topics import (
    TOPIC_CONFIGS,
    get_dialogue_survey,
    get_dialogue_topics,
)
from .serializers import (
    AIConversationSerializer,
    DialogueReplySerializer,
    DialogueSurveySerializer,
    DialogueTopicSerializer,
    DialogueSessionCreateSerializer,
)

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


def _get_survey_scoring_config(topic_id: int) -> dict:
    survey_config = get_dialogue_survey(topic_id) or {}
    scale_config = survey_config.get("scale", {})
    stance_rules = survey_config.get("stance_rules", {})
    likert_questions = survey_config.get("questions", [])

    return {
        "scale_min": int(scale_config.get("min", 1)),
        "scale_max": int(scale_config.get("max", 7)),
        "reverse_question_ids": {
            str(question_id)
            for question_id in stance_rules.get("reverse_question_ids", [])
        },
        "support_threshold": float(stance_rules.get("support_threshold", 4.5)),
        "oppose_threshold": float(stance_rules.get("oppose_threshold", 3.5)),
        "neutral_score": float(
            (
                float(scale_config.get("min", 1))
                + float(scale_config.get("max", 7))
            )
            / 2
        ),
        "likert_question_ids": {
            str(question["id"]) for question in likert_questions
        },
        "open_question_mappings": [
            {
                "id": question["id"],
                "code": question["code"],
            }
            for question in survey_config.get("open_questions", [])
        ],
    }


def _get_open_answer(
    survey_open_answers: dict[str, str],
    *,
    question_id: int,
    question_code: str,
) -> str:
    return (
        survey_open_answers.get(question_code)
        or survey_open_answers.get(str(question_id))
        or ""
    ).strip()


def _compute_user_stance_score(
    *,
    topic_id: int,
    survey_answers: dict[str, int],
) -> float:
    scoring_config = _get_survey_scoring_config(topic_id)
    if not survey_answers:
        return round(scoring_config["neutral_score"], 2)

    adjusted_scores = []

    for question_id in scoring_config["likert_question_ids"]:
        raw_score = survey_answers.get(question_id)
        if raw_score is None:
            continue

        adjusted_score = float(raw_score)

        if question_id in scoring_config["reverse_question_ids"]:
            adjusted_score = (
                scoring_config["scale_min"]
                + scoring_config["scale_max"]
                - adjusted_score
            )

        adjusted_scores.append(adjusted_score)

    if not adjusted_scores:
        return round(scoring_config["neutral_score"], 2)

    return round(sum(adjusted_scores) / len(adjusted_scores), 2)


def _resolve_stances(
    *,
    topic_id: int,
    user_stance_score: float,
) -> tuple[str, str, str]:
    scoring_config = _get_survey_scoring_config(topic_id)

    if user_stance_score > scoring_config["support_threshold"]:
        return (
            "較支持核電",
            "較反對核電",
            "認為核電的安全、成本與核廢料風險仍被低估，不應輕率視為能源轉型解方。",
        )
    if user_stance_score < scoring_config["oppose_threshold"]:
        return (
            "較反對核電",
            "較支持核電",
            "認為核電在減碳與穩定供電上仍具必要性，不應過早排除。",
        )

    return (
        "立場中立或尚未明確",
        "提出相反觀點",
        "會根據使用者當前的考量重點，補上另一側對安全、成本、環境與供電穩定性的判斷。",
    )


def _resolve_open_answers(
    *,
    topic_id: int,
    survey_open_answers: dict[str, str],
) -> dict[str, str]:
    scoring_config = _get_survey_scoring_config(topic_id)
    resolved_answers = {}

    for question in scoring_config["open_question_mappings"]:
        resolved_answers[question["code"]] = _get_open_answer(
            survey_open_answers,
            question_id=question["id"],
            question_code=question["code"],
        )

    return resolved_answers


def _build_topic_config(
    *,
    topic_id: int,
    topic_title: str,
    topic_description: str,
    survey_answers: dict[str, int],
    survey_open_answers: dict[str, str],
    user_initial_argument: str,
) -> dict[str, str | float | dict]:
    topic_meta = TOPIC_CONFIGS.get(topic_id, {})
    user_stance_score = _compute_user_stance_score(
        topic_id=topic_id,
        survey_answers=survey_answers,
    )
    user_stance_label, agent_stance, agent_stance_summary = _resolve_stances(
        topic_id=topic_id,
        user_stance_score=user_stance_score,
    )
    survey_config = get_dialogue_survey(topic_id) or {}
    semantic_vector_interface = survey_config.get("semantic_vector_interface", {})
    resolved_open_answers = _resolve_open_answers(
        topic_id=topic_id,
        survey_open_answers=survey_open_answers,
    )
    resolved_initial_argument = (
        resolved_open_answers.get("Q9", "")
        or user_initial_argument
    )

    return {
        "topic": topic_meta.get("title", topic_title),
        "topic_description": (
            topic_description
            or topic_meta.get("topic_description")
            or topic_meta.get("title")
            or topic_title
        ),
        "collection_name": topic_meta.get(
            "collection_name",
            DEFAULT_DIALOGUE_COLLECTION,
        ),
        "user_stance_label": user_stance_label,
        "user_stance_score": user_stance_score,
        "agent_stance": agent_stance,
        "agent_stance_summary": agent_stance_summary,
        "user_initial_argument": resolved_initial_argument,
        "survey_open_answers": resolved_open_answers,
        "semantic_vector_interface": semantic_vector_interface,
    }


@lru_cache(maxsize=1)
def _get_dialogue_runtime():
    from apps.matching.services.ai_agent import (
        DialogueAgent,
        DialoguePhase,
        DialogueSession,
    )

    return DialogueAgent, DialoguePhase, DialogueSession


@lru_cache(maxsize=8)
def get_dialogue_agent(collection_name: str):
    DialogueAgent, _, _ = _get_dialogue_runtime()
    return DialogueAgent(collection_name=collection_name)


class AIConversationListCreate(generics.ListCreateAPIView):
    queryset = AIConversation.objects.all().order_by("-created_at")
    serializer_class = AIConversationSerializer
    permission_classes = [permissions.IsAuthenticated]


class AIConversationDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = AIConversation.objects.all()
    serializer_class = AIConversationSerializer
    permission_classes = [permissions.IsAuthenticated]


class DialogueTopicListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = DialogueTopicSerializer(get_dialogue_topics(), many=True)
        return Response(serializer.data)


class DialogueSurveyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, topic_id: int):
        survey = get_dialogue_survey(topic_id)
        if not survey:
            return Response(
                {"detail": "找不到這個議題的問卷設定。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DialogueSurveySerializer(survey)
        return Response(serializer.data)


class DialogueSessionCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        _, _, DialogueSession = _get_dialogue_runtime()
        serializer = DialogueSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        topic_config = _build_topic_config(
            topic_id=validated["topic_id"],
            topic_title=validated["topic_title"],
            topic_description=validated.get("topic_description", ""),
            survey_answers=validated.get("survey_answers", {}),
            survey_open_answers=validated.get("survey_open_answers", {}),
            user_initial_argument=validated.get("user_initial_argument", ""),
        )

        session = DialogueSession(
            topic=topic_config["topic"],
            topic_description=topic_config["topic_description"],
            agent_stance=topic_config["agent_stance"],
            agent_stance_summary=topic_config["agent_stance_summary"],
            user_stance_label=topic_config["user_stance_label"],
            user_stance_score=topic_config["user_stance_score"],
            user_initial_argument=topic_config["user_initial_argument"],
        )

        session_id = uuid4().hex
        cache.set(
            _session_cache_key(session_id),
            {
                "user_id": request.user.id,
                "collection_name": topic_config["collection_name"],
                "survey_context": {
                    "survey_answers": validated.get("survey_answers", {}),
                    "survey_open_answers": topic_config["survey_open_answers"],
                    "semantic_vector_interface": topic_config[
                        "semantic_vector_interface"
                    ],
                },
                "session": session.to_dict(),
            },
            timeout=SESSION_TTL_SECONDS,
        )

        return Response(
            {
                "session_id": session_id,
                "dialogue_phase": session.dialogue_phase.value,
                "history": session.to_dict()["history"],
            },
            status=status.HTTP_201_CREATED,
        )


class DialogueSessionReplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        _, DialoguePhase, DialogueSession = _get_dialogue_runtime()
        serializer = DialogueReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cache_key = _session_cache_key(session_id)
        session_record = cache.get(cache_key)

        if not session_record:
            return Response(
                {"detail": "找不到對話 session，請重新建立對話。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if session_record["user_id"] != request.user.id:
            return Response(
                {"detail": "你沒有存取這個對話 session 的權限。"},
                status=status.HTTP_403_FORBIDDEN,
            )

        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(serializer.validated_data["message"])
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

        try:
            reply = get_dialogue_agent(session_record["collection_name"]).respond(
                session
            )
        except Exception:
            logger.exception(
                "Dialogue reply failed for session %s with collection %s.",
                session_id,
                session_record["collection_name"],
            )
            return Response(
                {
                    "detail": "目前無法取得 AI 回覆，請稍後再試。"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        session.add_agent_message(reply)
        session_record["session"] = session.to_dict()
        cache.set(cache_key, session_record, timeout=SESSION_TTL_SECONDS)

        return Response(
            {
                "reply": reply,
                "dialogue_phase": session.dialogue_phase.value,
                "history": session_record["session"]["history"],
            }
        )
