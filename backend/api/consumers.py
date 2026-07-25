import asyncio
import json
import logging
import os
import time
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from api.dialogue_topics import TOPIC_CONFIGS
from apps.matching.services.hh_ai import (
    aredirect_match_to_topic,
    arephrase_match_message,
    asuggest_match_direction,
    hh_ai_assist_enabled,
)
from apps.matching.services.hh_analysis import (
    acalculate_ai_session_stance_drift,
    acalculate_match_stance_drift,
    acheck_match_topic_relevance,
    adetect_match_stalemate,
    aextract_match_opponent_keywords,
    aget_topic_anchor_embedding,
    build_stalemate_prompt,
)
from chat.services.embedding import aget_embedding
from chat.services.emotion import aget_analyze_emotion
from chat.services.filter import check_content_sync

User = get_user_model()
SESSION_TTL_SECONDS = 60 * 60 * 12
DEFAULT_AI_ASSIST_TIMEOUT_SECONDS = 2.0
logger = logging.getLogger(__name__)

# Emotion interception only fires when the message is aimed at the other person.
# A high-arousal statement of fact ("核電其實很危險") should not be intercepted;
# an attack ("你根本不懂") should. Gating on a second-person pronoun keeps recall
# on genuine attacks while excluding factual venting.
_SECOND_PERSON = {"你", "您", "妳"}


def _targets_other(text: str) -> bool:
    """Return True if the message contains a second-person pronoun."""
    return any(pronoun in text for pronoun in _SECOND_PERSON)


def ai_assist_timeout_seconds() -> float:
    raw_value = os.getenv("H_H_AI_ASSIST_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_AI_ASSIST_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_AI_ASSIST_TIMEOUT_SECONDS
    return max(0.0, timeout)


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


def _query_value(scope, key: str) -> str | None:
    query = parse_qs(scope["query_string"].decode())
    return query.get(key, [None])[0]


async def _authenticate_access_token(token: str | None):
    if not token:
        return None

    try:
        from rest_framework_simplejwt.tokens import AccessToken

        access_token = await sync_to_async(AccessToken)(token)
        user_id = access_token["user_id"]
        return await User.objects.aget(id=user_id)
    except Exception:
        return None


class DialogueStreamConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.user = await _authenticate_access_token(_query_value(self.scope, "token"))
        if not self.user:
            await self.close(code=4001)
            return

        session_record = await self._get_session_record()
        if not session_record:
            await self.close(code=4004)
            return

        await self.accept()

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        if data.get("type") != "user_message":
            return

        user_message = data.get("content", "").strip()
        if not user_message:
            return

        await self._stream_response(user_message)

    async def _stream_response(self, user_message: str):
        from apps.matching.services.ai_agent import DialoguePhase, DialogueSession
        from api.views import get_dialogue_agent

        cache_key = _session_cache_key(self.session_id)
        session_record = await sync_to_async(cache.get)(cache_key)

        if not session_record:
            session_record = await self._get_session_record()
            if not session_record:
                await self._send_error("找不到對話 session，請重新建立對話。")
                return

        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(user_message)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

        if session.user_reasoning_mode == "unknown":
            from apps.matching.services.ai_agent import detect_focus_signal
            if detect_focus_signal(user_message):
                session.focus_signal_count += 1
                if session.focus_signal_count >= 2:
                    session.user_reasoning_mode = "collaborative"
        saved_turn = await self._create_ai_conversation(
            session_record=session_record,
            user_message=user_message,
            dialogue_phase=session.dialogue_phase.value,
        )

        agent = get_dialogue_agent(session_record["collection_name"])
        full_response = ""

        try:
            async for chunk in agent.astream_respond(session):
                full_response += chunk
                await self.send(
                    json.dumps(
                        {
                            "type": "agent_stream",
                            "content": chunk,
                        }
                    )
                )
        except Exception:
            logger.exception(
                "Dialogue stream failed for session %s with collection %s.",
                self.session_id,
                session_record.get("collection_name"),
            )
            await self._send_error("AI 回應中斷，請重試。")
            return

        session.add_agent_message(full_response)
        session_record["session"] = session.to_dict()
        await self._update_ai_conversation(
            saved_turn=saved_turn,
            ai_response=full_response,
            dialogue_phase=session.dialogue_phase.value,
        )
        stance_drift = await self._update_session_stance_drift(session_record)
        await sync_to_async(cache.set)(
            cache_key,
            session_record,
            timeout=SESSION_TTL_SECONDS,
        )
        await self._persist_session_record(session_record)

        await self.send(json.dumps({
            "type": "agent_stream_end",
            "stance_drift": stance_drift,
            "turn_id": saved_turn.id if saved_turn is not None else None,
        }))

    async def _get_session_record(self):
        from api.views import _restore_dialogue_session_record_for_user

        session_record, _ = await sync_to_async(
            _restore_dialogue_session_record_for_user
        )(
            session_id=self.session_id,
            user_id=self.user.id,
        )
        return session_record

    async def _update_session_stance_drift(self, session_record: dict):
        try:
            return await acalculate_ai_session_stance_drift(
                session_record=session_record,
                session_id=self.session_id,
                user_id=self.user.id,
            )
        except Exception:
            logger.exception("AI stance drift failed for session %s.", self.session_id)
            return (session_record.get("session") or {}).get("stance_drift")

    async def _persist_session_record(self, session_record: dict):
        from api.views import _persist_dialogue_session_record

        try:
            await sync_to_async(_persist_dialogue_session_record)(session_record)
        except Exception:
            logger.exception(
                "Failed to persist AI session record session=%s user=%s.",
                self.session_id,
                self.user.id,
            )

    async def _create_ai_conversation(
        self,
        *,
        session_record: dict,
        user_message: str,
        dialogue_phase: str,
    ):
        from api.models import AIConversation

        embedding = None
        try:
            embedding = await aget_embedding(user_message)
        except Exception:
            logger.exception(
                "Embedding failed for AI dialogue prompt session=%s user=%s.",
                self.session_id,
                self.user.id,
            )

        try:
            return await AIConversation.objects.acreate(
                user=self.user,
                session_id=self.session_id,
                topic_id=session_record.get("topic_id"),
                user_prompt=user_message,
                dialogue_phase=dialogue_phase,
                embedding=embedding,
            )
        except Exception:
            logger.exception(
                "Failed to persist AI dialogue prompt session=%s user=%s.",
                self.session_id,
                self.user.id,
            )
            return None

    async def _update_ai_conversation(
        self,
        *,
        saved_turn,
        ai_response: str,
        dialogue_phase: str,
    ):
        if saved_turn is None:
            return

        saved_turn.ai_response = ai_response
        saved_turn.dialogue_phase = dialogue_phase
        try:
            await saved_turn.asave(update_fields=["ai_response", "dialogue_phase"])
        except Exception:
            logger.exception(
                "Failed to persist AI dialogue response session=%s user=%s.",
                self.session_id,
                self.user.id,
            )

    async def _send_error(self, message: str):
        await self.send(
            json.dumps(
                {
                    "type": "error",
                    "content": message,
                }
            )
        )


# Stance drift and topic relevance now run per message (see _run_message_analysis),
# mirroring the H-AI per-turn cadence so both cohorts' drift series are comparable.
# Stalemate detection stays throttled: it compares both users' recent messages and
# fires a direction suggestion, so running it every message would spam and needs no
# such granularity. At most once per _STALEMATE_MIN_INTERVAL_SECONDS per match.
_STALEMATE_MIN_INTERVAL_SECONDS = 300

# Per-match stalemate throttle state (single-process only; see USE_REDIS_CHANNEL note).
_match_last_stalemate: dict[int, float] = {}


class MatchRoomConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_id = self.scope["url_route"]["kwargs"]["room_id"]
        self.user = await _authenticate_access_token(_query_value(self.scope, "token"))
        if not self.user:
            await self.close(code=4001)
            return

        self.match = await self._get_active_match_for_user()
        if not self.match:
            await self.close(code=4004)
            return
        if not await self._refresh_current_match_for_activity():
            await self.close(code=4004)
            return

        self.room_group_name = f"match_room_{self.room_id}"
        self.blocked_count = 0
        self.system_prompts_triggered = 0
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "match") and self.match:
            await self._mark_current_user_disconnected()
            _match_last_stalemate.pop(self.match.id, None)
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name,
            )

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        message_type = data.get("type")
        if message_type in {
            "accept_suggestion",
            "modify_suggestion",
            "ignore_suggestion",
        }:
            await self._handle_suggestion_response(data)
            return

        if message_type not in {None, "match_message", "user_message"}:
            return

        content = data.get("content", "").strip()
        if not content:
            return

        if not await self._refresh_current_match_for_activity():
            await self._send_error("聊天室已自動結束，無法再傳送訊息。")
            await self.close(code=4000)
            return

        if hh_ai_assist_enabled():
            await self._handle_ai_assisted_message(content)
            return

        await self._relay_and_persist(content)

    async def _handle_ai_assisted_message(self, content: str):
        try:
            filter_result = check_content_sync(content)
        except Exception:
            logger.exception("Content filter failed for match room %s.", self.room_id)
            filter_result = {"is_blocked": False}

        if filter_result.get("is_blocked"):
            self.blocked_count += 1
            await self._send_system_prompt(
                category="content_blocked",
                message="這則訊息包含可能冒犯對方的用語，請修改後重新發送。",
            )
            return

        emotion = await self._safe_analyze_emotion(content)
        if emotion.get("is_over_threshold") and _targets_other(content):
            await self._send_rephrase_suggestion(
                content,
                trigger_score=emotion.get("score"),
            )
            return

        await self._relay_and_persist(content, emotion_score=emotion.get("score"))

    async def _handle_suggestion_response(self, data: dict):
        suggestion = await self._get_user_suggestion(data.get("suggestion_id"))
        if suggestion is None:
            await self._send_error("找不到這則 AI 建議，請重新發送訊息。")
            return

        action_type = data.get("type")
        response_time_ms = self._suggestion_response_time_ms(suggestion)

        if suggestion.category != suggestion.Category.REPHRASE:
            suggestion.user_action = self._action_value(action_type)
            suggestion.response_time_ms = response_time_ms
            await suggestion.asave(update_fields=["user_action", "response_time_ms"])
            return

        if action_type == "accept_suggestion":
            final_content = suggestion.suggested_content.strip()
            suggestion.user_action = suggestion.Action.ACCEPT
            suggestion.final_content = final_content
            suggestion.response_time_ms = response_time_ms
            await suggestion.asave(
                update_fields=["user_action", "final_content", "response_time_ms"]
            )
            await self._relay_and_persist(final_content)
            return

        if action_type == "ignore_suggestion":
            final_content = (suggestion.original_content or "").strip()
            if not final_content:
                await self._send_error("找不到原始訊息，請重新輸入。")
                return
            suggestion.user_action = suggestion.Action.IGNORE
            suggestion.final_content = final_content
            suggestion.response_time_ms = response_time_ms
            await suggestion.asave(
                update_fields=["user_action", "final_content", "response_time_ms"]
            )
            await self._relay_and_persist(final_content)
            return

        modified_content = (data.get("content") or "").strip()
        if not modified_content:
            await self._send_error("修改後的訊息不可為空白。")
            return

        suggestion.user_action = suggestion.Action.MODIFY
        suggestion.modified_content = modified_content
        suggestion.response_time_ms = response_time_ms
        await suggestion.asave(
            update_fields=["user_action", "modified_content", "response_time_ms"]
        )

        emotion = await self._safe_analyze_emotion(modified_content)
        if emotion.get("is_over_threshold") and _targets_other(modified_content):
            await self._send_rephrase_suggestion(
                modified_content,
                trigger_score=emotion.get("score"),
            )
            return

        suggestion.final_content = modified_content
        await suggestion.asave(update_fields=["final_content"])
        await self._relay_and_persist(modified_content, emotion_score=emotion.get("score"))

    async def _safe_analyze_emotion(self, content: str) -> dict:
        timeout = ai_assist_timeout_seconds()
        try:
            if timeout <= 0:
                return await aget_analyze_emotion(content)
            return await asyncio.wait_for(aget_analyze_emotion(content), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Emotion analysis timed out after %.2fs for match room %s; "
                "relaying message without intervention.",
                timeout,
                self.room_id,
            )
            return {"score": None, "label": "timeout", "is_over_threshold": False}
        except Exception:
            logger.exception("Emotion analysis failed for match room %s.", self.room_id)
            return {"score": 0.0, "label": "neutral", "is_over_threshold": False}

    async def _send_rephrase_suggestion(self, content: str, *, trigger_score=None):
        from api.models import MatchAISuggestion

        try:
            suggested_content, is_llm_generated = await arephrase_match_message(
                content,
                self._topic_label(),
            )
        except Exception:
            logger.exception("Rephrase suggestion failed for match room %s.", self.room_id)
            suggested_content = "你的發言可能帶有較強烈的情緒，建議修改後再發送。"
            is_llm_generated = False

        actions = ["accept", "modify", "ignore"] if is_llm_generated else ["modify", "ignore"]
        context_ids = await self._recent_message_ids()
        suggestion = await MatchAISuggestion.objects.acreate(
            match_id=self.match.id,
            user=self.user,
            category=MatchAISuggestion.Category.REPHRASE,
            original_content=content,
            suggested_content=suggested_content,
            trigger_score=trigger_score,
            context_message_ids=context_ids,
        )
        self.system_prompts_triggered += 1
        await self.send(
            json.dumps(
                {
                    "type": "match_ai_suggestion",
                    "suggestion_id": suggestion.id,
                    "category": suggestion.category,
                    "original_content": content,
                    "suggested_content": suggested_content,
                    "actions": actions,
                }
            )
        )

    async def _send_redirect_suggestion(self, *, trigger_score=None):
        try:
            suggested_content = await aredirect_match_to_topic(
                self.match.id,
                self._topic_label(),
            )
        except Exception:
            logger.exception("Redirect suggestion failed for match room %s.", self.room_id)
            suggested_content = "目前的討論似乎偏離了主題，可以試著回到核心議題的討論。"

        await self._send_context_suggestion(
            category="redirect",
            suggested_content=suggested_content,
            trigger_score=trigger_score,
        )

    async def _send_direction_suggestion(self, *, suggested_content: str | None = None):
        if suggested_content is None:
            try:
                suggested_content = await asuggest_match_direction(
                    self.match.id,
                    self._topic_label(),
                )
            except Exception:
                logger.exception("Direction suggestion failed for match room %s.", self.room_id)
                suggested_content = "可以試著換個角度思考這個議題。"

        await self._send_context_suggestion(
            category="direction",
            suggested_content=suggested_content,
        )

    async def _send_context_suggestion(
        self,
        *,
        category: str,
        suggested_content: str,
        trigger_score=None,
    ):
        from api.models import MatchAISuggestion

        category_value = (
            MatchAISuggestion.Category.REDIRECT
            if category == "redirect"
            else MatchAISuggestion.Category.DIRECTION
        )
        suggestion = await MatchAISuggestion.objects.acreate(
            match_id=self.match.id,
            user=self.user,
            category=category_value,
            suggested_content=suggested_content,
            trigger_score=trigger_score,
            context_message_ids=await self._recent_message_ids(),
        )
        self.system_prompts_triggered += 1
        await self.send(
            json.dumps(
                {
                    "type": "match_ai_suggestion",
                    "suggestion_id": suggestion.id,
                    "category": suggestion.category,
                    "suggested_content": suggested_content,
                    "actions": ["accept"],
                }
            )
        )

    async def _relay_and_persist(self, content: str, *, emotion_score=None):
        message = await self._create_message(content, emotion_score=emotion_score)
        payload = self._message_payload(message)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "match.message",
                "message": payload,
            },
        )
        if hh_ai_assist_enabled():
            asyncio.create_task(
                self._run_message_analysis(message.id, content, emotion_score=emotion_score)
            )

    async def _run_message_analysis(self, message_id: int, content: str, *, emotion_score=None):
        from api.models import MatchMessage

        embedding = None
        try:
            embedding = await aget_embedding(content)
            await MatchMessage.objects.filter(id=message_id).aupdate(embedding=embedding)
        except Exception:
            logger.exception("Embedding failed for match message %s.", message_id)

        if emotion_score is not None:
            try:
                await MatchMessage.objects.filter(id=message_id).aupdate(
                    emotion_score=emotion_score
                )
            except Exception:
                logger.exception("Emotion score save failed for match message %s.", message_id)

        if embedding is None:
            return

        # Per-message, sender-bound analyses (run after this message's embedding is
        # persisted so the just-sent message is included). Stalemate stays throttled.
        await self._run_topic_check()
        await self._run_stance_drift()
        await self._maybe_run_stalemate_check()

    async def _run_topic_check(self):
        anchor = await self._ensure_topic_anchor_embedding()
        if anchor is None:
            return
        try:
            result = await acheck_match_topic_relevance(
                match_id=self.match.id,
                user_id=self.user.id,
                topic_anchor_embedding=anchor,
            )
        except Exception:
            logger.exception("Topic relevance failed for match %s.", self.match.id)
            return
        if result.get("is_off_topic"):
            await self._send_redirect_suggestion(trigger_score=result.get("relevance_score"))

    async def _run_stance_drift(self):
        """Recompute this speaker's own drift after each of their messages (mirrors
        the H-AI per-turn cadence) and push it to the speaker only. Drift is computed
        against the speaker's own Q9 baseline, so a message only affects its sender."""
        try:
            drift = await acalculate_match_stance_drift(
                match_id=self.match.id, user_id=self.user.id
            )
        except Exception:
            logger.exception("Stance drift failed for match %s.", self.match.id)
            return
        if not drift:
            return
        payload = {**drift, "measured_at": timezone.now().isoformat()}
        await self.send(
            json.dumps({"type": "match_stance_drift", "stance_drift": payload})
        )

    async def _maybe_run_stalemate_check(self):
        """Stalemate detection stays throttled (unlike drift): it compares both users'
        recent messages and fires a direction suggestion, so it must not run every
        message. At most once per _STALEMATE_MIN_INTERVAL_SECONDS per match."""
        match_id = self.match.id
        now = time.monotonic()
        last = _match_last_stalemate.get(match_id)
        if last is not None and (now - last) < _STALEMATE_MIN_INTERVAL_SECONDS:
            return
        _match_last_stalemate[match_id] = now
        try:
            stalemate = await adetect_match_stalemate(match_id=match_id)
            if stalemate.get("is_stalemate"):
                keywords = await aextract_match_opponent_keywords(
                    match_id=match_id,
                    user_id=self.user.id,
                )
                await self._send_direction_suggestion(
                    suggested_content=build_stalemate_prompt(keywords)
                )
        except Exception:
            logger.exception("Stalemate detection failed for match %s.", match_id)

    async def _ensure_topic_anchor_embedding(self):
        if self.match.topic_anchor_embedding is not None:
            return self.match.topic_anchor_embedding

        topic_description = TOPIC_CONFIGS.get(self.match.topic_id, {}).get(
            "topic_description",
            self._topic_label(),
        )
        try:
            anchor = await aget_topic_anchor_embedding(topic_description)
        except Exception:
            logger.exception("Topic anchor embedding failed for match %s.", self.match.id)
            return None

        self.match.topic_anchor_embedding = anchor
        try:
            await self.match.asave(update_fields=["topic_anchor_embedding"])
        except Exception:
            logger.exception("Topic anchor save failed for match %s.", self.match.id)
        return anchor

    async def match_message(self, event):
        await self.send(
            json.dumps(
                {
                    "type": "match_message",
                    "message": event["message"],
                }
            )
        )

    async def _send_error(self, message: str):
        await self.send(json.dumps({"type": "error", "content": message}))

    async def _send_system_prompt(self, category: str, message: str):
        self.system_prompts_triggered += 1
        await self.send(
            json.dumps(
                {
                    "type": "match_system_prompt",
                    "category": category,
                    "message": message,
                }
            )
        )

    async def _get_active_match_for_user(self):
        from api.models import DialogueMatch

        return await (
            DialogueMatch.objects.select_related("user_a", "user_b")
            .filter(
                room_id=self.room_id,
                status=DialogueMatch.Status.ACTIVE,
            )
            .filter(Q(user_a_id=self.user.id) | Q(user_b_id=self.user.id))
            .afirst()
        )

    async def _refresh_current_match_for_activity(self) -> bool:
        from api.models import DialogueMatch
        from apps.matching.services.matcher import (
            close_match_if_idle,
            close_match_if_participant_absent,
            mark_match_participant_connected,
        )

        self.match = await database_sync_to_async(close_match_if_participant_absent)(
            match=self.match
        )
        if self.match.status == DialogueMatch.Status.ACTIVE:
            self.match = await database_sync_to_async(mark_match_participant_connected)(
                match=self.match,
                user_id=self.user.id,
            )
            self.match = await database_sync_to_async(close_match_if_idle)(
                match=self.match
            )
        return self.match.status == DialogueMatch.Status.ACTIVE

    async def _mark_current_user_disconnected(self):
        from apps.matching.services.matcher import mark_match_participant_disconnected

        try:
            self.match = await database_sync_to_async(
                mark_match_participant_disconnected
            )(
                match=self.match,
                user_id=self.user.id,
            )
        except Exception:
            logger.exception(
                "Failed to mark match participant disconnected room=%s user=%s.",
                self.room_id,
                self.user.id,
            )

    async def _create_message(self, content: str, *, emotion_score=None):
        from api.models import MatchMessage

        return await MatchMessage.objects.acreate(
            match_id=self.match.id,
            sender=self.user,
            content=content,
            emotion_score=emotion_score,
        )

    async def _get_user_suggestion(self, suggestion_id):
        from api.models import MatchAISuggestion

        if not suggestion_id:
            return None
        try:
            return await MatchAISuggestion.objects.aget(
                id=suggestion_id,
                match_id=self.match.id,
                user_id=self.user.id,
            )
        except MatchAISuggestion.DoesNotExist:
            return None

    async def _recent_message_ids(self, limit: int = 5) -> list[int]:
        from api.models import MatchMessage

        def _load_ids():
            return list(
                MatchMessage.objects.filter(match_id=self.match.id)
                .order_by("-created_at", "-id")
                .values_list("id", flat=True)[:limit]
            )

        ids = await database_sync_to_async(_load_ids)()
        return list(reversed(ids))

    def _topic_label(self) -> str:
        return TOPIC_CONFIGS.get(self.match.topic_id, {}).get(
            "title",
            f"議題 {self.match.topic_id}",
        )

    @staticmethod
    def _action_value(action_type: str) -> str | None:
        if action_type == "accept_suggestion":
            return "accept"
        if action_type == "modify_suggestion":
            return "modify"
        if action_type == "ignore_suggestion":
            return "ignore"
        return None

    @staticmethod
    def _suggestion_response_time_ms(suggestion) -> int:
        return int((timezone.now() - suggestion.created_at).total_seconds() * 1000)

    def _message_payload(self, message) -> dict:
        return {
            "id": message.id,
            "match_id": self.match.id,
            "room_id": self.room_id,
            "sender_id": self.user.id,
            "sender_name": "匿名使用者",
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
