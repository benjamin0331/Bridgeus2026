import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


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

        self.room_group_name = f"hh_conv_{self.conversation_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        content = data.get("content", "").strip()
        if not content:
            return

        now = datetime.now(timezone.utc)

        # Relay first — DB write must not delay delivery to peer
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

        try:
            await Message.objects.acreate(
                conversation_id=self.conversation_id,
                sender=self.user,
                content=content,
            )
        except Exception as exc:
            logger.error("Failed to persist message conv=%s: %s", self.conversation_id, exc)

    async def chat_message(self, event):
        await self.send(
            json.dumps(
                {
                    "sender_id": event["sender_id"],
                    "content": event["content"],
                    "timestamp": event["timestamp"],
                    "conversation_id": event["conversation_id"],
                }
            )
        )
