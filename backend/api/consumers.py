import json
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core.cache import cache

User = get_user_model()
SESSION_TTL_SECONDS = 60 * 60 * 12


def _session_cache_key(session_id: str) -> str:
    return f"dialogue_session:{session_id}"


class DialogueStreamConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]

        query = parse_qs(self.scope["query_string"].decode())
        token = query.get("token", [None])[0]
        if not token:
            await self.close(code=4001)
            return

        self.user = await self._authenticate(token)
        if not self.user:
            await self.close(code=4001)
            return

        session_record = await cache.aget(_session_cache_key(self.session_id))
        if not session_record or session_record["user_id"] != self.user.id:
            await self.close(code=4004)
            return

        await self.accept()

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data):
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

    @staticmethod
    async def _authenticate(token: str):
        try:
            from rest_framework_simplejwt.tokens import AccessToken
            access_token = await sync_to_async(AccessToken)(token)
            user_id = access_token["user_id"]
            return await User.objects.aget(id=user_id)
        except Exception:
            return None

    async def _stream_response(self, user_message: str):
        from apps.matching.services.ai_agent import DialoguePhase, DialogueSession
        from api.views import get_dialogue_agent

        cache_key = _session_cache_key(self.session_id)
        session_record = await cache.aget(cache_key)

        if not session_record:
            await self.send(json.dumps({
                "type": "error",
                "content": "找不到對話 session，請重新建立對話。",
            }))
            return

        session = DialogueSession.from_dict(session_record["session"])
        session.add_user_message(user_message)
        session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

        agent = get_dialogue_agent(session_record["collection_name"])

        full_response = ""
        try:
            async for chunk in agent.astream_respond(session):
                full_response += chunk
                await self.send(json.dumps({
                    "type": "agent_stream",
                    "content": chunk,
                }))
        except Exception:
            await self.send(json.dumps({
                "type": "error",
                "content": "AI 回應中斷，請重試。",
            }))
            return

        session.add_agent_message(full_response)
        session_record["session"] = session.to_dict()
        await cache.aset(cache_key, session_record, timeout=SESSION_TTL_SECONDS)

        await self.send(json.dumps({"type": "agent_stream_end"}))
