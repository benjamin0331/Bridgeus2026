import logging
import uuid as _uuid_mod
from uuid import uuid4

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTStatelessUserAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    DiscomfortReport,
    Issue,
    PlatformFeedback,
    PostDialogueResponse,
)
from .dialogue_topics import (
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
    MatchingRoomSemanticTreeSerializer,
    MatchingRoomSemanticTreeTimelineSerializer,
    MatchingTopicSerializer,
    PlatformFeedbackSerializer,
    PlatformFeedbackOutputSerializer,
    PostDialogueResponseConsentSerializer,
    PostDialogueResponseOutputSerializer,
    PostDialogueResponseSerializer,
)
from .services.dialogue_session import (
    _dialogue_session_cache_payload_from_record,
    _dialogue_session_response_payload,
    _get_dialogue_runtime,
    _restore_dialogue_session_record_for_user,
    _update_ai_session_stance_drift,
    _build_topic_config,
    create_dialogue_session_record,
    get_dialogue_agent,
    invalidate_dialogue_session_cache,
    session_metadata,
    update_semantic_tree_state,
    update_session_metadata,
)
from .services.history import (
    _get_history_ai_record_for_user,
    _history_ai_detail,
    _history_ai_summary,
    _history_match_detail,
    _history_match_summary,
    _semantic_tree_root_name,
    _semantic_tree_root_name_for_topic_id,
)
from .services.room_state import (
    _build_matching_state_payload,
    _build_room_messages_payload,
    _get_room_match_for_user,
    _touch_room_match_for_user_activity,
)
from .services.stance_scoring import (
    _compute_user_stance_score,
    _resolve_open_answers,
    _resolve_stance_category,
)


logger = logging.getLogger(__name__)


def _get_dialogue_session_record_for_user(*, session_id: str, user_id: int):
    session_record, _ = _restore_dialogue_session_record_for_user(
        session_id=session_id,
        user_id=user_id,
    )
    if not session_record:
        return None, Response(
            {"detail": "找不到對話 session，請重新建立對話。"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return session_record, None


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
            user_reasoning_mode=topic_config["user_reasoning_mode"],
        )

        session_id = uuid4().hex
        create_dialogue_session_record(
            user_id=request.user.id,
            session_id=session_id,
            topic_id=validated["topic_id"],
            topic_title=topic_config["topic"],
            collection_name=topic_config["collection_name"],
            survey_context={
                "survey_answers": validated.get("survey_answers", {}),
                "survey_open_answers": topic_config["survey_open_answers"],
                "semantic_vector_interface": topic_config[
                    "semantic_vector_interface"
                ],
                "q9_embedding": topic_config["q9_embedding"],
            },
            metadata=session_metadata(session, stance_drift=None),
        )

        return Response(
            {
                "session_id": session_id,
                "dialogue_phase": session.dialogue_phase.value,
                "stance_score": session.user_stance_score,
                "stance_category": _resolve_stance_category(
                    topic_id=validated["topic_id"],
                    user_stance_score=session.user_stance_score,
                ),
                "stance_label": session.user_stance_label,
                "stance_drift": None,
                "history": session.to_dict()["history"],
            },
            status=status.HTTP_201_CREATED,
        )


class DialogueSessionLatestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        topic_id = request.query_params.get("topic_id")
        try:
            topic_id = int(topic_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "topic_id 必須是有效的議題編號。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = (
            DialogueSessionRecord.objects.filter(
                user=request.user,
                topic_id=topic_id,
                status=DialogueSessionRecord.Status.ACTIVE,
            )
            .order_by("-last_activity_at", "-id")
            .first()
        )
        if record is None:
            return Response(
                {"detail": "找不到可恢復的對話 session。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        session_record, restored_from = _restore_dialogue_session_record_for_user(
            session_id=record.session_id,
            user_id=request.user.id,
        )
        if session_record is None:
            return Response(
                {"detail": "找不到可恢復的對話 session。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            _dialogue_session_response_payload(
                session_record=session_record,
                restored_from=restored_from,
            )
        )


class DialogueSessionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id: str):
        session_record, restored_from = _restore_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if session_record is None:
            return Response(
                {"detail": "找不到對話 session，請重新建立對話。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            _dialogue_session_response_payload(
                session_record=session_record,
                restored_from=restored_from,
            )
        )


class DialogueSessionReplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        _, DialoguePhase, DialogueSession = _get_dialogue_runtime()
        serializer = DialogueReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session_record, _ = _restore_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )

        if not session_record:
            return Response(
                {"detail": "找不到對話 session，請重新建立對話。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        user_message = serializer.validated_data["message"].strip()
        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(user_message)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

        prompt_embedding = None
        try:
            from chat.services.embedding import get_embedding

            prompt_embedding = get_embedding(user_message)
        except Exception:
            logger.exception(
                "Embedding failed for AI dialogue prompt session=%s user=%s.",
                session_id,
                request.user.id,
            )

        saved_turn = AIConversation.objects.create(
            user=request.user,
            session_id=session_id,
            topic_id=session_record.get("topic_id"),
            user_prompt=user_message,
            dialogue_phase=session.dialogue_phase.value,
            embedding=prompt_embedding,
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
            invalidate_dialogue_session_cache(session_id)
            return Response(
                {
                    "detail": "目前無法取得 AI 回覆，請稍後再試。"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from apps.matching.services.ai_agent import split_into_chunks

        chunks = split_into_chunks(reply)
        session.add_agent_message(reply)
        # Drift must be computed on the read-through metadata (still holding
        # the previous stance_drift) before it's overwritten below — otherwise
        # previous_value is always None and direction is always "stable".
        stance_drift = _update_ai_session_stance_drift(
            session_record=session_record,
            session_id=session_id,
            user_id=request.user.id,
        )
        session_record["session"] = session.to_dict()
        session_record["session"]["stance_drift"] = stance_drift
        saved_turn.ai_response = reply
        saved_turn.dialogue_phase = session.dialogue_phase.value
        saved_turn.save(update_fields=["ai_response", "dialogue_phase"])
        update_session_metadata(
            session_id=session_id,
            user_id=request.user.id,
            metadata=session_metadata(session, stance_drift=stance_drift),
        )
        invalidate_dialogue_session_cache(session_id)

        return Response(
            {
                "reply": reply,
                "chunks": chunks,
                "dialogue_phase": session.dialogue_phase.value,
                "stance_score": session.user_stance_score,
                "stance_category": _resolve_stance_category(
                    topic_id=session_record.get("topic_id"),
                    user_stance_score=session.user_stance_score,
                ),
                "stance_label": session.user_stance_label,
                "stance_drift": stance_drift,
                "history": session_record["session"]["history"],
            }
        )


class DialogueSessionSemanticTreeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id: str):
        from apps.matching.services.semantic_tree import semantic_tree_session_payload

        session_record, error_response = _get_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if error_response is not None:
            return error_response

        payload = semantic_tree_session_payload(
            session_record=session_record,
            session_id=session_id,
            root_name=_semantic_tree_root_name_for_topic_id(
                session_record.get("topic_id"),
            ),
        )
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class DialogueSessionSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        from apps.matching.services.semantic_tree import (
            SemanticTreeError,
            analyze_pending_ai_conversations,
        )

        session_record, error_response = _get_dialogue_session_record_for_user(
            session_id=session_id,
            user_id=request.user.id,
        )
        if error_response is not None:
            return error_response

        try:
            payload = analyze_pending_ai_conversations(
                session_record=session_record,
                session_id=session_id,
                user_id=request.user.id,
                root_name=_semantic_tree_root_name_for_topic_id(
                    session_record.get("topic_id"),
                ),
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

        update_semantic_tree_state(
            session_id=session_id,
            user_id=request.user.id,
            semantic_tree=session_record.get("semantic_tree") or {},
        )
        invalidate_dialogue_session_cache(session_id)
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class HistoryConversationListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        history_type = request.query_params.get("type", "all")
        if history_type not in {"all", "ai", "match"}:
            return Response(
                {"detail": "type 必須是 all、ai 或 match。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        if history_type in {"all", "ai"}:
            for record in DialogueSessionRecord.objects.filter(
                user=request.user,
            ).order_by("-last_activity_at", "-id"):
                summary = _history_ai_summary(record)
                if summary["message_count"]:
                    results.append(summary)

        if history_type in {"all", "match"}:
            matches = (
                DialogueMatch.objects.select_related("user_a", "user_b")
                .filter(Q(user_a=request.user) | Q(user_b=request.user))
                .order_by("-created_at", "-id")
            )
            for match in matches:
                summary = _history_match_summary(match, user_id=request.user.id)
                if summary["message_count"]:
                    results.append(summary)

        results.sort(
            key=lambda item: item.get("last_activity_at") or timezone.datetime.min,
            reverse=True,
        )
        return Response({"count": len(results), "results": results})


class HistoryConversationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind: str, conversation_id: str):
        if kind == "ai":
            record = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if record is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(_history_ai_detail(record))

        if kind == "match":
            match = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if match is None:
                return Response(
                    {"detail": "找不到這筆真人對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(_history_match_detail(match, user_id=request.user.id))

        return Response(
            {"detail": "kind 必須是 ai 或 match。"},
            status=status.HTTP_404_NOT_FOUND,
        )


class HistoryConversationSemanticTreeTimelineView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, kind: str, conversation_id: str):
        from apps.matching.services.semantic_tree import (
            semantic_tree_session_timeline_payload,
            semantic_tree_timeline_payload,
        )

        as_of_message_id = (request.query_params.get("as_of_message_id") or "").strip()
        if not as_of_message_id:
            return Response(
                {"detail": "缺少 as_of_message_id 參數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if kind == "ai":
            record = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if record is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            session_record = _dialogue_session_cache_payload_from_record(record)
            payload = semantic_tree_session_timeline_payload(
                session_record=session_record,
                session_id=record.session_id,
                root_name=_semantic_tree_root_name_for_topic_id(record.topic_id),
                source_message_id=as_of_message_id,
            )
        elif kind == "match":
            match = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if match is None:
                return Response(
                    {"detail": "找不到這個配對房間。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            payload = semantic_tree_timeline_payload(
                match=match,
                root_name=_semantic_tree_root_name(match),
                current_user_id=request.user.id,
                source_message_id=as_of_message_id,
            )
        else:
            return Response(
                {"detail": "kind 必須是 ai 或 match。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if payload is None:
            return Response(
                {"detail": "這則訊息還沒有被分析過。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(MatchingRoomSemanticTreeTimelineSerializer(payload).data)


class HistoryConversationSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, kind: str, conversation_id: str):
        from apps.matching.services.semantic_tree import (
            SemanticTreeError,
            analyze_pending_ai_conversations,
            analyze_pending_room_messages,
        )

        if kind == "ai":
            record = _get_history_ai_record_for_user(
                session_id=conversation_id,
                user_id=request.user.id,
            )
            if record is None:
                return Response(
                    {"detail": "找不到這筆 AI 對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            session_record = _dialogue_session_cache_payload_from_record(record)
            try:
                semantic_tree = analyze_pending_ai_conversations(
                    session_record=session_record,
                    session_id=record.session_id,
                    user_id=request.user.id,
                    root_name=_semantic_tree_root_name_for_topic_id(record.topic_id),
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

            record.semantic_tree_state = session_record.get("semantic_tree") or {}
            record.save(update_fields=["semantic_tree_state", "updated_at"])
            invalidate_dialogue_session_cache(record.session_id)
            return Response(
                {
                    **_history_ai_detail(record),
                    "semantic_tree": MatchingRoomSemanticTreeSerializer(semantic_tree).data,
                }
            )

        if kind == "match":
            match = _get_room_match_for_user(
                room_id=conversation_id,
                user_id=request.user.id,
            )
            if match is None:
                return Response(
                    {"detail": "找不到這筆真人對話紀錄。"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            try:
                semantic_tree = analyze_pending_room_messages(
                    match=match,
                    root_name=_semantic_tree_root_name(match),
                    current_user_id=request.user.id,
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

            match.refresh_from_db()
            return Response(
                {
                    **_history_match_detail(match, user_id=request.user.id),
                    "semantic_tree": MatchingRoomSemanticTreeSerializer(semantic_tree).data,
                }
            )

        return Response(
            {"detail": "kind 必須是 ai 或 match。"},
            status=status.HTTP_404_NOT_FOUND,
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
        from apps.matching.services.matcher import get_room_messages

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        messages = get_room_messages(match=match)
        return Response(
            _build_room_messages_payload(
                match=match,
                user_id=request.user.id,
                messages=messages,
            )
        )

    def post(self, request, room_id: str):
        from apps.matching.services.matcher import get_room_messages
        from .models import MatchMessage

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
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
        from apps.matching.services.semantic_tree import semantic_tree_payload

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        payload = semantic_tree_payload(
            match=match,
            root_name=_semantic_tree_root_name(match),
            current_user_id=request.user.id,
        )
        return Response(MatchingRoomSemanticTreeSerializer(payload).data)


class MatchingRoomSemanticTreeTimelineView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id: str):
        from apps.matching.services.semantic_tree import semantic_tree_timeline_payload

        as_of_message_id = (request.query_params.get("as_of_message_id") or "").strip()
        if not as_of_message_id:
            return Response(
                {"detail": "缺少 as_of_message_id 參數。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        match = _get_room_match_for_user(room_id=room_id, user_id=request.user.id)
        if not match:
            return Response(
                {"detail": "找不到這個配對房間。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        payload = semantic_tree_timeline_payload(
            match=match,
            root_name=_semantic_tree_root_name(match),
            current_user_id=request.user.id,
            source_message_id=as_of_message_id,
        )
        if payload is None:
            return Response(
                {"detail": "這則訊息還沒有被分析過。"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(MatchingRoomSemanticTreeTimelineSerializer(payload).data)


class MatchingRoomSemanticTreeAnalyzeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id: str):
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

        match = _touch_room_match_for_user_activity(
            match=match,
            user_id=request.user.id,
        )
        if match.status != DialogueMatch.Status.ACTIVE:
            return Response(
                {"detail": "這個配對房間目前無法分析語意樹。"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            payload = analyze_pending_room_messages(
                match=match,
                root_name=_semantic_tree_root_name(match),
                current_user_id=request.user.id,
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


class PostDialogueResponseView(APIView):
    """POST /api/post-questionnaire/ — submit post-dialogue questionnaire."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PostDialogueResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        discomfort_detail = validated.pop("discomfort_detail", "") or ""

        response_obj = PostDialogueResponse.objects.create(
            user=request.user,
            topic_id=validated["topic_id"],
            session_id=validated.get("session_id") or None,
            room_id=validated.get("room_id") or None,
            experiment_condition=validated["experiment_condition"],
            post_likert_1=validated["post_likert_1"],
            post_likert_2=validated["post_likert_2"],
            post_likert_3=validated["post_likert_3"],
            post_likert_4=validated["post_likert_4"],
            post_likert_5=validated["post_likert_5"],
            post_likert_6=validated["post_likert_6"],
            post_likert_7=validated["post_likert_7"],
            post_likert_8=validated["post_likert_8"],
            exp_stance_change_1=validated["exp_stance_change_1"],
            exp_stance_change_2=validated["exp_stance_change_2"],
            exp_quality_1=validated["exp_quality_1"],
            exp_quality_2=validated["exp_quality_2"],
            exp_reflection_1=validated["exp_reflection_1"],
            exp_reflection_2=validated["exp_reflection_2"],
            ccnd_attention=validated["ccnd_attention"],
            ccnd_awareness=validated["ccnd_awareness"],
            ccnd_influence=validated["ccnd_influence"],
            opponent_judgment=validated.get("opponent_judgment"),
            post_open_comprehension=validated["post_open_comprehension"],
            post_open_feedback=validated.get("post_open_feedback", ""),
            discomfort_flag=validated.get("discomfort_flag", False),
        )

        if response_obj.discomfort_flag and discomfort_detail.strip():
            DiscomfortReport.objects.create(
                response=response_obj,
                detail=discomfort_detail.strip(),
            )

        out = PostDialogueResponseOutputSerializer(response_obj)
        return Response(out.data, status=status.HTTP_201_CREATED)


class PostDialogueResponseConsentView(APIView):
    """PATCH /api/post-questionnaire/<id>/consent/ — record debriefing consent."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, response_id: int):
        try:
            response_obj = PostDialogueResponse.objects.get(
                id=response_id,
                user=request.user,
            )
        except PostDialogueResponse.DoesNotExist:
            return Response(
                {"detail": "找不到這筆問卷紀錄。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PostDialogueResponseConsentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response_obj.consent_confirmed = serializer.validated_data["consent_confirmed"]
        response_obj.save(update_fields=["consent_confirmed", "updated_at"])

        withdrawn = not response_obj.consent_confirmed
        return Response(
            {
                "id": response_obj.id,
                "consent_confirmed": response_obj.consent_confirmed,
                "withdrawn": withdrawn,
                "message": (
                    "你的資料已標記為撤回，不會被納入研究分析。感謝你的參與。"
                    if withdrawn
                    else "感謝你同意繼續參與本研究。"
                ),
            }
        )


class PlatformFeedbackView(APIView):
    """POST /api/platform-feedback/ — submit Part F platform experience feedback."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PlatformFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        try:
            response_obj = PostDialogueResponse.objects.get(
                id=validated["response_id"],
                user=request.user,
            )
        except PostDialogueResponse.DoesNotExist:
            return Response(
                {"detail": "找不到對應的問卷紀錄。"},
                status=status.HTTP_404_NOT_FOUND,
            )

        feedback, _created = PlatformFeedback.objects.update_or_create(
            response=response_obj,
            defaults={
                "ux_matching": validated["ux_matching"],
                "ux_chatroom": validated["ux_chatroom"],
                "ux_nlp_intervention": validated["ux_nlp_intervention"],
                "ux_ccnd": validated["ux_ccnd"],
                "ux_overall": validated["ux_overall"],
                "nps_score": validated["nps_score"],
                "ux_improvement": validated.get("ux_improvement") or "",
            },
        )

        out = PlatformFeedbackOutputSerializer(feedback)
        return Response(out.data, status=status.HTTP_201_CREATED)
class GuestLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        nickname = (request.data.get("nickname") or "Guest")[:30]
        username = f"guest_{_uuid_mod.uuid4().hex[:8]}"
        user = User.objects.create_user(username=username, password=None)
        user.first_name = nickname
        user.save(update_fields=["first_name"])
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user_id": user.id,
                "username": username,
            },
            status=status.HTTP_201_CREATED,
        )


class IssueListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Issue.objects.all()
        author_id = request.query_params.get("author")
        if author_id:
            qs = qs.filter(author_id=author_id)
        data = [
            {
                "id": i.id,
                "title": i.title,
                "body": i.body,
                "author_id": i.author_id,
                "created_at": i.created_at,
            }
            for i in qs
        ]
        return Response(data)

    def post(self, request):
        title = request.data.get("title", "").strip()
        if not title:
            return Response(
                {"detail": "title 必填"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        issue = Issue.objects.create(
            author=request.user,
            title=title,
            body=request.data.get("body", ""),
        )
        return Response(
            {
                "id": issue.id,
                "title": issue.title,
                "body": issue.body,
                "author_id": issue.author_id,
                "created_at": issue.created_at,
            },
            status=status.HTTP_201_CREATED,
        )
