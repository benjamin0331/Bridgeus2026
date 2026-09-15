"""CCND 樹在 H-AI 這條路上的持久化：兩個寫入者不能互相把對方洗掉。

H-AI 的 session blob（DialogueSessionRecord）同時被兩條路寫：
  1. WebSocket consumer 每輪回覆結束後整份寫回（api/consumers.py）
  2. CCND 分析端點 POST /api/dialogue/sessions/<id>/semantic-tree/analyze/

兩邊都是 read-modify-write 同一個 blob，中間沒有鎖，而且 consumer 讀進來的那份
在整輪生成期間（數十秒）一直握在記憶體裡。分析寫進來的樹因此會被 consumer 那份
較舊的副本蓋掉——`semantic_tree_state` 被寫成 `{}`，`analyzedSourceIds` 跟著歸零，
前端下一次分析就把整場對話重跑一次。

對 GPT 議題的症狀是「同樣的回覆長出不同節點」；對本地分類器議題（節點名稱是
決定性的）症狀是「節點少了一大半」——SEMANTIC_TREE_ANALYZE_BATCH_SIZE 一次只補
5 則，重跑補不回十幾輪的對話。

Run from backend/:
    uv run pytest api/tests_ccnd_tree_persistence.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import AIConversation, DialogueSessionRecord
from api.views import (
    _cache_dialogue_session_record,
    _persist_dialogue_session_record,
    _session_cache_key,
)

User = get_user_model()

SESSION_ID = "sess-ccnd-persist"
TOPIC_ID = 102  # 核能：本地分類器議題，正是回報出問題的那一個


def _tree_state(*point_names):
    """一棵合法的 v2 H-AI 樹，anchor_safety 底下掛指定的節點。"""
    return {
        "version": 2,
        "mode": "ai_user_tree",
        "anchors": [{"id": "anchor_safety", "name": "核能安全"}],
        "participants": {
            "user": {
                "ownerKey": "user",
                "treeData": {
                    "id": "root",
                    "name": "核能發電",
                    "type": "root",
                    "children": [
                        {
                            "id": "anchor_safety",
                            "name": "核能安全",
                            "type": "anchor",
                            "hiddenUntilUsed": False,
                            "children": [
                                {
                                    "id": f"agent_{i + 1}",
                                    "name": name,
                                    "type": "point",
                                    "children": [],
                                    "messages": [],
                                }
                                for i, name in enumerate(point_names)
                            ],
                        }
                    ],
                },
                # 刻意不用純數字：AIConversation.id 在全新測試庫從 1 開始，
                # 寫死 "1" 會讓待分析的那一輪被誤判成已分析而整個跳過。
                "analyzedSourceIds": ["seed-turn"],
                "analysisHistory": [],
            }
        },
    }


def _point_names(state):
    owner = (state or {}).get("participants", {}).get("user", {})
    anchors = owner.get("treeData", {}).get("children", [])
    return [
        child.get("name")
        for anchor in anchors
        for child in anchor.get("children", [])
    ]


def _session_record(user, *, semantic_tree=None):
    """WS consumer 與 REST 兩條路共用的那個 session_record dict。"""
    record = {
        "user_id": user.id,
        "session_id": SESSION_ID,
        "topic_id": TOPIC_ID,
        "topic_title": "核能發電",
        "collection_name": "nuclear_energy_all",
        "survey_context": {},
        "session": {"history": [], "dialogue_phase": "engagement"},
    }
    if semantic_tree is not None:
        record["semantic_tree"] = semantic_tree
    return record


@pytest.fixture
def user(db):
    return User.objects.create_user(username="ccnd-persist", password="pw")


@pytest.fixture(autouse=True)
def clear_session_cache():
    cache.delete(_session_cache_key(SESSION_ID))
    yield
    cache.delete(_session_cache_key(SESSION_ID))


@pytest.fixture
def stored_record(db, user):
    """DB 裡已經有一場帶著分析好的 CCND 樹的對話。"""
    return DialogueSessionRecord.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=TOPIC_ID,
        topic_title="核能發電",
        collection_name="nuclear_energy_all",
        session_state={"history": [], "dialogue_phase": "engagement"},
        semantic_tree_state=_tree_state("事故風險", "老舊延役"),
        status=DialogueSessionRecord.Status.ACTIVE,
        last_activity_at=timezone.now(),
    )


def test_persist_without_semantic_tree_key_keeps_the_stored_tree(user, stored_record):
    """payload 沒帶 semantic_tree ≠ 樹是空的。

    `_persist_dialogue_session_record` 用 `session_record.get("semantic_tree") or {}`
    寫欄位，所以任何一個不帶樹的寫入者都會把 DB 裡分析好的樹清成 {}。
    """
    _persist_dialogue_session_record(_session_record(user))

    stored_record.refresh_from_db()
    assert _point_names(stored_record.semantic_tree_state) == ["事故風險", "老舊延役"]


def test_websocket_turn_does_not_wipe_a_tree_written_mid_turn(user, stored_record):
    """重現 C：consumer 握著回合開始時的副本，分析在中途寫入，consumer 收尾時蓋掉。

    時間軸（單一 process，不需要多 worker 就會發生）：
      t0 使用者送出訊息，consumer 讀 cache 拿到還沒有樹的 session_record
      t1 CCND 分析跑完，把樹寫進 cache + DB
      t2 consumer 生成結束，把 t0 那份整份寫回 → 樹沒了
    """
    # t0：consumer 讀到的副本（這場還沒分析過，所以沒有 semantic_tree）
    stale_record = _session_record(user)
    _cache_dialogue_session_record(stale_record)

    # t1：分析寫入新的樹
    fresh_record = _session_record(user, semantic_tree=_tree_state("事故風險", "核廢最終處置"))
    _cache_dialogue_session_record(fresh_record)
    _persist_dialogue_session_record(fresh_record)

    # t2：consumer 收尾，寫回 t0 的副本
    _persist_dialogue_session_record(stale_record)

    stored_record.refresh_from_db()
    assert _point_names(stored_record.semantic_tree_state) == ["事故風險", "核廢最終處置"]


def test_analyze_does_not_drop_tree_changes_that_landed_since_it_read(
    user, stored_record, monkeypatch
):
    """分析必須在鎖裡重讀，不能拿進來時的副本當基準整份覆寫。

    對照組是 H-H 版的 `analyze_pending_room_messages`——它包了
    transaction.atomic() + select_for_update()，H-AI 版沒有。
    """
    from apps.matching.services import semantic_tree as st

    turn = AIConversation.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=TOPIC_ID,
        user_prompt="核廢料的最終處置還沒有解方",
        ai_response="…",
    )

    # 分析端點讀進來的副本（此刻 DB 與它一致）
    session_record = _session_record(user, semantic_tree=_tree_state("事故風險", "老舊延役"))

    # 在分析跑起來之前，另一個寫入者已經把第三個節點寫進 DB
    stored_record.semantic_tree_state = _tree_state("事故風險", "老舊延役", "地震帶選址")
    stored_record.save(update_fields=["semantic_tree_state"])

    monkeypatch.setattr(
        st,
        "analyze_text_for_tree",
        lambda **kwargs: {
            "items": [
                {
                    "claimText": "核廢料的最終處置還沒有解方",
                    "anchorId": "anchor_safety",
                    "path": [],
                    "pointName": "核廢最終處置",
                    "stance": "反對",
                    "confidence": 0.9,
                }
            ],
            "invalidItems": [],
            "model": "test-stub",
        },
    )

    st.analyze_pending_ai_conversations(
        session_record=session_record,
        session_id=SESSION_ID,
        user_id=user.id,
        root_name="核能發電",
    )
    _persist_dialogue_session_record(session_record)

    stored_record.refresh_from_db()
    names = _point_names(stored_record.semantic_tree_state)
    assert "核廢最終處置" in names, "這一輪分析出來的節點應該要進去"
    assert "地震帶選址" in names, "分析期間別人寫進 DB 的節點不該被整份覆寫掉"
    assert str(turn.id) in (
        stored_record.semantic_tree_state["participants"]["user"]["analyzedSourceIds"]
    )


def test_analyze_ignores_a_legacy_version_tree_in_the_db(user, stored_record, monkeypatch):
    """DB 上是舊版格式時，不得拿它當基準把呼叫端手上的樹洗掉。

    鎖裡重讀的用意是「以 DB 為準」，但讀不懂的版本不算「準」——
    get_ai_semantic_tree_state 會把它重置成空樹，照用下去就是我們正在修的
    那個洗掉行為換個地方發生。
    """
    from apps.matching.services import semantic_tree as st

    AIConversation.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=TOPIC_ID,
        user_prompt="核廢料的最終處置還沒有解方",
        ai_response="…",
    )

    stored_record.semantic_tree_state = {
        "version": 1,
        "mode": "ai_user_tree",
        "participants": {},
    }
    stored_record.save(update_fields=["semantic_tree_state"])

    session_record = _session_record(user, semantic_tree=_tree_state("事故風險", "老舊延役"))

    monkeypatch.setattr(
        st,
        "analyze_text_for_tree",
        lambda **kwargs: {
            "items": [
                {
                    "claimText": "核廢料的最終處置還沒有解方",
                    "anchorId": "anchor_safety",
                    "path": [],
                    "pointName": "核廢最終處置",
                    "stance": "反對",
                    "confidence": 0.9,
                }
            ],
            "invalidItems": [],
            "model": "test-stub",
        },
    )

    st.analyze_pending_ai_conversations(
        session_record=session_record,
        session_id=SESSION_ID,
        user_id=user.id,
        root_name="核能發電",
    )

    names = _point_names(session_record["semantic_tree"])
    assert "事故風險" in names, "呼叫端手上的樹不該被 DB 的舊格式洗掉"
    assert "核廢最終處置" in names


def test_first_analysis_records_each_turn_on_the_node_exactly_once(
    user, stored_record, monkeypatch
):
    """一輪發言只能在節點上留一筆訊息紀錄。

    分析分成兩段：鎖外面那段把 item 套到一棵工作樹上，純粹是為了讓同一批的
    下一則看得到前一則長出來的節點（模型的合併依據）；真正算數的套用在鎖裡對
    DB 上最新的樹做。工作樹如果就是最後要存的那一棵，同一批 item 會被套兩次
    ——`_append_node_message` 是無條件 append，節點的 messages 會多出重複紀錄，
    CCND 的訊息數與立場歷程跟著失真。

    這條路是「這場對話第一次分析」（DB 還沒有樹），也就是每一場都會走到的路。
    """
    from apps.matching.services import semantic_tree as st

    stored_record.semantic_tree_state = {}
    stored_record.save(update_fields=["semantic_tree_state"])

    turn = AIConversation.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=TOPIC_ID,
        user_prompt="核廢料的最終處置還沒有解方",
        ai_response="…",
    )

    monkeypatch.setattr(
        st,
        "analyze_text_for_tree",
        lambda **kwargs: {
            "items": [
                {
                    "claimText": "核廢料的最終處置還沒有解方",
                    "anchorId": "anchor_safety",
                    "path": [],
                    "pointName": "核廢最終處置",
                    "stance": "反對",
                    "confidence": 0.9,
                }
            ],
            "invalidItems": [],
            "model": "test-stub",
        },
    )

    session_record = _session_record(user)
    st.analyze_pending_ai_conversations(
        session_record=session_record,
        session_id=SESSION_ID,
        user_id=user.id,
        root_name="核能發電",
    )

    stored_record.refresh_from_db()
    owner = stored_record.semantic_tree_state["participants"]["user"]
    nodes = [
        child
        for anchor in owner["treeData"]["children"]
        for child in anchor.get("children", [])
        if child.get("name") == "核廢最終處置"
    ]
    assert len(nodes) == 1, "同一個 pointName 不該長出兩個節點"
    assert [m.get("sourceMessageId") for m in nodes[0].get("messages", [])] == [
        str(turn.id)
    ]
    assert owner["analyzedSourceIds"].count(str(turn.id)) == 1
    # 第一次出現的節點要記成 new。鎖外那趟 context pass 如果跟這棵樹共用，
    # 鎖裡就變成第二次套用，這裡會讀到 merge-existing。
    applied = owner["analysisHistory"][0]["appliedItems"]
    assert [item["applyMode"] for item in applied] == ["new"]
