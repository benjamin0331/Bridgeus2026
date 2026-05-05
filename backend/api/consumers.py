import json
import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q

User = get_user_model()
SESSION_TTL_SECONDS = 60 * 60 * 12
logger = logging.getLogger(__name__)


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

        session_record = await sync_to_async(cache.get)(
            _session_cache_key(self.session_id)
        )
        if not session_record or session_record.get("user_id") != self.user.id:
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
            await self._send_error("找不到對話 session，請重新建立對話。")
            return

        if session_record.get("user_id") != self.user.id:
            await self._send_error("你沒有存取這個對話 session 的權限。")
            return

        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(user_message)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)
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
        await sync_to_async(cache.set)(
            cache_key,
            session_record,
            timeout=SESSION_TTL_SECONDS,
        )
        await self._update_ai_conversation(
            saved_turn=saved_turn,
            ai_response=full_response,
            dialogue_phase=session.dialogue_phase.value,
        )

        await self.send(json.dumps({"type": "agent_stream_end"}))

    async def _create_ai_conversation(
        self,
        *,
        session_record: dict,
        user_message: str,
        dialogue_phase: str,
    ):
        from api.models import AIConversation

        try:
            return await AIConversation.objects.acreate(
                user=self.user,
                session_id=self.session_id,
                topic_id=session_record.get("topic_id"),
                user_prompt=user_message,
                dialogue_phase=dialogue_phase,
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

        self.room_group_name = f"match_room_{self.room_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
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
        if message_type not in {None, "match_message", "user_message"}:
            return

        content = data.get("content", "").strip()
        if not content:
            return

        if not await self._close_current_match_if_idle():
            await self._send_error("聊天室已超過 10 分鐘沒有對話，已自動結束。")
            await self.close(code=4000)
            return

        message = await self._create_message(content)
        payload = self._message_payload(message)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "match.message",
                "message": payload,
            },
        )

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

    async def _close_current_match_if_idle(self) -> bool:
        from api.models import DialogueMatch
        from apps.matching.services.matcher import close_match_if_idle

        self.match = await database_sync_to_async(close_match_if_idle)(
            match=self.match
        )
        return self.match.status == DialogueMatch.Status.ACTIVE

    async def _create_message(self, content: str):
        from api.models import MatchMessage

        return await MatchMessage.objects.acreate(
            match_id=self.match.id,
            sender=self.user,
            content=content,
        )

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
