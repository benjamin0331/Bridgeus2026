import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model

from chat.services.ai_assist import arephrase_message
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

_SESSION_TIMEOUT_SECONDS = 3600  # 60 min hard cap


class HumanHumanConsumer(AsyncWebsocketConsumer):
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

        # Cumulative-trigger counters
        self.char_count = 0
        self.last_analysis_time = datetime.now(timezone.utc)

        # Session-end state
        self._session_ended = False
        self._pending_suggestion: dict | None = None
        self.blocked_count = 0
        self.system_prompts_triggered = 0

        self.room_group_name = f"hh_conv_{self.conversation_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        self._timer_task = asyncio.create_task(self._session_timer())

    async def disconnect(self, close_code):
        if hasattr(self, "_timer_task") and not self._timer_task.done():
            self._timer_task.cancel()
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        # Session control
        if msg_type == "end_session":
            await self._end_conversation("explicit")
            return

        # AI suggestion responses
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

        # Stage 2: emotion check — await result before deciding to relay
        try:
            emotion = await aget_analyze_emotion(content)
        except Exception as exc:
            logger.error("Emotion check error conv=%s: %s", self.conversation_id, exc)
            emotion = {"score": 0.0, "label": "neutral", "is_over_threshold": False}

        if emotion["is_over_threshold"]:
            await self._handle_emotion_overflow(content)
            return

        # Normal path: relay immediately, NLP in background
        await self._relay_and_persist(content, emotion_score=emotion["score"])

    # ------------------------------------------------------------------
    # Emotion overflow: rephrase + suggestion
    # ------------------------------------------------------------------

    async def _handle_emotion_overflow(self, content: str) -> None:
        topic = f"議題{self.conversation.topic_id}"
        try:
            suggested = await arephrase_message(content, topic)
        except Exception as exc:
            logger.error("Rephrase failed conv=%s: %s", self.conversation_id, exc)
            suggested = random.choice(EMOTION_WARNING_MESSAGES)

        from chat.models import AISuggestion

        suggestion_id = None
        try:
            suggestion = await AISuggestion.objects.acreate(
                conversation_id=self.conversation_id,
                user=self.user,
                category=AISuggestion.Category.REPHRASE,
                original_content=content,
                suggested_content=suggested,
            )
            suggestion_id = suggestion.id
            self._pending_suggestion = {
                "original_content": content,
                "suggestion_id": suggestion_id,
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
                "actions": ["accept", "modify", "ignore"],
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

        if action_type == "accept_suggestion":
            relay_content = (data.get("content") or "").strip() or pending["original_content"]
            user_action = "accept"
            modified_content = None
        elif action_type == "modify_suggestion":
            relay_content = (data.get("content") or "").strip() or pending["original_content"]
            user_action = "modify"
            modified_content = relay_content
        else:  # ignore_suggestion
            relay_content = pending["original_content"]
            user_action = "ignore"
            modified_content = None

        from chat.models import AISuggestion

        try:
            await AISuggestion.objects.filter(id=pending["suggestion_id"]).aupdate(
                user_action=user_action,
                modified_content=modified_content,
            )
        except Exception as exc:
            logger.error("AISuggestion update failed conv=%s: %s", self.conversation_id, exc)

        await self._relay_and_persist(relay_content)

    # ------------------------------------------------------------------
    # Relay + persist (shared by normal path and suggestion responses)
    # ------------------------------------------------------------------

    async def _relay_and_persist(self, content: str, emotion_score: float | None = None) -> None:
        now = datetime.now(timezone.utc)
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

        self.char_count += len(content)
        elapsed = (datetime.now(timezone.utc) - self.last_analysis_time).total_seconds()
        if self.char_count >= 200 or elapsed >= 900:
            self.char_count = 0
            self.last_analysis_time = datetime.now(timezone.utc)
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
                    self.system_prompts_triggered += 1
                    await self.send(
                        json.dumps({
                            "type": "system_prompt",
                            "category": "off_topic",
                            "message": random.choice(OFF_TOPIC_MESSAGES),
                        })
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
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _session_timer(self) -> None:
        """Hard 60-minute session cap."""
        await asyncio.sleep(_SESSION_TIMEOUT_SECONDS)
        await self._end_conversation("timeout")

    async def _end_conversation(self, reason: str = "explicit") -> None:
        if self._session_ended:
            return
        self._session_ended = True

        if hasattr(self, "_timer_task") and not self._timer_task.done():
            self._timer_task.cancel()

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
