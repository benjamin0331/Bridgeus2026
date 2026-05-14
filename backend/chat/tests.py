import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import override_settings

from chat.models import Conversation

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)


async def _make_conversation(user_a, user_b, topic_id=1):
    return await Conversation.objects.acreate(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        session_number=1,
        status=Conversation.Status.ACTIVE,
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_message_relayed_to_peer():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="alice_hh1", password="x")
    bob = await create_user(username="bob_hh1", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")

    connected_a, _ = await comm_a.connect()
    connected_b, _ = await comm_b.connect()
    assert connected_a, "alice failed to connect"
    assert connected_b, "bob failed to connect"

    await comm_a.send_json_to({"content": "hi from alice"})

    resp_a = await comm_a.receive_json_from(timeout=3)
    resp_b = await comm_b.receive_json_from(timeout=3)

    assert resp_a["sender_id"] == alice.id
    assert resp_a["content"] == "hi from alice"
    assert resp_a["conversation_id"] == conv.id
    assert resp_b["sender_id"] == alice.id
    assert resp_b["content"] == "hi from alice"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_message_relayed_reverse():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="alice_hh2", password="x")
    bob = await create_user(username="bob_hh2", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    comm_b = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={bob.id}")

    await comm_a.connect()
    await comm_b.connect()

    await comm_b.send_json_to({"content": "hello from bob"})

    resp_a = await comm_a.receive_json_from(timeout=3)
    resp_b = await comm_b.receive_json_from(timeout=3)

    assert resp_a["sender_id"] == bob.id
    assert resp_b["sender_id"] == bob.id
    assert resp_a["content"] == "hello from bob"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_message_persisted_to_db():
    from BridgeUs_Django.asgi import application
    from chat.models import Message

    alice = await create_user(username="alice_hh3", password="x")
    bob = await create_user(username="bob_hh3", password="x")
    conv = await _make_conversation(alice, bob)

    comm_a = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={alice.id}")
    await comm_a.connect()

    await comm_a.send_json_to({"content": "persisted message"})
    await comm_a.receive_json_from(timeout=3)  # wait for relay to complete

    # Give async DB write a moment to finish
    import asyncio
    await asyncio.sleep(0.05)

    count = await Message.objects.filter(conversation=conv, sender=alice).acount()
    assert count == 1

    msg = await Message.objects.aget(conversation=conv, sender=alice)
    assert msg.content == "persisted message"

    await comm_a.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_no_user_id_rejected():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="alice_hh4", password="x")
    bob = await create_user(username="bob_hh4", password="x")
    conv = await _make_conversation(alice, bob)

    comm = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/")
    connected, code = await comm.connect()
    assert not connected
    assert code == 4001


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_non_participant_rejected():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="alice_hh5", password="x")
    bob = await create_user(username="bob_hh5", password="x")
    charlie = await create_user(username="charlie_hh5", password="x")
    conv = await _make_conversation(alice, bob)

    comm = WebsocketCommunicator(application, f"/ws/hh/{conv.id}/?user_id={charlie.id}")
    connected, code = await comm.connect()
    assert not connected
    assert code == 4003
