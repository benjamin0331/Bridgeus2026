"""Consumer-level tests for the input gate.

The unit tests in apps/matching/tests/test_input_gate.py prove the rules. These
prove the thing that actually costs money: a blocked message must not reach the
Claude API, must not create an AIConversation row, and must not grow the
persisted dialogue context. Every one of those is a separate assertion because
each is a separate code path that could regress independently.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from api.models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    MatchMessage,
)
from apps.matching.services.rate_limit import reset_rate_limit

User = get_user_model()

TEST_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

create_user = sync_to_async(User.objects.create_user, thread_sensitive=True)
areset_rate_limit = sync_to_async(reset_rate_limit, thread_sensitive=False)


def make_test_embedding(first_value):
    return [float(first_value), *([0.0] * 383)]


@pytest.fixture(autouse=True)
def clean_cache():
    """Cooldown and rate-limit state live in the cache and would leak between
    tests (LocMemCache is process-wide)."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def disable_hh_ai_assist_env(monkeypatch):
    monkeypatch.setenv("H_H_AI_ASSIST_ENABLED", "false")


class SpyDialogueAgent:
    """Counts every LLM call. The whole point of the gate is that this stays 0."""

    def __init__(self):
        self.call_count = 0

    async def astream_respond(self, session, correction: str = ""):
        self.call_count += 1
        yield (
            "<judgment>開新方向|A|測試</judgment>"
            f"<reply>AI reply to: {session.history[-1].content}</reply>"
        )


async def _access_token_for(user):
    return await sync_to_async(lambda: str(AccessToken.for_user(user)))()


async def _setup_ai_session(user, session_id, *, prev_ai_is_question=False):
    """Seed both the cache and the DB row the gate's counters live on."""
    from apps.matching.services.ai_agent import DialogueSession

    session = DialogueSession(
        topic="台灣核能議題討論",
        topic_description="討論台灣是否應使用核能。",
        agent_stance="較反對核電",
        agent_stance_summary="以反方角度提出核安與核廢料疑慮。",
        user_stance_label="較支持核電",
        user_stance_score=6.5,
    )
    session_record = {
        "user_id": user.id,
        "session_id": session_id,
        "topic_id": 102,
        "topic_title": "台灣核能議題討論",
        "collection_name": "nuclear_energy_all",
        "survey_context": {"q9_embedding": make_test_embedding(1)},
        "session": session.to_dict(),
    }
    await sync_to_async(cache.set)(f"dialogue_session:{session_id}", session_record)
    record = await DialogueSessionRecord.objects.acreate(
        user=user,
        session_id=session_id,
        topic_id=102,
        topic_title="台灣核能議題討論",
        collection_name="nuclear_energy_all",
        survey_context=session_record["survey_context"],
        session_state=session_record["session"],
        last_activity_at=timezone.now(),
    )
    if prev_ai_is_question:
        await AIConversation.objects.acreate(
            user=user,
            session_id=session_id,
            topic_id=102,
            user_prompt="核廢料要放哪裡？",
            ai_response="那你會把最終處置場設在哪一個縣市？",
            ai_turn_is_question=True,
        )
    return record


async def _connect(user, session_id):
    from BridgeUs_Django.asgi import application

    token = await _access_token_for(user)
    return WebsocketCommunicator(
        application,
        f"/ws/dialogue/{session_id}/?token={token}",
    )


# ═══════════════════════════════════════════════════════════
# 1. 無意義訊息不得觸發 LLM
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_meaningless_message_never_reaches_the_llm():
    user = await create_user(username="gate_user_1", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)

    with (
        patch("api.views.get_dialogue_agent", return_value=agent) as get_agent,
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ) as embed,
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"type": "user_message", "content": "6456"})
        message = await communicator.receive_json_from(timeout=3)

    assert message["type"] == "input_blocked"
    assert message["presentation"] == "bubble"
    assert message["reason"] == "non_linguistic"
    assert message["invalid_input_count"] == 1
    assert message["content"]

    # 零 API 成本：連 agent 都沒有被建立。
    assert agent.call_count == 0
    assert get_agent.call_count == 0
    # 也不觸發 Sentence-Transformers 向量化。
    assert embed.call_count == 0
    # 沒有 AIConversation 記錄 → CCND 概念節點擷取也不會撿到這則訊息，
    # 因為 analyze_pending_ai_conversations 是掃這張表的。
    assert await AIConversation.objects.filter(session_id=session_id).acount() == 0

    await communicator.disconnect()


# ═══════════════════════════════════════════════════════════
# 2. Context 隔離（最高優先）
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_blocked_message_does_not_grow_persisted_context():
    """這是 token 成本的根源：舊流程把垃圾寫進 session_state.history，
    之後每一輪 prompt 都重新攜帶它。"""
    user = await create_user(username="gate_user_2", password="secret123")
    session_id = uuid4().hex
    record = await _setup_ai_session(user, session_id)
    before = len(record.session_state.get("history", []))

    communicator = await _connect(user, session_id)
    with patch("api.views.get_dialogue_agent", return_value=SpyDialogueAgent()):
        connected, _ = await communicator.connect()
        assert connected

        for text in ("54", "asdasdasdasd", "。。。"):
            await areset_rate_limit(user.id)
            await communicator.send_json_to({"type": "user_message", "content": text})
            await communicator.receive_json_from(timeout=3)

    record = await DialogueSessionRecord.objects.aget(session_id=session_id)
    assert len(record.session_state.get("history", [])) == before
    # 只有計數欄位被更新。
    assert record.invalid_input_count == 3
    assert record.invalid_input_total == 3
    assert record.input_attempt_total == 3

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_valid_message_resets_the_invalid_counter():
    user = await create_user(username="gate_user_3", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    communicator = await _connect(user, session_id)
    with (
        patch("api.views.get_dialogue_agent", return_value=SpyDialogueAgent()),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"type": "user_message", "content": "54"})
        await communicator.receive_json_from(timeout=3)

        await areset_rate_limit(user.id)
        await communicator.send_json_to(
            {"type": "user_message", "content": "我覺得核廢料處理才是關鍵"}
        )
        # agent_thinking → agent_stream → agent_stream_end
        while True:
            message = await communicator.receive_json_from(timeout=3)
            if message["type"] == "agent_stream_end":
                break

    record = await DialogueSessionRecord.objects.aget(session_id=session_id)
    assert record.invalid_input_count == 0
    # 累計數不歸零——它是實驗資料。
    assert record.invalid_input_total == 1
    assert record.input_attempt_total == 2

    await communicator.disconnect()


# ═══════════════════════════════════════════════════════════
# 3. 遞進節流
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_throttle_escalates_to_cooldown_after_six_invalid_inputs():
    user = await create_user(username="gate_user_4", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    communicator = await _connect(user, session_id)
    presentations = []
    with patch("api.views.get_dialogue_agent", return_value=SpyDialogueAgent()):
        connected, _ = await communicator.connect()
        assert connected

        for _ in range(6):
            await areset_rate_limit(user.id)
            await communicator.send_json_to({"type": "user_message", "content": "6456"})
            message = await communicator.receive_json_from(timeout=3)
            presentations.append(message.get("presentation") or message["type"])

        last = message

    # 1–2 氣泡、3–5 系統提示列、第 6 則進冷卻。
    assert presentations == [
        "bubble",
        "bubble",
        "notice",
        "notice",
        "notice",
        "input_cooldown",
    ]
    assert last["type"] == "input_cooldown"
    assert last["seconds"] == 60
    assert last["invalid_input_count"] == 6

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_cooldown_blocks_even_a_valid_message():
    """冷卻期間連合法發言都不進 LLM，否則冷卻等於沒有。"""
    user = await create_user(username="gate_user_5", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)
    with patch("api.views.get_dialogue_agent", return_value=agent):
        connected, _ = await communicator.connect()
        assert connected

        for _ in range(6):
            await areset_rate_limit(user.id)
            await communicator.send_json_to({"type": "user_message", "content": "6456"})
            await communicator.receive_json_from(timeout=3)

        await areset_rate_limit(user.id)
        await communicator.send_json_to(
            {"type": "user_message", "content": "我認為核廢料處理才是關鍵問題"}
        )
        message = await communicator.receive_json_from(timeout=3)

    assert message["type"] == "input_cooldown"
    assert agent.call_count == 0

    await communicator.disconnect()


# ═══════════════════════════════════════════════════════════
# 4. prev_ai_is_question 來自策略層旗標
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_short_reply_passes_when_previous_ai_turn_asked_a_question():
    user = await create_user(username="gate_user_6", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id, prev_ai_is_question=True)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)
    with (
        patch("api.views.get_dialogue_agent", return_value=agent),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"type": "user_message", "content": "好"})
        while True:
            message = await communicator.receive_json_from(timeout=3)
            if message["type"] in {"agent_stream_end", "input_blocked"}:
                break

    assert message["type"] == "agent_stream_end"
    assert agent.call_count == 1

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_short_reply_blocked_without_question_context():
    user = await create_user(username="gate_user_7", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)
    with patch("api.views.get_dialogue_agent", return_value=agent):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"type": "user_message", "content": "好"})
        message = await communicator.receive_json_from(timeout=3)

    assert message["type"] == "input_blocked"
    assert message["reason"] == "low_information"
    assert agent.call_count == 0

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_standalone_profanity_blocked_even_after_an_ai_question():
    """「好」在 AI 提問後放行，「幹」不放行——規則 0 不看脈絡。"""
    user = await create_user(username="gate_user_10", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id, prev_ai_is_question=True)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)
    with patch("api.views.get_dialogue_agent", return_value=agent):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to({"type": "user_message", "content": "幹"})
        message = await communicator.receive_json_from(timeout=3)

    assert message["type"] == "input_blocked"
    assert message["reason"] == "profanity_only"
    assert agent.call_count == 0
    # 只有第一輪那筆 AI 回覆，這則「幹」沒有落庫。
    assert await AIConversation.objects.filter(session_id=session_id).acount() == 1

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_repeated_profanity_escalates_to_cooldown():
    """「幹×n」不是靠字數變長被擋，而是逐則累加後進冷卻。"""
    user = await create_user(username="gate_user_11", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id, prev_ai_is_question=True)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)
    types = []
    with patch("api.views.get_dialogue_agent", return_value=agent):
        connected, _ = await communicator.connect()
        assert connected

        for n in range(1, 7):
            await areset_rate_limit(user.id)
            await communicator.send_json_to(
                {"type": "user_message", "content": "幹" * n}
            )
            message = await communicator.receive_json_from(timeout=3)
            types.append(message["type"])

    assert types == ["input_blocked"] * 5 + ["input_cooldown"]
    assert agent.call_count == 0

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_does_not_apply_hai_profanity_gate():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="gate_alice3", password="secret123")
    bob = await create_user(username="gate_bob3", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    alice_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}"
    )
    bob_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}"
    )
    assert (await alice_ws.connect())[0]
    assert (await bob_ws.connect())[0]

    await alice_ws.send_json_to({"type": "match_message", "content": "幹幹幹"})
    received = await bob_ws.receive_json_from(timeout=3)

    assert received["type"] == "match_message"
    assert received["message"]["content"] == "幹幹幹"
    assert await MatchMessage.objects.filter(match_id=match.id).acount() == 1

    await alice_ws.disconnect()
    await bob_ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_agent_turn_question_flag_is_persisted_from_the_strategy_layer():
    user = await create_user(username="gate_user_8", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    class PerspectiveFlipAgent:
        async def astream_respond(self, session, correction: str = ""):
            # C 型 = 視角翻轉型；判定段就宣告了本輪以提問收尾。
            yield (
                "<judgment>開新方向|C|拋出視角翻轉</judgment>"
                "<reply>假設你是經濟部長，你會怎麼決定</reply>"
            )

    communicator = await _connect(user, session_id)
    with (
        patch("api.views.get_dialogue_agent", return_value=PerspectiveFlipAgent()),
        patch(
            "api.consumers.aget_embedding",
            new=AsyncMock(return_value=make_test_embedding(-1)),
        ),
    ):
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "user_message", "content": "核電成本其實沒有算進除役費用"}
        )
        while True:
            message = await communicator.receive_json_from(timeout=3)
            if message["type"] == "agent_stream_end":
                break

    turn = await AIConversation.objects.filter(session_id=session_id).afirst()
    # 回覆本身沒有問號，旗標仍為 True——這正是「不要事後用正則猜」的意思。
    assert turn.ai_turn_is_question is True

    await communicator.disconnect()


# ═══════════════════════════════════════════════════════════
# 5. Rate limit（獨立於內容判斷之外）
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_rate_limit_blocks_a_second_message_sent_too_fast():
    user = await create_user(username="gate_user_9", password="secret123")
    session_id = uuid4().hex
    await _setup_ai_session(user, session_id)

    agent = SpyDialogueAgent()
    communicator = await _connect(user, session_id)
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
            {"type": "user_message", "content": "核廢料的最終處置一直沒有進展"}
        )
        while True:
            message = await communicator.receive_json_from(timeout=3)
            if message["type"] == "agent_stream_end":
                break

        # No reset_rate_limit here — the two sends are back to back.
        await communicator.send_json_to(
            {"type": "user_message", "content": "而且地方政府也不願意接受"}
        )
        message = await communicator.receive_json_from(timeout=3)

    assert message["type"] == "rate_limited"
    assert message["reason"] == "too_fast"
    assert message["retry_after"] > 0
    assert agent.call_count == 1

    await communicator.disconnect()


# ═══════════════════════════════════════════════════════════
# 6. H-H 配對房
# ═══════════════════════════════════════════════════════════

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
async def test_match_room_low_information_message_is_relayed_to_the_partner():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="gate_alice", password="secret123")
    bob = await create_user(username="gate_bob", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    alice_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}"
    )
    bob_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}"
    )

    assert (await alice_ws.connect())[0]
    assert (await bob_ws.connect())[0]

    await alice_ws.send_json_to({"type": "match_message", "content": "6456"})
    received = await bob_ws.receive_json_from(timeout=3)

    assert received["type"] == "match_message"
    assert received["message"]["content"] == "6456"
    assert await MatchMessage.objects.filter(match_id=match.id).acount() == 1

    await alice_ws.disconnect()
    await bob_ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_valid_message_still_relays():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="gate_alice2", password="secret123")
    bob = await create_user(username="gate_bob2", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    alice_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}"
    )
    bob_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}"
    )
    assert (await alice_ws.connect())[0]
    assert (await bob_ws.connect())[0]

    await alice_ws.send_json_to(
        {"type": "match_message", "content": "核廢料最終處置場址一直選不出來"}
    )
    received = await bob_ws.receive_json_from(timeout=3)

    assert received["type"] == "match_message"
    assert received["message"]["content"] == "核廢料最終處置場址一直選不出來"

    await alice_ws.disconnect()
    await bob_ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
async def test_match_room_still_rate_limits_messages_sent_too_fast():
    from BridgeUs_Django.asgi import application

    alice = await create_user(username="gate_alice_rate", password="secret123")
    bob = await create_user(username="gate_bob_rate", password="secret123")
    match = await _make_active_match(alice, bob)

    alice_token = await _access_token_for(alice)
    bob_token = await _access_token_for(bob)
    alice_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={alice_token}"
    )
    bob_ws = WebsocketCommunicator(
        application, f"/ws/matching/rooms/{match.room_id}/?token={bob_token}"
    )
    assert (await alice_ws.connect())[0]
    assert (await bob_ws.connect())[0]

    await alice_ws.send_json_to({"type": "match_message", "content": "54"})
    assert (await alice_ws.receive_json_from(timeout=3))["type"] == "match_message"
    assert (await bob_ws.receive_json_from(timeout=3))["type"] == "match_message"

    await alice_ws.send_json_to({"type": "match_message", "content": "6456"})
    notice = await alice_ws.receive_json_from(timeout=3)

    assert notice["type"] == "rate_limited"
    assert await bob_ws.receive_nothing(timeout=0.5) is True
    assert await MatchMessage.objects.filter(match_id=match.id).acount() == 1

    await alice_ws.disconnect()
    await bob_ws.disconnect()


# ═══════════════════════════════════════════════════════════
# 7. REST fallback 路徑（WebSocket 開不起來時前端會走這裡）
# ═══════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_rest_reply_path_is_gated_too():
    """否則掉線的受試者就等於繞過閘門。"""
    from rest_framework.test import APIClient

    user = User.objects.create_user(username="gate_rest", password="secret123")
    session_id = uuid4().hex
    DialogueSessionRecord.objects.create(
        user=user,
        session_id=session_id,
        topic_id=102,
        topic_title="核能",
        collection_name="nuclear_energy_all",
        session_state={"history": []},
        last_activity_at=timezone.now(),
    )

    client = APIClient()
    client.force_authenticate(user=user)
    with patch("api.views.get_dialogue_agent") as get_agent:
        response = client.post(
            f"/api/dialogue/sessions/{session_id}/reply/",
            {"message": "6456"},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["type"] == "input_blocked"
    assert response.data["reply"]
    assert get_agent.call_count == 0
    assert AIConversation.objects.filter(session_id=session_id).count() == 0

    record = DialogueSessionRecord.objects.get(session_id=session_id)
    assert record.invalid_input_count == 1
    assert record.session_state == {"history": []}


@pytest.mark.django_db
def test_rest_match_message_path_allows_low_information_content():
    from rest_framework.test import APIClient

    alice = User.objects.create_user(username="gate_rest_a", password="secret123")
    bob = User.objects.create_user(username="gate_rest_b", password="secret123")
    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=alice,
        user_b=bob,
        user_a_score=6.5,
        user_b_score=2.0,
        room_id=uuid4().hex,
        status=DialogueMatch.Status.ACTIVE,
    )

    client = APIClient()
    client.force_authenticate(user=alice)
    response = client.post(
        f"/api/matching/rooms/{match.room_id}/messages/",
        {"content": "6456"},
        format="json",
    )

    assert response.status_code == 201
    assert MatchMessage.objects.filter(
        match_id=match.id, sender=alice, content="6456"
    ).exists()

    too_fast = client.post(
        f"/api/matching/rooms/{match.room_id}/messages/",
        {"content": "好"},
        format="json",
    )

    assert too_fast.status_code == 429
    assert too_fast.data["type"] == "rate_limited"
    assert MatchMessage.objects.filter(match_id=match.id).count() == 1


# ═══════════════════════════════════════════════════════════
# 8. 實驗資料完整性
# ═══════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_finalize_ai_session_metrics_produces_exportable_fields():
    from apps.matching.services.input_gate_store import finalize_ai_session_metrics

    user = User.objects.create_user(username="gate_metrics", password="secret123")
    session_id = uuid4().hex
    DialogueSessionRecord.objects.create(
        user=user,
        session_id=session_id,
        topic_id=102,
        topic_title="核能",
        collection_name="nuclear_energy_all",
        last_activity_at=timezone.now(),
        invalid_input_total=3,
        input_attempt_total=10,
    )
    # 通過閘門的 7 則：5 則實質發言 + 2 則短回應。
    for text in (
        "核廢料最終處置一直沒有解方",
        "地方政府不願意接受最終處置場",
        "除役成本沒有算進發電成本裡",
        "再生能源的間歇性確實是問題",
        "我還是覺得風險大於效益",
        "好",
        "同意",
    ):
        AIConversation.objects.create(
            user=user, session_id=session_id, topic_id=102, user_prompt=text
        )

    metrics = finalize_ai_session_metrics(session_id)

    assert metrics["invalid_ratio"] == 0.3
    assert metrics["substantive_turn_count"] == 5

    record = DialogueSessionRecord.objects.get(session_id=session_id)
    assert record.invalid_ratio == 0.3
    assert record.substantive_turn_count == 5
