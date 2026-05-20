import logging
import os
from functools import lru_cache
from uuid import uuid4

from django.core.cache import cache
from django.db.models import Q
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTStatelessUserAuthentication

from .models import AIConversation, DialogueMatch, MatchStanceDrift
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
    MatchMessageSerializer,
    MatchingJoinSerializer,
    MatchingRoomMessageCreateSerializer,
    MatchingRoomMessagesSerializer,
    MatchingRoomSemanticTreeSerializer,
    MatchingStateSerializer,
    MatchingTopicSerializer,
)

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)
ANONYMOUS_MATCH_USER_NAME = "匿名對話者"


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


# 問卷分數邏輯
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


def _resolve_stance_category(*, topic_id: int, user_stance_score: float) -> str:
    scoring_config = _get_survey_scoring_config(topic_id)

    if user_stance_score > scoring_config["support_threshold"]:
        return "support"
    if user_stance_score < scoring_config["oppose_threshold"]:
        return "oppose"
    return "neutral"


def _resolve_stances(
    *,
    topic_id: int,
    user_stance_score: float,
) -> tuple[str, str, str]:
    stance_category = _resolve_stance_category(
        topic_id=topic_id,
        user_stance_score=user_stance_score,
    )

    if stance_category == "support":
        return (
            "較支持核電",
            "較反對核電",
            "認為核電的安全、成本與核廢料風險仍被低估，不應輕率視為能源轉型解方。",
        )
    if stance_category == "oppose":
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


def _get_other_user(match: DialogueMatch, *, user_id: int):
    return match.user_b if match.user_a_id == user_id else match.user_a


def _build_matching_state_payload(*, topic_id: int, state, user_id: int) -> dict:
    queue_entry = state.queue_entry
    match = state.match
    other_user_id = None
    other_user_name = None
    if match:
        other_user = _get_other_user(match, user_id=user_id)
        other_user_id = other_user.id
        other_user_name = ANONYMOUS_MATCH_USER_NAME

    payload = {
        "topic_id": topic_id,
        "status": state.status,
        "stance_score": (
            state.profile.stance_score if state.profile else None
        ),
        "stance_category": (
            state.profile.stance_category if state.profile else None
        ),
        "queue_entry_id": queue_entry.id if queue_entry else None,
        "waiting_started_at": (
            queue_entry.waiting_started_at if queue_entry else None
        ),
        "matched_at": queue_entry.matched_at if queue_entry else None,
        "cancelled_at": queue_entry.cancelled_at if queue_entry else None,
        "closed_at": match.closed_at if match else None,
        "match_id": match.id if match else None,
        "room_id": match.room_id if match else None,
        "other_user_id": other_user_id,
        "other_user_name": other_user_name,
    }
    return MatchingStateSerializer(payload).data


def _room_match_state_status(match: DialogueMatch) -> str:
    if match.status == DialogueMatch.Status.ACTIVE:
        return "matched"
    return match.status


def _get_latest_room_stance_drift(*, match: DialogueMatch, user_id: int) -> dict | None:
    latest = (
        MatchStanceDrift.objects.filter(match=match, user_id=user_id)
        .order_by("-measured_at", "-id")
        .first()
    )
    if latest is None:
        return None

    return {
        "drift_value": latest.drift_value,
        "measured_at": latest.measured_at,
    }


def _build_room_messages_payload(*, match: DialogueMatch, user_id: int, messages) -> dict:
    other_user = _get_other_user(match, user_id=user_id)
    payload = {
        "room_id": match.room_id,
        "match_id": match.id,
        "topic_id": match.topic_id,
        "status": _room_match_state_status(match),
        "other_user_id": other_user.id,
        "other_user_name": ANONYMOUS_MATCH_USER_NAME,
        "stance_drift": _get_latest_room_stance_drift(match=match, user_id=user_id),
        "messages": messages,
    }
    return MatchingRoomMessagesSerializer(payload).data


def _get_room_match_for_user(*, room_id: str, user_id: int) -> DialogueMatch | None:
    return (
        DialogueMatch.objects.select_related("user_a", "user_b")
        .filter(room_id=room_id)
        .filter(Q(user_a_id=user_id) | Q(user_b_id=user_id))
        .first()
    )


def _semantic_tree_root_name(match: DialogueMatch) -> str:
    return TOPIC_CONFIGS.get(match.topic_id, {}).get("title") or "核電"


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
    serializer_class = AIConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return AIConversation.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class AIConversationDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AIConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return AIConversation.objects.filter(user=self.request.user)


class DialogueTopicListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTStatelessUserAuthentication]

    def get(self, request):
        serializer = DialogueTopicSerializer(get_dialogue_topics(), many=True)
        return Response(serializer.data)


class DialogueSurveyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTStatelessUserAuthentication]

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
                "session_id": session_id,
                "topic_id": validated["topic_id"],
                "topic_title": topic_config["topic"],
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

        user_message = serializer.validated_data["message"].strip()
        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(user_message)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)
        saved_turn = AIConversation.objects.create(
            user=request.user,
            session_id=session_id,
            topic_id=session_record.get("topic_id"),
            user_prompt=user_message,
            dialogue_phase=session.dialogue_phase.value,
        )

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

        from apps.matching.services.ai_agent import split_into_chunks

        chunks = split_into_chunks(reply)
        session.add_agent_message(reply)
        session_record["session"] = session.to_dict()
        cache.set(cache_key, session_record, timeout=SESSION_TTL_SECONDS)
        saved_turn.ai_response = reply
        saved_turn.dialogue_phase = session.dialogue_phase.value
        saved_turn.save(update_fields=["ai_response", "dialogue_phase"])

        return Response(
            {
                "reply": reply,
                "chunks": chunks,
                "dialogue_phase": session.dialogue_phase.value,
                "history": session_record["session"]["history"],
            }
        )


class MatchingJoinView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import enqueue_for_matching

        serializer = MatchingJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        stance_score = _compute_user_stance_score(
            topic_id=validated["topic_id"],
            survey_answers=validated["survey_answers"],
        )
        stance_category = _resolve_stance_category(
            topic_id=validated["topic_id"],
            user_stance_score=stance_score,
        )
        resolved_open_answers = _resolve_open_answers(
            topic_id=validated["topic_id"],
            survey_open_answers=validated.get("survey_open_answers", {}),
        )

        state = enqueue_for_matching(
            user=request.user,
            topic_id=validated["topic_id"],
            stance_score=stance_score,
            stance_category=stance_category,
            survey_answers=validated["survey_answers"],
            survey_open_answers=resolved_open_answers,
            restart_existing_match=validated.get("restart_existing_match", False),
        )

        return Response(
            _build_matching_state_payload(
                topic_id=validated["topic_id"],
                state=state,
                user_id=request.user.id,
            )
        )


class MatchingStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.matching.services.matcher import get_matching_state

        serializer = MatchingTopicSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        topic_id = serializer.validated_data["topic_id"]

        state = get_matching_state(user=request.user, topic_id=topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=topic_id,
                state=state,
                user_id=request.user.id,
            )
        )


class MatchingCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.matching.services.matcher import (
            MatchingAlreadyMatchedError,
            MatchingNotFoundError,
            cancel_matching,
            get_matching_state,
        )

        serializer = MatchingTopicSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic_id = serializer.validated_data["topic_id"]

        try:
            cancel_matching(user=request.user, topic_id=topic_id)
        except MatchingAlreadyMatchedError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except MatchingNotFoundError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )

        state = get_matching_state(user=request.user, topic_id=topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=topic_id,
                state=state,
                user_id=request.user.id,
            )
        )


class MatchingRoomMessagesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        from apps.matching.services.matcher import (
            close_match_if_idle,
            get_room_messages,
        )

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = close_match_if_idle(match=match)
        messages = get_room_messages(match=match)
        return Response(
            _build_room_messages_payload(
                match=match,
                user_id=request.user.id,
                messages=messages,
            )
        )

    def post(self, request, room_id: str):
        from apps.matching.services.matcher import (
            close_match_if_idle,
            get_room_messages,
        )
        from .models import MatchMessage

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        match = close_match_if_idle(match=match)
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {"detail": "這個配對房間目前無法傳送訊息。"},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = MatchingRoomMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        MatchMessage.objects.create(
            match=match,
            sender=request.user,
            content=serializer.validated_data["content"].strip(),
        )

        messages = get_room_messages(match=match)
        return Response(
            _build_room_messages_payload(
                match=match,
                user_id=request.user.id,
                messages=messages,
            ),
            status=status.HTTP_201_CREATED,
        )


class MatchingRoomSemanticTreeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        from apps.matching.services.matcher import close_match_if_idle
        from apps.matching.services.semantic_tree import semantic_tree_payload

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = close_match_if_idle(match=match)
        payload = semantic_tree_payload(
            match=match,
            root_name=_semantic_tree_root_name(match),
        )
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class MatchingRoomSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id: str):
        from apps.matching.services.matcher import close_match_if_idle
        from apps.matching.services.semantic_tree import (
            SemanticTreeError,
            analyze_pending_room_messages,
        )

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = close_match_if_idle(match=match)
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {"detail": "這個配對房間目前無法分析語意樹。"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            payload = analyze_pending_room_messages(
                match=match,
                root_name=_semantic_tree_root_name(match),
            )
        except SemanticTreeError as exc:
            return Response(
                {
                    "error": exc.code,
                    "message": str(exc),
                    "analysisStatus": exc.code,
                },
                status=exc.status_code,
            )

        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class MatchingRoomLeaveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id: str):
        from apps.matching.services.matcher import close_match, get_matching_state

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        closed_match = close_match(match=match)
        state = get_matching_state(user=request.user, topic_id=closed_match.topic_id)
        return Response(
            _build_matching_state_payload(
                topic_id=closed_match.topic_id,
                state=state,
                user_id=request.user.id,
            )
        )
