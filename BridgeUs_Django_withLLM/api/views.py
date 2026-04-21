import logging
import os
from functools import lru_cache
from uuid import uuid4

from django.core.cache import cache
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIConversation
from .serializers import (
    AIConversationSerializer,
    DialogueReplySerializer,
    DialogueSessionCreateSerializer,
)

SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)
DEFAULT_DIALOGUE_COLLECTION = os.getenv(
    "DEFAULT_DIALOGUE_COLLECTION",
    "general_knowledge",
)
TOPIC_CONFIGS = {
    102: {
        "collection_name": "nuclear_energy_all",
        "topic_description": "台灣是否應重啟核電廠以應對能源轉型與減碳需求",
    },
}


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


def _compute_user_stance_score(survey_answers: dict[str, int]) -> float:
    if not survey_answers:
        return 0.5

    values = list(survey_answers.values())
    average = sum(values) / len(values)
    return round((average - 1) / 4, 2)


def _resolve_stances(user_stance_score: float) -> tuple[str, str, str]:
    if user_stance_score >= 0.55:
        return (
            "較支持目前政策／主張",
            "較反對目前政策／主張",
            "認為這項政策／主張的風險、代價或副作用可能被低估。",
        )
    if user_stance_score <= 0.45:
        return (
            "較反對目前政策／主張",
            "較支持目前政策／主張",
            "認為這項政策／主張有其必要性與公共利益上的正當性。",
        )

    return (
        "立場尚未明確",
        "提出相反觀點",
        "會針對使用者當前傾向提出另一側的價值取向、風險判斷與政策考量。",
    )


def _build_topic_config(
    *,
    topic_id: int,
    topic_title: str,
    topic_description: str,
    survey_answers: dict[str, int],
) -> dict[str, str | float]:
    topic_meta = TOPIC_CONFIGS.get(topic_id, {})
    user_stance_score = _compute_user_stance_score(survey_answers)
    user_stance_label, agent_stance, agent_stance_summary = _resolve_stances(
        user_stance_score
    )

    return {
        "topic": topic_title,
        "topic_description": (
            topic_description
            or topic_meta.get("topic_description")
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
        )

        session = DialogueSession(
            topic=topic_config["topic"],
            topic_description=topic_config["topic_description"],
            agent_stance=topic_config["agent_stance"],
            agent_stance_summary=topic_config["agent_stance_summary"],
            user_stance_label=topic_config["user_stance_label"],
            user_stance_score=topic_config["user_stance_score"],
            user_initial_argument=validated.get("user_initial_argument", ""),
        )

        session_id = uuid4().hex
        cache.set(
            _session_cache_key(session_id),
            {
                "user_id": request.user.id,
                "collection_name": topic_config["collection_name"],
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
