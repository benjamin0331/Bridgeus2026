"""人對 AI（H-AI）觀點進入 M6 觀點知識庫審核機制的端到端測試。

規則：只有「使用者發言」會產生 ViewpointNode 送進 Step 4 人工終審；AI 回覆
只當作 ai_response_text 的對話脈絡存著，本身不是被審核的觀點。

觸發點：對話後問卷送出 → session 由 ACTIVE 轉 CLOSED →
api.views._close_dialogue_session_record() 的 transaction.on_commit() 呼叫
apps.summary.pipeline.assemble.run_pipeline_for_session()。
"""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import AIConversation, DialogueSessionRecord
from api.permissions import RESEARCHER_GROUP_NAME
from apps.matching.services.semantic_tree import (
    AI_TREE_MODE,
    OWNER_AI_USER,
    SEMANTIC_TREE_STATE_VERSION,
    apply_analysis_items_to_tree,
    create_owner_tree_state,
    get_topic_anchors,
)
from apps.summary.models import DialogueSummary, ViewpointNode

User = get_user_model()

TOPIC_ID = 103  # 女性義務兵役
SESSION_ID = "haisession103abc"

_EMB_T1 = [1.0] + [0.0] * 383
_EMB_T2 = [0.0, 1.0] + [0.0] * 382  # 與 _EMB_T1 正交 → cosine distance 1.0

# 七輪對話，每則都遠超過品質篩選的 30 字門檻，使用者發言 7 次 >= 6（H-AI 只數
# 使用者發言）。第 2 輪使用者發言（embedding=_EMB_T2，相對第 1 輪 distance=1.0）
# 是唯一會清過 Step 2 SEMANTIC_DIST_THRESHOLD 的候選。
_TURNS = [
    (
        "我覺得如果女性想要享有和男性一樣完整的公民權利，那麼在國防義務上也應該一起承擔，這樣才算真正的平等。",
        "你把權利與義務的對等當成性別平等的核心判準，這是一個很清楚的立場。",
        _EMB_T1,
    ),
    (
        "少子化讓可以徵召的兵源一年比一年少，把女性納入義務役可以直接擴大兵員基數，對後備動員也有實質幫助。",
        "你從兵源總量的角度切入，這和單純談個別戰力是不同的層次。",
        _EMB_T2,
    ),
    (
        "我知道有人擔心軍中性騷擾問題，但我認為那應該是要求軍方改善管理，而不是因此就不讓女性服役。",
        "你主張把軍中現況當成需要一併解決的配套，而不是反對女性服役的理由。",
        _EMB_T2,
    ),
    (
        "挪威和以色列都已經推行性別中立的徵兵制度，台灣其實可以參考它們在體位分流上的具體做法。",
        "你援引了國際上已經落地的制度作為可行性的佐證。",
        _EMB_T2,
    ),
    (
        "體能標準可以依任務類別分流設計，行政、後勤、通訊這些崗位本來就不需要一線步兵的體能門檻。",
        "你把體能疑慮拆成不同職務類別分開討論。",
        _EMB_T2,
    ),
    (
        "如果只讓男性服役，等於把國防的機會成本全部壓在男性的求學與就業黃金期，這對男性也不公平。",
        "你從男性單方承擔的角度補強了公平性的論點。",
        _EMB_T2,
    ),
    (
        "我認為配套要先到位，包含營區設施、申訴機制、育嬰役期彈性，然後再分階段擴大徵集對象。",
        "你主張採取先建制、再分階段實施的推行節奏。",
        _EMB_T2,
    ),
]


def _make_researcher(username="hai_reviewer"):
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user = User.objects.create_user(username=username, password="pw")
    user.groups.add(group)
    return user


def _post_questionnaire_payload():
    return {
        "topic_id": TOPIC_ID,
        "session_id": SESSION_ID,
        "experiment_condition": "ai",
        "opponent_judgment": 2,
        "post_likert_1": 6, "post_likert_2": 3, "post_likert_3": 4, "post_likert_4": 5,
        "post_likert_5": 6, "post_likert_6": 2, "post_likert_7": 3, "post_likert_8": 4,
        "exp_stance_change_1": 4, "exp_stance_change_2": 5, "exp_quality_1": 6,
        "exp_quality_2": 5, "exp_reflection_1": 3, "exp_reflection_2": 4,
        "exp_comprehension_1": 5,
        "ccnd_attention": 5, "ccnd_awareness": 4, "ccnd_influence": 3,
        "post_open_comprehension": "這是一段刻意寫得超過五十個字的回答內容，用來通過 D1 對立觀點陳述的最低字數限制檢查機制。" * 2,
        "post_open_feedback": "整體來說這次和 AI 的討論讓我對女性義務兵役這個議題有了更立體的理解。",
        "discomfort_flag": False,
    }


def _build_user_tree_state(analyzed_turn_ids: list[str]):
    """組出 DialogueSessionRecord.semantic_tree_state：使用者那一棵 CCND 樹，
    把指定的 AIConversation turn id 標記成命中 anchor_equality。"""
    anchors = get_topic_anchors(TOPIC_ID)
    owner_state = create_owner_tree_state(OWNER_AI_USER, "女性義務兵役", anchors)
    for turn_id in analyzed_turn_ids:
        apply_analysis_items_to_tree(
            owner_state["treeData"],
            [
                {
                    "claimText": "權利義務對等，女性應一起承擔國防義務",
                    "anchorId": "anchor_equality",
                    "path": [],
                    "pointName": "權利義務對等",
                    "stance": "支持",
                    "confidence": 0.9,
                    "rationale": "test",
                }
            ],
            source_message={"id": turn_id},
        )
        owner_state["analysisHistory"].append(
            {"sourceId": turn_id, "analyzedAt": timezone.now().isoformat()}
        )
        owner_state["analyzedSourceIds"].append(turn_id)
    return {
        "version": SEMANTIC_TREE_STATE_VERSION,
        "mode": AI_TREE_MODE,
        "anchors": anchors,
        "participants": {OWNER_AI_USER: owner_state},
    }


@pytest.fixture
def hai_session(db):
    user = User.objects.create_user(username="hai_subject", password="pw")
    record = DialogueSessionRecord.objects.create(
        user=user,
        session_id=SESSION_ID,
        topic_id=TOPIC_ID,
        topic_title="女性義務兵役討論",
        collection_name="military_service_women_news",
        session_state={"user_stance_score": 5.25, "history": []},
        survey_context={"user_stance_score": 5.25},
        last_activity_at=timezone.now(),
    )
    turns = []
    for user_prompt, ai_response, embedding in _TURNS:
        turns.append(
            AIConversation.objects.create(
                user=user,
                session_id=SESSION_ID,
                topic_id=TOPIC_ID,
                user_prompt=user_prompt,
                ai_response=ai_response,
                embedding=embedding,
            )
        )
    # 第 2 輪之後的使用者發言都在 CCND 樹裡有分析紀錄。
    record.semantic_tree_state = _build_user_tree_state([str(t.id) for t in turns[1:]])
    record.save(update_fields=["semantic_tree_state"])
    return user, record, turns


def _submit_questionnaire(client, capture):
    # execute=True：讓 transaction.on_commit() 註冊的 M6 pipeline 真的跑起來。
    with capture(execute=True) as callbacks:
        res = client.post(
            "/api/post-questionnaire/", _post_questionnaire_payload(), format="json"
        )
    assert res.status_code == 201, res.data
    return callbacks


@pytest.mark.django_db
class TestHAIViewpointReviewPipeline:
    def test_submitting_questionnaire_files_user_viewpoint_for_review(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)

        _submit_questionnaire(client, django_capture_on_commit_callbacks)

        summary = DialogueSummary.objects.get(dialogue_id=SESSION_ID)
        assert summary.topic_id == TOPIC_ID
        # 使用者 = A 方且有立場；AI 沒有量測到的立場 → side_b 留空。
        assert summary.side_a_stance == "support"
        assert summary.side_b_stance == ""

        nodes = list(ViewpointNode.objects.filter(summary=summary))
        assert len(nodes) >= 1
        for node in nodes:
            assert node.review_status == ViewpointNode.ReviewStatus.PENDING
            assert node.speaker_side == "a"  # 一律使用者側
            assert node.topic_id == TOPIC_ID
            assert node.dimension == "anchor_equality"

    def test_ai_reply_is_context_only_not_its_own_review_item(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)
        _submit_questionnaire(client, django_capture_on_commit_callbacks)

        nodes = ViewpointNode.objects.filter(topic_id=TOPIC_ID)
        ai_replies = {ai for _, ai, _ in _TURNS}
        # 沒有任何一個節點是「拿 AI 回覆當作被審核的觀點」。
        assert not any(n.user_input_text in ai_replies for n in nodes)
        # AI 回覆只會出現在 ai_response_text（脈絡）欄位。
        node = nodes.first()
        assert node.ai_response_text in ai_replies
        assert node.user_input_text not in ai_replies

    def test_researcher_sees_hai_viewpoint_in_pending_queue(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)
        _submit_questionnaire(client, django_capture_on_commit_callbacks)

        researcher = _make_researcher()
        client.force_authenticate(user=researcher)
        res = client.get("/api/summary/viewpoints/", {"topic_id": TOPIC_ID})
        assert res.status_code == 200
        assert len(res.data) >= 1
        row = res.data[0]
        assert row["dialogue_id"] == SESSION_ID
        assert row["review_status"] == "pending"
        assert row["speaker_side"] == "a"

    def test_approved_hai_viewpoint_surfaces_in_public_knowledge_base(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)
        _submit_questionnaire(client, django_capture_on_commit_callbacks)
        node = ViewpointNode.objects.filter(topic_id=TOPIC_ID).first()

        researcher = _make_researcher()
        client.force_authenticate(user=researcher)
        approve = client.post(
            f"/api/summary/viewpoints/{node.id}/review/", {"action": "approve"}
        )
        assert approve.status_code == 200

        client.force_authenticate(user=user)
        highlights = client.get(
            "/api/summary/viewpoints/highlights/", {"topic_id": TOPIC_ID}
        )
        assert highlights.status_code == 200
        assert node.id in {row["id"] for row in highlights.data}

        browse = client.get("/api/summary/viewpoints/browse/", {"topic_id": TOPIC_ID})
        assert browse.status_code == 200
        assert node.id in {row["id"] for row in browse.data["results"]}

    def test_conversation_detail_renders_both_sides_and_user_ccnd_tree(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)
        _submit_questionnaire(client, django_capture_on_commit_callbacks)
        node = ViewpointNode.objects.filter(topic_id=TOPIC_ID).first()

        researcher = _make_researcher()
        client.force_authenticate(user=researcher)
        client.post(f"/api/summary/viewpoints/{node.id}/review/", {"action": "approve"})

        client.force_authenticate(user=user)
        # summary_text 由 pipeline 存成逐字稿格式，詳情頁會嘗試即時生成 AI 摘要；
        # 這裡把它 mock 掉，測試不打真的 Anthropic API。
        with mock.patch(
            "apps.summary.pipeline.assemble.generate_ai_summary",
            return_value="這是一段測試用的 AI 摘要。",
        ):
            res = client.get(f"/api/summary/viewpoints/{node.id}/conversation/")
        assert res.status_code == 200, res.data
        sides = {m["side"] for m in res.data["messages"]}
        assert sides == {"a", "b"}  # 逐字稿仍完整呈現雙方往返
        assert res.data["semantic_tree"]["semanticMode"] == AI_TREE_MODE
        assert res.data["side_a_stance"] == "support"

    def test_pipeline_failure_never_blocks_questionnaire_submission(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)

        with mock.patch(
            "apps.summary.pipeline.assemble.run_pipeline_for_session",
            side_effect=RuntimeError("boom"),
        ):
            with django_capture_on_commit_callbacks(execute=True):
                res = client.post(
                    "/api/post-questionnaire/",
                    _post_questionnaire_payload(),
                    format="json",
                )

        assert res.status_code == 201
        record.refresh_from_db()
        assert record.status == DialogueSessionRecord.Status.CLOSED
        assert not DialogueSummary.objects.filter(dialogue_id=SESSION_ID).exists()

    def test_pipeline_is_not_run_twice_for_the_same_session(
        self, hai_session, django_capture_on_commit_callbacks
    ):
        user, record, turns = hai_session
        client = APIClient()
        client.force_authenticate(user=user)
        _submit_questionnaire(client, django_capture_on_commit_callbacks)

        first_count = ViewpointNode.objects.filter(topic_id=TOPIC_ID).count()
        assert first_count >= 1

        # 直接再呼叫一次 pipeline（模擬競態/重試）——不該再寫一份。
        from apps.summary.pipeline.assemble import run_pipeline_for_session

        assert run_pipeline_for_session(SESSION_ID) == 0
        assert DialogueSummary.objects.filter(dialogue_id=SESSION_ID).count() == 1
        assert ViewpointNode.objects.filter(topic_id=TOPIC_ID).count() == first_count

    def test_fewer_than_six_user_turns_is_filtered_out(self, db):
        """H-AI 的 Step 1「輪數 >= 6」只數使用者發言：5 輪使用者 + 5 則 AI 回覆
        （合計 10 則）仍然不合格，整場對話不產生任何觀點。"""
        from apps.summary.pipeline.assemble import run_pipeline_for_session

        user = User.objects.create_user(username="hai_short", password="pw")
        session_id = "haishort103xyz"
        record = DialogueSessionRecord.objects.create(
            user=user,
            session_id=session_id,
            topic_id=TOPIC_ID,
            topic_title="女性義務兵役討論",
            collection_name="military_service_women_news",
            session_state={"user_stance_score": 5.25, "history": []},
            survey_context={"user_stance_score": 5.25},
            last_activity_at=timezone.now(),
        )
        turns = []
        for user_prompt, ai_response, embedding in _TURNS[:5]:  # 只取 5 輪
            turns.append(
                AIConversation.objects.create(
                    user=user,
                    session_id=session_id,
                    topic_id=TOPIC_ID,
                    user_prompt=user_prompt,
                    ai_response=ai_response,
                    embedding=embedding,
                )
            )
        anchors = get_topic_anchors(TOPIC_ID)
        owner_state = create_owner_tree_state(OWNER_AI_USER, "女性義務兵役", anchors)
        for t in turns[1:]:
            apply_analysis_items_to_tree(
                owner_state["treeData"],
                [
                    {
                        "claimText": "x",
                        "anchorId": "anchor_equality",
                        "path": [],
                        "pointName": "權利義務對等",
                        "stance": "支持",
                        "confidence": 0.9,
                        "rationale": "test",
                    }
                ],
                source_message={"id": str(t.id)},
            )
            owner_state["analysisHistory"].append(
                {"sourceId": str(t.id), "analyzedAt": timezone.now().isoformat()}
            )
            owner_state["analyzedSourceIds"].append(str(t.id))
        record.semantic_tree_state = {
            "version": SEMANTIC_TREE_STATE_VERSION,
            "mode": AI_TREE_MODE,
            "anchors": anchors,
            "participants": {OWNER_AI_USER: owner_state},
        }
        record.save(update_fields=["semantic_tree_state"])

        assert run_pipeline_for_session(session_id) == 0
        assert not DialogueSummary.objects.filter(dialogue_id=session_id).exists()
