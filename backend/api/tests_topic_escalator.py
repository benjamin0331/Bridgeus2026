"""
pytest tests for 議題 104「手扶梯靠邊站討論」。

這個議題跟 102／103 的差別在於它**沒有**本機分類模型，CCND 節點分析要落回
GPT（`semantic_tree.analyze_with_openai`）。這裡把那條分流、問卷計分方向與
兩個 API 端點釘住——分流寫錯的話症狀是「節點永遠生不出來」，很難從畫面看出
是分流問題還是模型問題。

Run from backend/:
    pytest api/tests_topic_escalator.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.dialogue_topics import SURVEY_CONFIGS, TOPIC_CONFIGS

User = get_user_model()

TOPIC_ID = 104

# 一致支持「一側站立、一側通行」：正向題給 7、反向題給 1。
PRO_ANSWERS = {"1": 7, "2": 1, "3": 7, "4": 1, "5": 7, "6": 1, "7": 7, "8": 1}
ANTI_ANSWERS = {"1": 1, "2": 7, "3": 1, "4": 7, "5": 1, "6": 7, "7": 1, "8": 7}


@pytest.fixture
def client(db):
    user = User.objects.create_user(username="escalator_user", password="pass1234!")
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


class TestEscalatorTopicConfig:
    def test_ccnd_uses_the_gpt_path_not_a_local_classifier(self):
        """104 沒有訓練模型，必須落回 analyze_with_openai。"""
        from apps.matching.services.semantic_tree import (
            LOCAL_CLASSIFIER_TOPIC_IDS,
            uses_local_classifier,
        )

        assert TOPIC_ID not in LOCAL_CLASSIFIER_TOPIC_IDS
        assert uses_local_classifier(TOPIC_ID) is False

    def test_anchor_ids_are_unique_and_described(self):
        topic = TOPIC_CONFIGS[TOPIC_ID]
        anchor_ids = [anchor["id"] for anchor in topic["anchors"]]
        assert len(anchor_ids) == len(set(anchor_ids)) == 6
        # 描述是 GPT 分類節點時唯一的面向定義，缺一個就等於那個錨點沒說明。
        assert set(anchor_ids) == set(topic["anchor_descriptions"])
        assert all(topic["anchor_descriptions"][a] for a in anchor_ids)

    def test_reverse_flags_match_stance_rules(self):
        """`reverse_scored` 與 stance_rules 的反向題清單必須一致。

        兩邊分頭維護：計分讀 stance_rules，前端題目卡片讀 reverse_scored。
        不一致時計分照舊、畫面卻標錯，是最難從測試以外發現的那種錯。
        """
        survey = SURVEY_CONFIGS[TOPIC_ID]
        declared = set(survey["stance_rules"]["reverse_question_ids"])
        flagged = {q["id"] for q in survey["questions"] if q["reverse_scored"]}
        assert declared == flagged == {2, 4, 6, 8}

    def test_open_questions_are_q9_and_q10(self):
        codes = [q["code"] for q in SURVEY_CONFIGS[TOPIC_ID]["open_questions"]]
        assert codes == ["Q9", "Q10"]


@pytest.mark.django_db
class TestEscalatorScoring:
    def test_consistent_pro_and_anti_land_on_opposite_categories(self):
        from api.display_settings import resolve_stance_category
        from api.views import _compute_user_stance_score

        pro = _compute_user_stance_score(topic_id=TOPIC_ID, survey_answers=PRO_ANSWERS)
        anti = _compute_user_stance_score(topic_id=TOPIC_ID, survey_answers=ANTI_ANSWERS)

        assert pro == 7.0
        assert anti == 1.0
        assert resolve_stance_category(topic_id=TOPIC_ID, user_stance_score=pro) == "support"
        assert resolve_stance_category(topic_id=TOPIC_ID, user_stance_score=anti) == "oppose"

    def test_all_sevens_is_neutral(self):
        """全部按 7：4 題正向 + 4 題反向反轉後互相抵消，落在中立帶。"""
        from api.display_settings import resolve_stance_category
        from api.views import _compute_user_stance_score

        score = _compute_user_stance_score(
            topic_id=TOPIC_ID,
            survey_answers={str(i): 7 for i in range(1, 9)},
        )
        assert score == 4.0
        assert resolve_stance_category(topic_id=TOPIC_ID, user_stance_score=score) == "neutral"


@pytest.mark.django_db
class TestEscalatorPostQuestionnaire:
    def test_post_reverse_items_are_derived_from_this_topic(self):
        """後測 C1 的反向題是從各議題自己的前測設定推出來的，不是寫死的。

        104 的前測反向題是 2/4/6/8，經 POST_LIKERT_TO_PRE_QUESTION 打散後
        對應到後測第 1/6/7/8 題——跟 102（前測反向 2/4/5/6 → 後測 2/6/7/8）
        不同。這裡釘住新議題不必動 models 就能正確計分。
        """
        from api.models import _post_likert_reversed_indices

        assert _post_likert_reversed_indices(TOPIC_ID) == {1, 6, 7, 8}
        assert _post_likert_reversed_indices(102) == {2, 6, 7, 8}


@pytest.mark.django_db
class TestEscalatorEndpoints:
    def test_topic_list_includes_escalator(self, client):
        response = client.get("/api/dialogue/topics/")
        assert response.status_code == 200
        topics = {topic["id"]: topic for topic in response.data}
        assert TOPIC_ID in topics
        assert topics[TOPIC_ID]["title"] == TOPIC_CONFIGS[TOPIC_ID]["title"]

    def test_survey_endpoint_serves_the_escalator_questionnaire(self, client):
        response = client.get(f"/api/dialogue/topics/{TOPIC_ID}/survey/")
        assert response.status_code == 200
        assert len(response.data["questions"]) == 8
        assert len(response.data["open_questions"]) == 2
