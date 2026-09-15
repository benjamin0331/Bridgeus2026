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


@pytest.mark.xfail(
    reason=(
        "鎖 + 重讀已於 2026-09-15 應要求移除（成本：穿隧 DB 下每次 analyze 多 "
        "3~4 趟來回，約 0.6~1.3 秒）。這項保護因此不再成立，失敗是預期內的。\n"
        "實際失去的是什麼：analyze 以「進來時的副本」為基準整份覆寫 "
        "semantic_tree_state，所以在它跑的期間（本地分類器並行後仍有數秒）"
        "任何其他寫入者的成果都會被蓋掉——另一個分頁、歷史頁的分析端點、"
        "或 WS consumer 拿著一份較舊但非空的樹落庫。\n"
        "注意 _persist_dialogue_session_record 的 `or {}` 修正仍在，所以 WS "
        "那條「完全沒帶樹」的情況仍然安全；不安全的是它帶著舊樹的情況。\n"
        "要恢復保護：把 transaction.atomic() + select_for_update() 重新包回 "
        "analyze_pending_ai_conversations 的套用與寫入段（見 git "
        "f5c9466），這個測試就會轉綠。"
    ),
    strict=False,
)
def test_analyze_does_not_drop_tree_changes_that_landed_since_it_read(
    user, stored_record, monkeypatch
):
    """分析必須在鎖裡重讀，不能拿進來時的副本當基準整份覆寫。

    對照組是 H-H 版的 `analyze_pending_room_messages`——它包了
    transaction.atomic() + select_for_update()，H-AI 版已移除。
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
    # 這支函式本身不寫 DB，落庫是 view 的責任（DialogueSessionSemanticTreeAnalyzeView
    # 在它之後呼叫這支）。測試要走完整條路徑才算數。
    _persist_dialogue_session_record(session_record)

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


# ── 同一批次的分析並行化 ──────────────────────────────────────────────
#
# 實測（2026-09-15，143 個有樹的 session）：同批次內每一則的分析耗時中位數
# 1.59 秒、p90 9.0 秒，batch size 是 5，所以一次 analyze 請求中位數就要 8 秒。
# 時間不在 BERT 推論，在每個節點多打的那一次 classify_stance_with_openai。

def _pending_turns(user, count):
    return [
        AIConversation.objects.create(
            user=user,
            session_id=SESSION_ID,
            topic_id=TOPIC_ID,
            user_prompt=f"第 {i} 則發言：核廢料的最終處置還沒有解方。",
            ai_response="…",
        )
        for i in range(count)
    ]


def _stub_result(text, point_name=None):
    return {
        "items": [
            {
                "claimText": text,
                "anchorId": "anchor_safety",
                "path": [],
                "pointName": point_name or f"節點-{text[2]}",
                "stance": "反對",
                "confidence": 0.9,
            }
        ],
        "invalidItems": [],
        "model": "test-stub",
    }


def test_local_classifier_batch_runs_in_parallel(user, stored_record, monkeypatch):
    """本地分類器議題的同一批次必須並行跑。

    並行在這條路上是**語意等價**的：`build_candidate_items(text, anchors)`
    根本不吃樹，樹只在 validate_analysis_items 影響純資訊欄位 mergeTargetName，
    真正的合併是鎖裡的 apply_analysis_items_to_tree 對最新的樹重新判定。
    """
    import threading
    from apps.matching.services import semantic_tree as st

    turns = _pending_turns(user, 3)
    monkeypatch.setattr(st, "uses_local_classifier", lambda topic_id: True)

    barrier = threading.Barrier(3)
    observed = {"parallel": False}

    def stub(**kwargs):
        try:
            barrier.wait(timeout=5)
            observed["parallel"] = True
        except threading.BrokenBarrierError:
            # 循序執行時第一個就會等到逾時，旗標維持 False。
            pass
        return _stub_result(kwargs["text"])

    monkeypatch.setattr(st, "analyze_text_for_tree", stub)

    session_record = _session_record(user)
    st.analyze_pending_ai_conversations(
        session_record=session_record,
        session_id=SESSION_ID,
        user_id=user.id,
        root_name="核能發電",
    )

    assert observed["parallel"], "同一批次的分析應該並行，不是一則跑完再跑下一則"

    # 並行不得打亂套用順序：新分析的三則必須照 turn 的時間序接在後面。
    # （前面那筆 "seed-turn" 來自 stored_record fixture 的樹——鎖裡會從 DB
    # 重讀並採用那一份，所以它會留在清單開頭。）
    owner = session_record["semantic_tree"]["participants"]["user"]
    expected = [str(t.id) for t in turns]
    assert owner["analyzedSourceIds"][-3:] == expected
    assert [h["sourceId"] for h in owner["analysisHistory"]] == expected


def test_openai_path_stays_sequential_so_merging_context_survives(
    user, stored_record, monkeypatch
):
    """OpenAI 路徑必須維持循序。

    那條路的 prompt 夾帶整棵樹與現有節點清單，同批的下一則要看得到前一則長出
    的節點才合併得起來（見 build_openai_request 的「合併規則」）。並行會讓 5 則
    都看到同一棵起始樹，近義節點就會增生。
    """
    from apps.matching.services import semantic_tree as st

    _pending_turns(user, 3)
    monkeypatch.setattr(st, "uses_local_classifier", lambda topic_id: False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    seen_node_counts = []

    def stub(**kwargs):
        anchor = next(
            (a for a in kwargs["tree"].get("children", []) if a["id"] == "anchor_safety"),
            {"children": []},
        )
        seen_node_counts.append(len(anchor.get("children") or []))
        return _stub_result(kwargs["text"])

    monkeypatch.setattr(st, "analyze_text_for_tree", stub)

    st.analyze_pending_ai_conversations(
        session_record=_session_record(user),
        session_id=SESSION_ID,
        user_id=user.id,
        root_name="核能發電",
    )

    # 每一則都該比前一則多看到一個節點；並行的話三次都會看到同一個數字。
    assert seen_node_counts == sorted(set(seen_node_counts)), (
        f"後一則沒有看到前一則長出的節點：{seen_node_counts}"
    )
    assert len(set(seen_node_counts)) == 3, (
        f"三則看到的樹應該逐次成長，實際：{seen_node_counts}"
    )


def test_nuclear_micro_model_cache_is_guarded_by_the_lock(monkeypatch):
    """topic 102 的 micro model 載入必須在鎖裡。

    女性議題那支（women_conscription_node_classifier）本來就包了 `with _lock`，
    核能這支沒有。循序執行時碰不到，一旦同批次並行，多條執行緒會同時 miss 同一
    個 class_id 並各自載入一份 391MB 權重——CUDA 上就是 VRAM 直接翻倍。
    """
    import threading
    from apps.matching.services import nuclear_node_classifier as nc

    import time

    loads = []
    load_lock = threading.Lock()

    def fake_from_pretrained(path, *a, **kw):
        # 停一下，讓沒有鎖的版本確實有機會三條同時進到載入區。
        # 不用 Barrier：有鎖時只有一條進得來，barrier 必然 broken，
        # 會噴出跟受測行為無關的執行緒例外警告。
        time.sleep(0.2)
        with load_lock:
            loads.append(str(path))

        class _M:
            def to(self, *_a, **_kw): return self
            def eval(self): return self
        return _M()

    monkeypatch.setattr(nc, "_state", {
        "device": "cpu", "micro_cache": {},
        "class_name_map": {}, "cluster_name_map": {},
        "macro_tokenizer": None, "macro_model": None, "macro_mapping": {},
    })
    monkeypatch.setattr(nc, "_ensure_loaded", lambda: nc._state)
    monkeypatch.setattr(nc.Path, "exists", lambda self: True)
    monkeypatch.setattr(nc, "_load_label_mapping", lambda d: {})

    import transformers
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", fake_from_pretrained)
    monkeypatch.setattr(
        transformers.AutoModelForSequenceClassification, "from_pretrained", fake_from_pretrained
    )

    threads = [threading.Thread(target=lambda: nc._get_micro_model(0)) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    # 有鎖的話只有第一條會真的載入；沒鎖的話三條都會載入一遍。
    assert len(loads) <= 2, f"同一個 micro model 被重複載入 {len(loads)} 次（權重約 391MB）"


def _normalise_tree(node, turn_index):
    """把樹壓成可跨次比對的形狀：去掉時間戳，把 turn id 換成該批次內的序號。"""
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "claimText": node.get("claimText"),
        "messages": [
            {
                "text": m.get("text"),
                "stance": m.get("stance"),
                "mode": m.get("mode"),
                "turn": turn_index.get(str(m.get("sourceMessageId"))),
            }
            for m in node.get("messages") or []
        ],
        "children": [_normalise_tree(c, turn_index) for c in node.get("children") or []],
    }


def test_parallel_and_sequential_produce_the_same_tree(db, monkeypatch):
    """並行與循序必須長出**一模一樣**的樹，包含合併行為與節點 id。

    這是並行化唯一真正要證明的事。先前那次量測用的 stub 每則回傳不同的
    pointName，整場沒發生過合併，等於沒測到關鍵情況。這裡刻意讓同一批次裡
    三則指向同一個 pointName——如果並行讓後面幾則看不到前面長出的節點而
    另開新節點，這個測試就會紅。

    保證來源不是「剛好沒事」：真正的合併發生在鎖裡的
    apply_analysis_items_to_tree，它對 DB 上最新的樹、**照 turn 順序循序**套用；
    鎖外那趟只是拿來餵給模型的脈絡，而本地分類器根本不吃樹。
    """
    from apps.matching.services import semantic_tree as st

    # 同一批次內刻意製造合併：0/1/3 同名，2 不同名。
    POINT_NAMES = ["核廢最終處置", "核廢最終處置", "事故風險", "核廢最終處置"]

    def run(session_id, parallel):
        user = User.objects.create_user(username=f"eq-{session_id}", password="pw")
        DialogueSessionRecord.objects.create(
            user=user, session_id=session_id, topic_id=TOPIC_ID,
            topic_title="核能發電", collection_name="c",
            session_state={}, semantic_tree_state={},
            status=DialogueSessionRecord.Status.ACTIVE,
            last_activity_at=timezone.now(),
        )
        turns = [
            AIConversation.objects.create(
                user=user, session_id=session_id, topic_id=TOPIC_ID,
                user_prompt=f"第 {i} 則發言：核廢料的最終處置還沒有解方。",
                ai_response="…",
            )
            for i in range(len(POINT_NAMES))
        ]
        turn_index = {str(t.id): i for i, t in enumerate(turns)}

        monkeypatch.setattr(st, "uses_local_classifier", lambda topic_id: parallel)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        def stub(**kwargs):
            i = int(kwargs["text"][2])
            return {
                "items": [{
                    "claimText": f"主張 {i}",
                    "anchorId": "anchor_safety",
                    "path": [],
                    "pointName": POINT_NAMES[i],
                    "stance": "反對" if i % 2 else "支持",
                    "confidence": 0.9,
                }],
                "invalidItems": [],
                "model": "test-stub",
            }

        monkeypatch.setattr(st, "analyze_text_for_tree", stub)

        record = {
            "user_id": user.id, "session_id": session_id, "topic_id": TOPIC_ID,
            "topic_title": "核能發電", "collection_name": "c",
            "survey_context": {}, "session": {},
        }
        st.analyze_pending_ai_conversations(
            session_record=record, session_id=session_id,
            user_id=user.id, root_name="核能發電",
        )
        owner = record["semantic_tree"]["participants"]["user"]
        return _normalise_tree(owner["treeData"], turn_index), owner

    parallel_tree, parallel_owner = run("eq-parallel", True)
    sequential_tree, _ = run("eq-sequential", False)

    assert parallel_tree == sequential_tree

    # 這個測試本身要有意義：合併必須真的發生過，否則它只是在比兩棵各自獨立的樹。
    safety = next(
        a for a in parallel_tree["children"] if a["name"] == "核能安全"
    )
    merged = next(c for c in safety["children"] if c["name"] == "核廢最終處置")
    assert [m["turn"] for m in merged["messages"]] == [0, 1, 3], (
        "0/1/3 應該合併進同一個節點且保持順序"
    )
    assert len(safety["children"]) == 2, "同名的三則不該各自開一個節點"
