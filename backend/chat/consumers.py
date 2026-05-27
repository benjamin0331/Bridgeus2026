import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model

from chat.services.ai_assist import aredirect_to_topic, arephrase_message, asuggest_direction
from chat.services.drift import acalculate_drift
from chat.services.embedding import aget_embedding
from chat.services.emotion import aget_analyze_emotion
from chat.services.filter import check_content_sync
from chat.services.session import aend_session, agenerate_summary
from chat.services.stalemate import (
    adetect_stalemate,
    aextract_opponent_keywords,
    build_stalemate_prompt,
)
from chat.services.topic import acheck_topic_relevance

logger = logging.getLogger(__name__)
User = get_user_model()

_SECOND_PERSON = {"你", "您", "妳"}


def _targets_other(text: str) -> bool:
    """Return True if the message contains a second-person pronoun."""
    return any(p in text for p in _SECOND_PERSON)


CONTENT_BLOCKED_MESSAGES = [
    "這則訊息包含可能冒犯對方的用語，請修改後重新發送。",
    "請避免使用攻擊性語言，試著以更平和的方式表達你的觀點。",
    "您的訊息含有不當用詞，請重新表達後再發送。",
]

EMOTION_WARNING_MESSAGES = [
    "請試著用更平和的方式表達你的觀點。",
    "深呼吸一下，試著聚焦在論點本身。",
    "注意保持對話的理性，你的觀點值得被認真對待。",
    "試著用事實和邏輯代替情緒來表達立場。",
]

OFF_TOPIC_MESSAGES = [
    "目前的討論似乎偏離了主題，可以試著回到核心議題的討論。",
    "這個方向有點遠離議題了，試著把焦點拉回來？",
    "回到主題聊聊吧，對方在等你對議題的看法。",
]

DIRECTION_FALLBACK_MESSAGES = [
    "可以試著從經濟面切入討論",
    "對方之前提到的觀點，你有什麼想法？",
    "試著換個角度思考這個議題",
]

# When LLM is unavailable for rephrase, send this fallback with reduced actions.
_REPHRASE_FALLBACK_TEMPLATE = "你的發言可能帶有較強烈的情緒，建議修改後再發送。"
_REPHRASE_MAX_INTERCEPTS = 3  # max times a single message chain can be intercepted

_SESSION_TIMEOUT_SECONDS = 3600   # 60-min hard cap
_INACTIVITY_SECONDS = 120         # 2-min mutual silence → suggest direction
_INACTIVITY_CHECK_INTERVAL = 30   # watcher poll interval (seconds)
_INACTIVITY_MAX_TRIGGERS = 3      # max direction suggestions per conversation
_INACTIVITY_MIN_INTERVAL_SECONDS = 300  # min gap between consecutive triggers
_INACTIVITY_GRACE_SECONDS = 180   # no trigger in first 3 minutes

# Shared per-conversation state (single-process only).
_conversation_last_active: dict[int, datetime] = {}
_conversation_connected_at: dict[int, datetime] = {}
_conversation_disconnected: set[int] = set()
_conversation_char_count: dict[int, int] = {}
_conversation_last_analysis: dict[int, datetime] = {}


class HumanHumanConsumer(AsyncWebsocketConsumer):

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        conv_id_str = self.scope["url_route"]["kwargs"]["conversation_id"]
        query = parse_qs(self.scope["query_string"].decode())
        user_id_str = query.get("user_id", [None])[0]

        try:
            self.conversation_id = int(conv_id_str)
            user_id = int(user_id_str)
        except (TypeError, ValueError):
            await self.close(code=4001)
            return

        try:
            self.user = await User.objects.aget(id=user_id)
        except User.DoesNotExist:
            await self.close(code=4001)
            return

        from chat.models import Conversation

        try:
            self.conversation = await Conversation.objects.aget(id=self.conversation_id)
        except Conversation.DoesNotExist:
            await self.close(code=4004)
            return

        if self.user.id not in (self.conversation.user_a_id, self.conversation.user_b_id):
            await self.close(code=4003)
            return

        anchor = self.conversation.topic_anchor_embedding
        self.topic_anchor = list(anchor) if anchor is not None else None

        # Per-instance counters (only drift is per-user; char/analysis tracking is conversation-level)
        self.char_count = 0  # kept for legacy reference, not used for triggering
        self.last_analysis_time = datetime.now(timezone.utc)  # fallback only

        # Session-end state
        self._session_ended = False
        self._pending_suggestion: dict | None = None
        self._rephrase_retry_count = 0
        self.blocked_count = 0
        self.system_prompts_triggered = 0

        self.room_group_name = f"hh_conv_{self.conversation_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        now = datetime.now(timezone.utc)
        _conversation_last_active[self.conversation_id] = now
        _conversation_connected_at[self.conversation_id] = now

        self._timer_task = asyncio.create_task(self._session_timer())
        self._inactivity_task: asyncio.Task | None = None
        if self.user.id == self.conversation.user_a_id:
            self._inactivity_task = asyncio.create_task(self._inactivity_watcher())

    async def disconnect(self, close_code):
        if hasattr(self, "_timer_task") and not self._timer_task.done():
            self._timer_task.cancel()
        if hasattr(self, "_inactivity_task") and self._inactivity_task and not self._inactivity_task.done():
            self._inactivity_task.cancel()
        if hasattr(self, "conversation_id"):
            _conversation_disconnected.add(self.conversation_id)
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "end_session":
            await self._end_conversation("explicit")
            return

        if msg_type in ("accept_suggestion", "modify_suggestion", "ignore_suggestion"):
            await self._handle_suggestion_response(data)
            return

        content = data.get("content", "").strip()
        if not content:
            return

        # Stage 1: sync content filter — fail-open
        try:
            filter_result = check_content_sync(content)
        except Exception as exc:
            logger.error("Content filter error conv=%s: %s", self.conversation_id, exc)
            filter_result = {"is_blocked": False}

        if filter_result["is_blocked"]:
            self.blocked_count += 1
            await self.send(
                json.dumps({
                    "type": "system_prompt",
                    "category": "content_blocked",
                    "message": random.choice(CONTENT_BLOCKED_MESSAGES),
                })
            )
            return

        # Each new content message resets the rephrase intercept counter.
        self._rephrase_retry_count = 0

        # Stage 2: emotion check
        try:
            emotion = await aget_analyze_emotion(content)
        except Exception as exc:
            logger.error("Emotion check error conv=%s: %s", self.conversation_id, exc)
            emotion = {"score": 0.0, "label": "neutral", "is_over_threshold": False}

        if emotion["is_over_threshold"] and _targets_other(content):
            await self._handle_emotion_overflow(content, trigger_score=emotion["score"])
            return

        await self._relay_and_persist(content, emotion_score=emotion["score"])

    # ------------------------------------------------------------------
    # Emotion overflow: rephrase + suggestion
    # ------------------------------------------------------------------

    async def _handle_emotion_overflow(self, content: str, trigger_score: float | None = None) -> None:
        self._rephrase_retry_count += 1
        topic = f"議題{self.conversation.topic_id}"
        is_llm_generated = True
        try:
            suggested = await arephrase_message(content, topic)
        except Exception as exc:
            logger.error("Rephrase failed conv=%s: %s", self.conversation_id, exc)
            suggested = _REPHRASE_FALLBACK_TEMPLATE
            is_llm_generated = False

        actions = ["accept", "modify", "ignore"] if is_llm_generated else ["modify", "ignore"]
        context_ids = await self._get_recent_message_ids()
        sent_at = datetime.now(timezone.utc)

        from chat.models import AISuggestion

        suggestion_id = None
        try:
            suggestion = await AISuggestion.objects.acreate(
                conversation_id=self.conversation_id,
                user=self.user,
                category=AISuggestion.Category.REPHRASE,
                original_content=content,
                suggested_content=suggested,
                trigger_score=trigger_score,
                context_message_ids=context_ids,
            )
            suggestion_id = suggestion.id
            self._pending_suggestion = {
                "category": "rephrase",
                "original_content": content,
                "suggestion_id": suggestion_id,
                "sent_at": sent_at,
            }
        except Exception as exc:
            logger.error("AISuggestion save failed conv=%s: %s", self.conversation_id, exc)

        self.system_prompts_triggered += 1
        await self.send(
            json.dumps({
                "type": "ai_suggestion",
                "category": "rephrase",
                "original_content": content,
                "suggested_content": suggested,
                "actions": actions,
            })
        )

    # ------------------------------------------------------------------
    # Suggestion response handler
    # ------------------------------------------------------------------

    async def _handle_suggestion_response(self, data: dict) -> None:
        if self._pending_suggestion is None:
            logger.warning(
                "Suggestion response with no pending suggestion conv=%s", self.conversation_id
            )
            return

        action_type = data.get("type")
        pending = self._pending_suggestion
        self._pending_suggestion = None
        category = pending.get("category", "rephrase")
        suggestion_id = pending.get("suggestion_id")

        now = datetime.now(timezone.utc)
        sent_at = pending.get("sent_at")
        response_time_ms: int | None = None
        if sent_at is not None:
            response_time_ms = int((now - sent_at).total_seconds() * 1000)

        if action_type == "accept_suggestion":
            user_action = "accept"
            modified_content = None
        elif action_type == "modify_suggestion":
            user_action = "modify"
            modified_content = (data.get("content") or "").strip() or None
        else:  # ignore_suggestion
            user_action = "ignore"
            modified_content = None

        from chat.models import AISuggestion

        # Rephrase + modify: update DB, then run second emotion check before deciding to relay.
        if category == "rephrase" and action_type == "modify_suggestion":
            if suggestion_id is not None:
                try:
                    await AISuggestion.objects.filter(id=suggestion_id).aupdate(
                        user_action=user_action,
                        modified_content=modified_content,
                        response_time_ms=response_time_ms,
                    )
                except Exception as exc:
                    logger.error("AISuggestion update failed conv=%s: %s", self.conversation_id, exc)
            await self._handle_modify_emotion_check(modified_content or "", suggestion_id)
            return

        # All other cases: determine final_content and update DB.
        if category == "rephrase":
            if action_type == "accept_suggestion":
                relay_content = (data.get("content") or "").strip() or pending["original_content"]
                final_content = relay_content
            else:  # ignore
                relay_content = pending["original_content"]
                final_content = relay_content
        else:
            # redirect / direction: no content relay
            relay_content = None
            final_content = None

        if suggestion_id is not None:
            try:
                await AISuggestion.objects.filter(id=suggestion_id).aupdate(
                    user_action=user_action,
                    modified_content=modified_content,
                    response_time_ms=response_time_ms,
                    final_content=final_content,
                )
            except Exception as exc:
                logger.error("AISuggestion update failed conv=%s: %s", self.conversation_id, exc)

        if category == "rephrase" and relay_content:
            await self._relay_and_persist(relay_content)

    # ------------------------------------------------------------------
    # Second emotion check after user edits (Patch 3)
    # ------------------------------------------------------------------

    async def _handle_modify_emotion_check(
        self, content: str, prev_suggestion_id: int | None = None
    ) -> None:
        try:
            emotion = await aget_analyze_emotion(content)
        except Exception as exc:
            logger.error("Emotion re-check failed conv=%s: %s", self.conversation_id, exc)
            emotion = {"score": 0.0, "label": "neutral", "is_over_threshold": False}

        if emotion["is_over_threshold"] and _targets_other(content) and self._rephrase_retry_count < _REPHRASE_MAX_INTERCEPTS:
            await self._handle_emotion_overflow(content, trigger_score=emotion["score"])
        else:
            # Force relay; update previous record with final_content.
            if prev_suggestion_id is not None:
                from chat.models import AISuggestion
                try:
                    await AISuggestion.objects.filter(id=prev_suggestion_id).aupdate(
                        final_content=content,
                    )
                except Exception as exc:
                    logger.error(
                        "AISuggestion final_content update failed conv=%s: %s",
                        self.conversation_id, exc,
                    )
            await self._relay_and_persist(content, emotion_score=emotion["score"])

    # ------------------------------------------------------------------
    # Relay + persist (shared by normal path and suggestion responses)
    # ------------------------------------------------------------------

    async def _relay_and_persist(self, content: str, emotion_score: float | None = None) -> None:
        now = datetime.now(timezone.utc)
        _conversation_last_active[self.conversation_id] = now
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.message",
                "sender_id": self.user.id,
                "content": content,
                "timestamp": now.isoformat(),
                "conversation_id": self.conversation_id,
            },
        )

        from chat.models import Message

        message = None
        try:
            message = await Message.objects.acreate(
                conversation_id=self.conversation_id,
                sender=self.user,
                content=content,
            )
        except Exception as exc:
            logger.error("Failed to persist message conv=%s: %s", self.conversation_id, exc)

        if message is not None:
            asyncio.create_task(self._run_nlp_analysis(message.id, content, emotion_score))

        conv_id = self.conversation_id
        _conversation_char_count[conv_id] = _conversation_char_count.get(conv_id, 0) + len(content)
        last_analysis = _conversation_last_analysis.get(conv_id)
        elapsed = (now - last_analysis).total_seconds() if last_analysis else 300
        if _conversation_char_count[conv_id] >= 200 or elapsed >= 300:
            _conversation_char_count[conv_id] = 0
            _conversation_last_analysis[conv_id] = now
            asyncio.create_task(self._run_periodic_analysis())

    # ------------------------------------------------------------------
    # Background NLP
    # ------------------------------------------------------------------

    async def _run_nlp_analysis(
        self, message_id: int, content: str, emotion_score: float | None = None
    ) -> None:
        from chat.models import Message

        try:
            embedding = await aget_embedding(content)
            await Message.objects.filter(id=message_id).aupdate(embedding=embedding)
        except Exception as exc:
            logger.error("Embedding failed msg=%s: %s", message_id, exc)

        if emotion_score is not None:
            try:
                await Message.objects.filter(id=message_id).aupdate(emotion_score=emotion_score)
            except Exception as exc:
                logger.error("Emotion save failed msg=%s: %s", message_id, exc)

        if self.topic_anchor is not None:
            try:
                topic = await acheck_topic_relevance(
                    self.conversation_id, self.user.id, self.topic_anchor
                )
                if topic["is_off_topic"]:
                    await self._handle_off_topic(
                        message_id, trigger_score=topic.get("relevance_score")
                    )
            except Exception as exc:
                logger.error("Topic check failed msg=%s: %s", message_id, exc)

    async def _run_periodic_analysis(self) -> None:
        try:
            await acalculate_drift(self.conversation_id, self.user.id)
        except Exception as exc:
            logger.error("Drift calculation failed conv=%s: %s", self.conversation_id, exc)

        try:
            stalemate = await adetect_stalemate(self.conversation_id)
            if stalemate["is_stalemate"]:
                keywords = await aextract_opponent_keywords(
                    self.conversation_id, self.user.id
                )
                self.system_prompts_triggered += 1
                await self.send(
                    json.dumps({
                        "type": "system_prompt",
                        "category": "stalemate_hint",
                        "message": build_stalemate_prompt(keywords),
                    })
                )
        except Exception as exc:
            logger.error("Stalemate check failed conv=%s: %s", self.conversation_id, exc)

    # ------------------------------------------------------------------
    # Off-topic: LLM redirect
    # ------------------------------------------------------------------

    async def _handle_off_topic(
        self, message_id: int, trigger_score: float | None = None
    ) -> None:
        topic_label = f"議題{self.conversation.topic_id}"
        try:
            redirect_text = await aredirect_to_topic(self.conversation_id, topic_label)
        except Exception as exc:
            logger.error("redirect_to_topic failed msg=%s: %s", message_id, exc)
            redirect_text = random.choice(OFF_TOPIC_MESSAGES)

        context_ids = await self._get_recent_message_ids()
        sent_at = datetime.now(timezone.utc)

        from chat.models import AISuggestion

        suggestion_id = None
        try:
            suggestion = await AISuggestion.objects.acreate(
                conversation_id=self.conversation_id,
                user=self.user,
                category=AISuggestion.Category.REDIRECT,
                original_content=None,
                suggested_content=redirect_text,
                trigger_score=trigger_score,
                context_message_ids=context_ids,
            )
            suggestion_id = suggestion.id
            self._pending_suggestion = {
                "category": "redirect",
                "original_content": None,
                "suggestion_id": suggestion_id,
                "sent_at": sent_at,
            }
        except Exception as exc:
            logger.error("AISuggestion save failed conv=%s: %s", self.conversation_id, exc)

        self.system_prompts_triggered += 1
        await self.send(
            json.dumps({
                "type": "ai_suggestion",
                "category": "redirect",
                "suggested_content": redirect_text,
                "actions": ["accept", "ignore"],
            })
        )

    # ------------------------------------------------------------------
    # Inactivity watcher: suggest_direction after mutual silence
    # ------------------------------------------------------------------

    async def _inactivity_watcher(self) -> None:
        trigger_count = 0
        last_trigger_at: datetime | None = None
        in_inactivity_window = False

        while True:
            await asyncio.sleep(_INACTIVITY_CHECK_INTERVAL)

            if self.conversation_id in _conversation_disconnected:
                return

            if trigger_count >= _INACTIVITY_MAX_TRIGGERS:
                return

            now = datetime.now(timezone.utc)

            # Grace period: suppress triggers in the first N seconds of the conversation.
            connected_at = _conversation_connected_at.get(self.conversation_id)
            if connected_at is not None:
                if (now - connected_at).total_seconds() < _INACTIVITY_GRACE_SECONDS:
                    continue

            # Min interval: don't re-trigger within N seconds of the last trigger.
            if last_trigger_at is not None:
                since_last = (now - last_trigger_at).total_seconds()
                if since_last < _INACTIVITY_MIN_INTERVAL_SECONDS:
                    # If there was activity since the last trigger, reset the inactivity window.
                    last_active = _conversation_last_active.get(self.conversation_id)
                    if last_active and last_active > last_trigger_at:
                        in_inactivity_window = False
                    continue

            last_active = _conversation_last_active.get(self.conversation_id)
            if last_active is None:
                continue

            elapsed = (now - last_active).total_seconds()
            if elapsed >= _INACTIVITY_SECONDS:
                if not in_inactivity_window:
                    in_inactivity_window = True
                    trigger_count += 1
                    last_trigger_at = now
                    asyncio.create_task(self._suggest_direction_bg())
            else:
                in_inactivity_window = False

    async def _suggest_direction_bg(self) -> None:
        topic_label = f"議題{self.conversation.topic_id}"
        try:
            direction_text = await asuggest_direction(self.conversation_id, topic_label)
        except Exception as exc:
            logger.error("suggest_direction failed conv=%s: %s", self.conversation_id, exc)
            direction_text = random.choice(DIRECTION_FALLBACK_MESSAGES)

        try:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "session.ai_suggestion",
                    "category": "direction",
                    "suggested_content": direction_text,
                },
            )
        except Exception as exc:
            logger.error(
                "group_send direction failed conv=%s: %s", self.conversation_id, exc
            )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _session_timer(self) -> None:
        await asyncio.sleep(_SESSION_TIMEOUT_SECONDS)
        await self._end_conversation("timeout")

    async def _end_conversation(self, reason: str = "explicit") -> None:
        if self._session_ended:
            return
        self._session_ended = True

        if hasattr(self, "_timer_task") and not self._timer_task.done():
            self._timer_task.cancel()
        if hasattr(self, "_inactivity_task") and self._inactivity_task and not self._inactivity_task.done():
            self._inactivity_task.cancel()

        stats: dict = {}
        try:
            stats = await aend_session(
                self.conversation_id,
                blocked_count=self.blocked_count,
                system_prompts_triggered=self.system_prompts_triggered,
            )
        except Exception as exc:
            logger.error("end_session failed conv=%s: %s", self.conversation_id, exc)

        try:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "session.ended",
                    "reason": reason,
                    "stats": stats,
                },
            )
        except Exception as exc:
            logger.error(
                "group_send session_ended failed conv=%s: %s", self.conversation_id, exc
            )

        asyncio.create_task(self._generate_summary_bg())

    async def _generate_summary_bg(self) -> None:
        try:
            await agenerate_summary(self.conversation_id)
        except Exception as exc:
            logger.error("Summary generation failed conv=%s: %s", self.conversation_id, exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_recent_message_ids(self, n: int = 5) -> list[int]:
        from chat.models import Message
        try:
            ids = []
            async for msg_id in (
                Message.objects.filter(conversation_id=self.conversation_id)
                .order_by("-timestamp")
                .values_list("id", flat=True)[:n]
            ):
                ids.append(msg_id)
            return list(reversed(ids))
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Channel layer handlers
    # ------------------------------------------------------------------

    async def chat_message(self, event):
        await self.send(
            json.dumps({
                "sender_id": event["sender_id"],
                "content": event["content"],
                "timestamp": event["timestamp"],
                "conversation_id": event["conversation_id"],
            })
        )

    async def session_ended(self, event):
        try:
            await self.send(
                json.dumps({
                    "type": "session_ended",
                    "reason": event["reason"],
                    "stats": event.get("stats", {}),
                })
            )
        except Exception:
            pass
        try:
            await self.close()
        except Exception:
            pass

    async def session_ai_suggestion(self, event):
        """Handle direction suggestions broadcast to both users."""
        from chat.models import AISuggestion

        context_ids = await self._get_recent_message_ids()
        sent_at = datetime.now(timezone.utc)
        suggestion_id = None
        try:
            suggestion = await AISuggestion.objects.acreate(
                conversation_id=self.conversation_id,
                user=self.user,
                category=AISuggestion.Category.DIRECTION,
                original_content=None,
                suggested_content=event["suggested_content"],
                context_message_ids=context_ids,
            )
            suggestion_id = suggestion.id
            self._pending_suggestion = {
                "category": "direction",
                "original_content": None,
                "suggestion_id": suggestion_id,
                "sent_at": sent_at,
            }
        except Exception as exc:
            logger.error(
                "AISuggestion direction save failed conv=%s user=%s: %s",
                self.conversation_id, self.user.id, exc,
            )

        try:
            await self.send(
                json.dumps({
                    "type": "ai_suggestion",
                    "category": event["category"],
                    "suggested_content": event["suggested_content"],
                    "actions": ["accept", "ignore"],
                })
            )
        except Exception:
            pass
