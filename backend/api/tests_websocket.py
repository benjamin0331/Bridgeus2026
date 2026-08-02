import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework_simplejwt.tokens import AccessToken

from api.models import AIConversation, DialogueMatch, MatchMessage

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)


def make_test_embedding(first_value):
    return [float(first_value), *([0.0] * 383)]


@pytest.fixture(autouse=True)
def disable_hh_ai_assist_env(monkeypatch):
    monkeypatch.setenv("H_H_AI_ASSIST_ENABLED", "false")


class FakeStreamingDialogueAgent:
    """Contract-compliant fake, in the real stream shape.

    The model rejects assistant prefill, so the stream carries the full
    "<judgment>…</judgment>" tag pair (see ai_agent.astream_respond).
    """

    async def astream_respond(self, session, correction: str = ""):
        yield (
            "<judgment>開啟新方向。結構 A。使用者提出新的事實問題。</judgment>"
            f"<reply>AI reply to: {session.history[-1].content}</reply>"
        )


class LeakyStreamingDialogueAgent:
    """判定段 + 正文的真實輸出形狀。

    `with_open_tag=True`  → 現況（無 prefill，串流含 <judgment> 開標籤）
    `with_open_tag=False` → 若未來模型支援 prefill 時的形狀（不含開標籤）
    兩種形狀都必須被 gate 正確攔截。
    """

    JUDGMENT = (
        "使用者在收斂到成本這個子問題。判定為「收斂」,"
        "強制使用承接深化型(E)。"
    )
    REPLY = "核電的優勢是低碳穩定,不是便宜。"

    def __init__(self, chunk_size: int = 7, with_open_tag: bool = True):
        self.chunk_size = chunk_size
        self.with_open_tag = with_open_tag

    async def astream_respond(self, session, correction: str = ""):
        payload = (
            f"{'<judgment>' if self.with_open_tag else ''}"
            f"{self.JUDGMENT}</judgment>"
            f"<reply>{self.REPLY}</reply>"
        )
        for i in range(0, len(payload), self.chunk_size):
            yield payload[i:i + self.chunk_size]


class MimickedTagDialogueAgent:
    """判定段內文自己寫出 <reply> 字面字串（第十節在 prompt 裡就有這個字串）。

    單段式 gate 會在這裡提前開閘，把後面的判定文字全部推給前端。
    """

    MIMICRY = "本輪 <reply> 只寫 3 句,結尾不拋問題。"
    REPLY = "核電的優勢是低碳穩定,不是便宜。"

    def __init__(self, chunk_size: int = 3):
        self.chunk_size = chunk_size

    async def astream_respond(self, session, correction: str = ""):
        payload = (
            f"<judgment>判定為「收斂」,強制使用承接深化型(E)。{self.MIMICRY}"
            f"</judgment><reply>{self.REPLY}</reply>"
        )
        for i in range(0, len(payload), self.chunk_size):
            yield payload[i:i + self.chunk_size]


class LeakInReplyDialogueAgent:
    """Tag boundary holds, but judgment language lands inside <reply>.

    First call leaks; the corrected retry (correction text appended to the user
    message) comes back clean. Records what it was called with so the test can
    prove the retry actually carried the correction.
    """

    LEAKY_REPLY = (
        "內部判斷：使用者這一輪是在開啟新方向——提供了一段具體的政策現況背景，"
        "等待我回應。 這個現況說明台灣的核電政策一直在政治可行性和現實需求之間拉扯。"
    )
    CLEAN_REPLY = (
        "核二、核三被評估為具再運轉可行性，並不等於安全無虞可以直接重啟，"
        "這只是技術評估的第一步。"
    )

    def __init__(self, chunk_size: int = 5):
        self.chunk_size = chunk_size
        self.call_count = 0
        self.corrections = []

    async def astream_respond(self, session, correction: str = ""):
        self.call_count += 1
        self.corrections.append(correction)
        body = self.CLEAN_REPLY if correction else self.LEAKY_REPLY
        payload = (
            "<judgment>開新方向|A|使用者貼入政策背景</judgment>"
            f"<reply>{body}</reply>"
        )
        for i in range(0, len(payload), self.chunk_size):
            yield payload[i:i + self.chunk_size]


class UnsalvageableLeakDialogueAgent:
    """Leaks on both attempts, and the body cannot be salvaged."""

    RAW_REPLY = "內部判斷：本輪強制使用承接深化型(E)。判定為「收斂」。"

    def __init__(self):
        self.call_count = 0

    async def astream_respond(self, session, correction: str = ""):
        self.call_count += 1
        yield (
            "<judgment>收斂|E|測試</judgment>"
            f"<reply>{self.RAW_REPLY}</reply>"
        )


class SalvageableLeakDialogueAgent:
    """Leaks on both attempts, but the body after the first sentence is clean."""

    LEAKY_REPLY = (
        "內部判斷：使用者這一輪是在開啟新方向。"
        "核二、核三被評估為具再運轉可行性，並不等於安全無虞可以直接重啟，"
        "後面還有自主安全檢查與再運轉計畫審查，核廢料最終處置也仍無著落。"
    )
    SALVAGED = (
        "核二、核三被評估為具再運轉可行性，並不等於安全無虞可以直接重啟，"
        "後面還有自主安全檢查與再運轉計畫審查，核廢料最終處置也仍無著落。"
    )

    def __init__(self):
        self.call_count = 0

    async def astream_respond(self, session, correction: str = ""):
        self.call_count += 1
        yield (
            "<judgment>開新方向|A|測試</judgment>"
            f"<reply>{self.LEAKY_REPLY}</reply>"
        )


class ContractViolatingDialogueAgent:
    """Never emits <reply> — the gate must fail closed and leak nothing."""

    RAW = "判定為「收斂」,強制使用承接深化型(E)。核電的優勢是低碳穩定,不是便宜。"

    def __init__(self, chunk_size: int = 5):
        self.chunk_size = chunk_size
        self.call_count = 0

    async def astream_respond(self, session, correction: str = ""):
        self.call_count += 1
        for i in range(0, len(self.RAW), self.chunk_size):
            yield self.RAW[i:i + self.chunk_size]


FORBIDDEN_IN_STREAM = [
    "判定為",
    "承接深化型",
    "<judgment>",
    "</judgment>",
    "<reply>",
    "</reply>",
]


async def _setup_ai_session(user, session_id):
    """Seed the cache with an active H-AI dialogue session record."""
    from apps.matching.services.ai_agent import DialogueSession

    session = DialogueSession(
        topic="台灣核能議題討論",
        topic_description="討論台灣是否應使用核能。",
        agent_stance="較反對核電",
        agent_stance_summary="以反方角度提出核安與核廢料疑慮。",
        user_stance_label="較支持核電",
        user_stance_score=6.5,
    )
    await sync_to_async(cache.set)(
        f"dialogue_session:{session_id}",
        {
            "user_id": user.id,
            "session_id": session_id,
            "topic_id": 102,
            "topic_title": "台灣核能議題討論",
            "collection_name": "nuclear_energy_all",
            "survey_context": {"q9_embedding": make_test_embedding(1)},
            "session": session.to_dict(),
        },
    )


async def _drain_agent_stream(communicator, timeout=3):
    """Collect every agent_stream chunk up to agent_stream_end.

    Returns (chunks, end_message, saw_thinking).
    """
    chunks = []
    saw_thinking = False
    while True:
        message = await communicator.receive_json_from(timeout=timeout)
        if message["type"] == "agent_thinking":
            saw_thinking = True
            continue
        if message["type"] == "agent_stream":
            chunks.append(message["content"])
            continue
        return chunks, message, saw_thinking


async def _access_token_for(user):
    return await sync_to_async(lambda: str(AccessToken.for_user(user)))()


async def _make_active_match(alice, bob):
    return await DialogueMatch.objects.acreate(
        topic_id=102,
        user_a=alice,
        user_b=bob,
        user_a_score=6.5,
        user_b_score=2.0,
        room_id=uuid4().hex,
        status=DialogueMatch.Status.ACTIVE,
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_dialogue_websocket_persists_completed_turn():
    from BridgeUs_Django.asgi import application
    from apps.matching.services.ai_agent import DialogueSession

    user = await create_user(username="ai_ws_user", password="secret123")
    session_id = uuid4().hex
    session = DialogueSession(
        topic="台灣核能議題討論",
        topic_description="討論台灣是否應使用核能。",
        agent_stance="較反對核電",
        agent_stance_summary="以反方角度提出核安與核廢料疑慮。",
        user_stance_label="較支持核電",
        user_stance_score=6.5,
    )
    await sync_to_async(cache.set)(
        f"dialogue_session:{session_id}",
        {
            "user_id": user.id,
            "session_id": session_id,
            "topic_id": 102,
            "topic_title": "台灣核能議題討論",
            "collection_name": "nuclear_energy_all",
            "survey_context": {"q9_embedding": make_test_embedding(1)},
            "session": session.to_dict(),
        },
    )

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch("api.views.get_dialogue_agent", return_value=FakeStreamingDialogueAgent()),
        patch("api.consumers.aget_embedding", new=AsyncMock(return_value=make_test_embedding(-1))),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "user_message", "content": "核能真的比較穩定嗎？"}
        )
        thinking_message = await communicator.receive_json_from(timeout=3)
        stream_message = await communicator.receive_json_from(timeout=3)
        end_message = await communicator.receive_json_from(timeout=3)

    # The reply is held until it passes validation, so the client is told to
    # show a typing indicator first.
    assert thinking_message == {"type": "agent_thinking"}
    assert stream_message == {
        "type": "agent_stream",
        "content": "AI reply to: 核能真的比較穩定嗎？",
    }
    assert end_message["type"] == "agent_stream_end"
    assert end_message["stance_drift"]["drift_value"] == 2.0
    assert end_message["stance_drift"]["measured_at"]

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.user_id == user.id
    assert saved_turn.topic_id == 102
    assert saved_turn.user_prompt == "核能真的比較穩定嗎？"
    assert saved_turn.ai_response == "AI reply to: 核能真的比較穩定嗎？"
    assert saved_turn.contract_violated is False

    await communicator.disconnect()


# ═══════════════════════════════════════════════════════════
# Output contract: the <judgment> block must never reach the participant
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 13, 1000])
@pytest.mark.parametrize("with_open_tag", [True, False])
async def test_judgment_block_never_reaches_the_client(chunk_size, with_open_tag):
    """Tag-splitting stress test. chunk_size=1 is the worst case: every tag is
    split across as many chunks as it has characters. Both stream shapes (with
    and without the <judgment> open tag) must be gated identically."""
    from BridgeUs_Django.asgi import application

    user = await create_user(
        username=f"gate_user_{chunk_size}_{int(with_open_tag)}",
        password="secret123",
    )
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch(
            "api.views.get_dialogue_agent",
            return_value=LeakyStreamingDialogueAgent(
                chunk_size=chunk_size,
                with_open_tag=with_open_tag,
            ),
        ),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "user_message", "content": "所以優勢就是比較便宜嗎"}
        )
        chunks, end_message, saw_thinking = await _drain_agent_stream(communicator)

    assert end_message["type"] == "agent_stream_end"

    streamed = "".join(chunks)
    assert streamed == "核電的優勢是低碳穩定,不是便宜。"
    for token_text in FORBIDDEN_IN_STREAM:
        assert token_text not in streamed

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.ai_response == "核電的優勢是低碳穩定,不是便宜。"
    assert saved_turn.contract_violated is False

    # The judgment block is retained for research, but only in its own column.
    assert saved_turn.internal_judgment
    assert "判定為" in saved_turn.internal_judgment
    assert "<judgment>" not in saved_turn.internal_judgment
    assert "</judgment>" not in saved_turn.internal_judgment

    # History is what gets replayed into the next turn's system prompt: if the
    # judgment block lands here, the model learns it is normal to emit one.
    record = await sync_to_async(cache.get)(f"dialogue_session:{session_id}")
    last_agent_message = [
        message
        for message in record["session"]["history"]
        if message["role"] == "agent"
    ][-1]
    assert last_agent_message["content"] == "核電的優勢是低碳穩定,不是便宜。"
    for token_text in FORBIDDEN_IN_STREAM:
        assert token_text not in last_agent_message["content"]

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_reply_tag_mimicked_inside_judgment_does_not_leak():
    """End-to-end version of the two-stage gating case: the model writes the
    literal "<reply>" inside its judgment, which a single-stage gate would treat
    as the opening tag."""
    from BridgeUs_Django.asgi import application

    user = await create_user(username="gate_mimicry_user", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch(
            "api.views.get_dialogue_agent",
            return_value=MimickedTagDialogueAgent(),
        ),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "user_message", "content": "所以優勢就是比較便宜嗎"}
        )
        chunks, end_message, saw_thinking = await _drain_agent_stream(communicator)

    assert end_message["type"] == "agent_stream_end"

    streamed = "".join(chunks)
    assert streamed == MimickedTagDialogueAgent.REPLY
    assert MimickedTagDialogueAgent.MIMICRY not in streamed
    for token_text in FORBIDDEN_IN_STREAM + ["本輪", "只寫 3 句"]:
        assert token_text not in streamed

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.ai_response == MimickedTagDialogueAgent.REPLY
    assert saved_turn.contract_violated is False
    assert "本輪" in saved_turn.internal_judgment

    await communicator.disconnect()


async def _run_single_turn(username, agent, message="所以優勢就是比較便宜嗎", timeout=5):
    """Drive one H-AI turn end-to-end; return (chunks, end_message, saw_thinking,
    session_id, user)."""
    from BridgeUs_Django.asgi import application

    user = await create_user(username=username, password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch("api.views.get_dialogue_agent", return_value=agent),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected
        await communicator.send_json_to({"type": "user_message", "content": message})
        chunks, end_message, saw_thinking = await _drain_agent_stream(
            communicator, timeout=timeout
        )

    await communicator.disconnect()
    return chunks, end_message, saw_thinking, session_id, user


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_in_reply_leak_triggers_corrected_retry():
    """5d: judgment language inside <reply> → corrected retry → clean output."""
    agent = LeakInReplyDialogueAgent()
    chunks, end_message, saw_thinking, session_id, _ = await _run_single_turn(
        "gate_inreply_user",
        agent,
        message="台灣的核電政策現況是這樣的：核二、核三已被評估為具再運轉可行性……",
    )

    assert end_message["type"] == "agent_stream_end"
    assert saw_thinking is True

    # Exactly two calls: the leaky one and the corrected retry.
    assert agent.call_count == 2
    assert agent.corrections[0] == ""
    assert "違反格式契約" in agent.corrections[1]

    streamed = "".join(chunks)
    assert streamed == LeakInReplyDialogueAgent.CLEAN_REPLY
    # The first attempt must never have reached the client.
    assert "內部判斷" not in streamed
    assert "使用者這一輪" not in streamed
    assert "開啟新方向" not in streamed

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.ai_response == LeakInReplyDialogueAgent.CLEAN_REPLY
    # A clean retry is a satisfied contract, not a violation.
    assert saved_turn.contract_violated is False

    record = await sync_to_async(cache.get)(f"dialogue_session:{session_id}")
    last_agent_message = [
        message
        for message in record["session"]["history"]
        if message["role"] == "agent"
    ][-1]
    assert last_agent_message["content"] == LeakInReplyDialogueAgent.CLEAN_REPLY


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_persistent_in_reply_leak_is_salvaged():
    """Both attempts leak, but the body after the judgment sentence is usable:
    show the salvaged text, still flag the turn as violated."""
    agent = SalvageableLeakDialogueAgent()
    chunks, _, _, session_id, _ = await _run_single_turn(
        "gate_salvage_user", agent
    )

    assert agent.call_count == 2      # no third API call
    streamed = "".join(chunks)
    assert streamed == SalvageableLeakDialogueAgent.SALVAGED
    assert "內部判斷" not in streamed
    assert "開啟新方向" not in streamed

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.ai_response == SalvageableLeakDialogueAgent.SALVAGED
    # Salvaged output is still the product of a violation — research must see it.
    assert saved_turn.contract_violated is True
    assert "內部判斷" in saved_turn.internal_judgment


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_unsalvageable_in_reply_leak_falls_back():
    from api.consumers import CONTRACT_FALLBACK_TEXT

    agent = UnsalvageableLeakDialogueAgent()
    chunks, _, _, session_id, _ = await _run_single_turn(
        "gate_unsalvageable_user", agent
    )

    assert agent.call_count == 2
    assert chunks == [CONTRACT_FALLBACK_TEXT]
    assert UnsalvageableLeakDialogueAgent.RAW_REPLY not in "".join(chunks)

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.ai_response == CONTRACT_FALLBACK_TEXT
    assert saved_turn.contract_violated is True
    assert "內部判斷" in saved_turn.internal_judgment


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_contract_violation_fails_closed():
    """When <reply> never opens, the participant sees a fallback line and the
    raw output is never streamed, stored as ai_response, or replayed."""
    from BridgeUs_Django.asgi import application
    from api.consumers import CONTRACT_FALLBACK_TEXT

    user = await create_user(username="gate_violation_user", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    agent = ContractViolatingDialogueAgent()
    token = await _access_token_for(user)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )

    with (
        patch("api.views.get_dialogue_agent", return_value=agent),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "user_message", "content": "所以優勢就是比較便宜嗎"}
        )
        chunks, end_message, saw_thinking = await _drain_agent_stream(communicator, timeout=5)

    assert end_message["type"] == "agent_stream_end"

    # The whole call is retried once before giving up.
    assert agent.call_count == 2

    assert chunks == [CONTRACT_FALLBACK_TEXT]
    streamed = "".join(chunks)
    assert ContractViolatingDialogueAgent.RAW not in streamed
    for token_text in ["判定為", "承接深化型"]:
        assert token_text not in streamed

    saved_turn = await AIConversation.objects.aget(session_id=session_id)
    assert saved_turn.contract_violated is True
    assert saved_turn.ai_response == CONTRACT_FALLBACK_TEXT
    assert "判定為" not in saved_turn.ai_response

    record = await sync_to_async(cache.get)(f"dialogue_session:{session_id}")
    last_agent_message = [
        message
        for message in record["session"]["history"]
        if message["role"] == "agent"
    ][-1]
    assert last_agent_message["content"] == CONTRACT_FALLBACK_TEXT

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_websocket_persists_and_broadcasts_message():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="match_ws_alice", password="secret123")
    bob = await create_user(username="match_ws_bob", password="secret123")
    match = await DialogueMatch.objects.acreate(
        topic_id=102,
        user_a=alice,
        user_b=bob,
        user_a_score=6.5,
        user_b_score=2.0,
        room_id=uuid4().hex,
        status=DialogueMatch.Status.ACTIVE,
    )

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={alice_token}",
    )
    comm_b = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={bob_token}",
    )

    connected_a, _ = await comm_a.connect()
    connected_b, _ = await comm_b.connect()
    assert connected_a
    assert connected_b

    await comm_a.send_json_to({"type": "match_message", "content": "我想先談核安。"})
    response_a = await comm_a.receive_json_from(timeout=3)
    response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a["type"] == "match_message"
    assert response_a == response_b
    payload = response_a["message"]
    assert payload["room_id"] == match.room_id
    assert payload["sender_id"] == alice.id
    assert payload["sender_name"] == "匿名使用者"
    assert payload["content"] == "我想先談核安。"

    saved_message = await MatchMessage.objects.aget(id=payload["id"])
    assert saved_message.match_id == match.id
    assert saved_message.sender_id == alice.id
    assert saved_message.content == "我想先談核安。"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_blocks_blacklisted_message():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="assist_block_alice", password="secret123")
    bob = await create_user(username="assist_block_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={alice_token}",
    )
    comm_b = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={bob_token}",
    )
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    with patch("api.consumers.hh_ai_assist_enabled", return_value=True):
        await comm_a.send_json_to({"type": "match_message", "content": "你這個白痴"})
        prompt = await comm_a.receive_json_from(timeout=3)

    assert prompt["type"] == "match_system_prompt"
    assert prompt["category"] == "content_blocked"
    assert "message" in prompt
    assert await comm_b.receive_nothing(timeout=1)
    assert await MatchMessage.objects.filter(match=match).acount() == 0

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_sends_rephrase_suggestion_without_relay():
    from BridgeUs_Django.asgi import application
    from api.models import MatchAISuggestion

    alice = await create_user(username="assist_rephrase_alice", password="secret123")
    bob = await create_user(username="assist_rephrase_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={alice_token}",
    )
    comm_b = WebsocketCommunicator(
        application,
        f"/ws/matching/rooms/{match.room_id}/?token={bob_token}",
    )
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.91, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("請用更平和的語氣表達。", True))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂核電"})
        suggestion = await comm_a.receive_json_from(timeout=3)

    assert suggestion["type"] == "match_ai_suggestion"
    assert suggestion["category"] == "rephrase"
    assert suggestion["original_content"] == "你完全不懂核電"
    assert suggestion["suggested_content"] == "請用更平和的語氣表達。"
    assert suggestion["actions"] == ["accept", "modify", "ignore"]
    assert await comm_b.receive_nothing(timeout=1)
    assert await MatchMessage.objects.filter(match=match).acount() == 0
    saved = await MatchAISuggestion.objects.aget(id=suggestion["suggestion_id"])
    assert saved.trigger_score == 0.91
    assert saved.user_action is None

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_high_emotion_without_second_person_is_not_intercepted():
    """High-arousal factual venting (no 你/您/妳) should relay, not trigger rephrase."""
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="vent_alice", password="secret123")
    bob = await create_user(username="vent_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)):
        await comm_a.send_json_to({"type": "match_message", "content": "核電根本就是一場騙局"})
        relayed = await comm_a.receive_json_from(timeout=3)

    # No rephrase suggestion — the message is broadcast as-is to both participants.
    assert relayed["type"] == "match_message"
    assert relayed["message"]["content"] == "核電根本就是一場騙局"
    assert (await comm_b.receive_json_from(timeout=3))["message"]["content"] == "核電根本就是一場騙局"
    assert await MatchMessage.objects.filter(match=match).acount() == 1

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_accepts_suggestion_and_relays_suggested_content():
    from BridgeUs_Django.asgi import application
    from api.models import MatchAISuggestion

    alice = await create_user(username="assist_accept_alice", password="secret123")
    bob = await create_user(username="assist_accept_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("我不同意你的核電看法，但想理解你的理由。", True))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂"})
        suggestion = await comm_a.receive_json_from(timeout=3)
        await comm_a.send_json_to({
            "type": "accept_suggestion",
            "suggestion_id": suggestion["suggestion_id"],
        })
        response_a = await comm_a.receive_json_from(timeout=3)
        response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a == response_b
    assert response_a["type"] == "match_message"
    assert response_a["message"]["content"] == "我不同意你的核電看法，但想理解你的理由。"
    saved = await MatchAISuggestion.objects.aget(id=suggestion["suggestion_id"])
    assert saved.user_action == MatchAISuggestion.Action.ACCEPT
    assert saved.final_content == "我不同意你的核電看法，但想理解你的理由。"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_modifies_suggestion_after_second_emotion_check():
    from BridgeUs_Django.asgi import application
    from api.models import MatchAISuggestion

    alice = await create_user(username="assist_modify_alice", password="secret123")
    bob = await create_user(username="assist_modify_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    low_emotion = {"score": 0.10, "label": "neutral", "is_over_threshold": False}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(side_effect=[high_emotion, low_emotion])), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("請改成比較平和的說法。", True))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂"})
        suggestion = await comm_a.receive_json_from(timeout=3)
        await comm_a.send_json_to({
            "type": "modify_suggestion",
            "suggestion_id": suggestion["suggestion_id"],
            "content": "我不同意，但想聽聽你的理由。",
        })
        response_a = await comm_a.receive_json_from(timeout=3)
        response_b = await comm_b.receive_json_from(timeout=3)

    assert response_a == response_b
    assert response_a["message"]["content"] == "我不同意，但想聽聽你的理由。"
    saved = await MatchAISuggestion.objects.aget(id=suggestion["suggestion_id"])
    assert saved.user_action == MatchAISuggestion.Action.MODIFY
    assert saved.modified_content == "我不同意，但想聽聽你的理由。"
    assert saved.final_content == "我不同意，但想聽聽你的理由。"

    await comm_a.disconnect()
    await comm_b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_relays_when_emotion_analysis_times_out():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="assist_timeout_alice", password="secret123")
    bob = await create_user(username="assist_timeout_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    async def slow_emotion(_content):
        await asyncio.sleep(1)
        return {"score": 0.99, "label": "negative", "is_over_threshold": True}

    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.ai_assist_timeout_seconds", return_value=0.01), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(side_effect=slow_emotion)), \
         patch("api.consumers.aget_embedding", new=AsyncMock(return_value=[0.0] * 384)):
        await comm_a.send_json_to({"type": "match_message", "content": "我想先談核能穩定供電。"})
        response_a = await comm_a.receive_json_from(timeout=0.5)
        response_b = await comm_b.receive_json_from(timeout=0.5)

    assert response_a == response_b
    assert response_a["type"] == "match_message"
    assert response_a["message"]["content"] == "我想先談核能穩定供電。"
    saved_message = await MatchMessage.objects.aget(id=response_a["message"]["id"])
    assert saved_message.emotion_score is None

    await comm_a.disconnect()
    await comm_b.disconnect()

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_ai_assist_rephrase_fallback_disallows_accept():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="assist_fallback_alice", password="secret123")
    bob = await create_user(username="assist_fallback_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    assert (await comm_a.connect())[0]

    high_emotion = {"score": 0.95, "label": "negative", "is_over_threshold": True}
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=high_emotion)), \
         patch("api.consumers.arephrase_match_message", new=AsyncMock(return_value=("你的發言可能帶有較強烈的情緒，建議修改後再發送。", False))):
        await comm_a.send_json_to({"type": "match_message", "content": "你完全不懂"})
        suggestion = await comm_a.receive_json_from(timeout=3)

    assert suggestion["type"] == "match_ai_suggestion"
    assert suggestion["actions"] == ["modify", "ignore"]

    await comm_a.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_pushes_sender_drift_after_each_message():
    """Each user message recomputes and pushes that speaker's own drift (mirrors the
    H-AI per-turn cadence, no 200-char throttle) and only to the sender."""
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="drift_alice", password="secret123")
    bob = await create_user(username="drift_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    comm_a = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}")
    comm_b = WebsocketCommunicator(application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}")
    assert (await comm_a.connect())[0]
    assert (await comm_b.connect())[0]

    low_emotion = {"score": 0.1, "label": "neutral", "is_over_threshold": False}
    drift_mock = AsyncMock(return_value={"drift_value": 0.42, "direction": "approaching"})
    with patch("api.consumers.hh_ai_assist_enabled", return_value=True), \
         patch("api.consumers.aget_analyze_emotion", new=AsyncMock(return_value=low_emotion)), \
         patch("api.consumers.aget_embedding", new=AsyncMock(return_value=make_test_embedding(1))), \
         patch("api.consumers.aget_topic_anchor_embedding", new=AsyncMock(return_value=make_test_embedding(0.5))), \
         patch("api.consumers.acheck_match_topic_relevance", new=AsyncMock(return_value={"is_off_topic": False, "relevance_score": 0.9})), \
         patch("api.consumers.adetect_match_stalemate", new=AsyncMock(return_value={"is_stalemate": False})), \
         patch("api.consumers.acalculate_match_stance_drift", new=drift_mock):
        # A short (<200 char) message must still trigger a drift recompute.
        await comm_a.send_json_to({"type": "match_message", "content": "短短一句話。"})

        msg_a = await comm_a.receive_json_from(timeout=3)
        msg_b = await comm_b.receive_json_from(timeout=3)
        assert msg_a["type"] == "match_message"
        assert msg_b["type"] == "match_message"

        drift_a = await comm_a.receive_json_from(timeout=3)
        assert drift_a["type"] == "match_stance_drift"
        assert drift_a["stance_drift"]["drift_value"] == 0.42
        assert drift_a["stance_drift"]["measured_at"]

        # The other user is not sent a drift push for the sender's message.
        assert await comm_b.receive_nothing(timeout=1)

    # Drift was recomputed for the sender only.
    drift_mock.assert_awaited_once_with(match_id=match.id, user_id=alice.id)

    await comm_a.disconnect()
    await comm_b.disconnect()
