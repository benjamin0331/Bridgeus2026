from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from api.dialogue_topics import SURVEY_CONFIGS, TOPIC_CONFIGS
from api.models import (
    AIConversation,
    DialogueMatch,
    DialogueSessionRecord,
    MatchMessage,
    MatchQueueEntry,
    MatchStanceDrift,
    UserStanceProfile,
)
from api.views import _resolve_stance_category


def fake_waste_items_response():
    return {
        "items": [
            {
                "claimText": "核廢料處理會帶來長期負擔",
                "anchorId": "anchor_waste",
                "path": ["長期處置"],
                "pointName": "核廢長期負擔",
                "stance": "反對",
                "confidence": 0.86,
                "rationale": "訊息明確提到核廢料與長期處理負擔。",
            }
        ],
        "invalidItems": [],
        "model": "gpt-5.4-mini",
    }


def fake_energy_items_response():
    return {
        "items": [
            {
                "claimText": "核能可以補足再生能源不穩定",
                "anchorId": "anchor_energy",
                "path": ["供電穩定"],
                "pointName": "補足間歇供電",
                "stance": "支持",
                "confidence": 0.88,
                "rationale": "訊息明確提到核能能支援供電穩定。",
            }
        ],
        "invalidItems": [],
        "model": "gpt-5.4-mini",
    }


def fake_economy_items_response():
    return {
        "items": [
            {
                "claimText": "核電除役與維護成本會增加負擔",
                "anchorId": "anchor_economy",
                "path": ["長期成本"],
                "pointName": "除役維護成本",
                "stance": "反對",
                "confidence": 0.84,
                "rationale": "訊息明確提到成本負擔。",
            }
        ],
        "invalidItems": [],
        "model": "gpt-5.4-mini",
    }


class FakeDialogueAgent:
    def respond(self, session):
        return f"AI reply to: {session.history[-1].content}"


class FakeExplodingDialogueAgent:
    def respond(self, session):
        raise RuntimeError("anthropic invalid key")


def build_supporting_answers():
    return {
        "1": 7,
        "2": 1,
        "3": 7,
        "4": 1,
        "5": 1,
        "6": 1,
        "7": 7,
        "8": 7,
    }


def build_opposing_answers():
    return {
        "1": 1,
        "2": 7,
        "3": 1,
        "4": 7,
        "5": 7,
        "6": 7,
        "7": 1,
        "8": 1,
    }


def build_mild_opposing_answers():
    return {
        "1": 3,
        "2": 5,
        "3": 3,
        "4": 5,
        "5": 5,
        "6": 5,
        "7": 3,
        "8": 3,
    }


def build_neutral_answers():
    return {
        "1": 4,
        "2": 4,
        "3": 4,
        "4": 4,
        "5": 4,
        "6": 4,
        "7": 4,
        "8": 4,
    }


def make_test_embedding(first_value):
    return [float(first_value), *([0.0] * 383)]


class SemanticTreeServiceTests(SimpleTestCase):
    def test_builds_openai_request_with_fixed_anchors_and_prompt_rules(self):
        from apps.matching.services.semantic_tree import (
            FIXED_ANCHORS,
            build_openai_request,
            create_initial_tree,
        )

        request = build_openai_request(
            text="核廢料處理會帶來長期負擔，也讓經濟成本上升。",
            tree=create_initial_tree("核電"),
            anchors=FIXED_ANCHORS,
        )
        request_text = str(request)

        self.assertEqual(
            [anchor["name"] for anchor in FIXED_ANCHORS],
            ["核能安全", "經濟成本", "能源問題", "環境保護", "民主治理", "核廢處理"],
        )
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertEqual(request["text"]["format"]["name"], "semantic_tree_analysis")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertTrue(request["model"])
        self.assertIn("你是「個人想法脈絡樹」的語意整理 agent", request_text)
        self.assertIn("只整理單一說話者自己的想法脈絡", request_text)
        self.assertIn("每次輸入最多輸出 2 個 items", request_text)
        self.assertIn("confidence 低於 0.55", request_text)
        self.assertIn("anchor_waste", request_text)
        self.assertNotIn("OPENAI_API_KEY", request_text)

    def test_validates_and_applies_openai_items_to_nested_tree(self):
        from apps.matching.services.semantic_tree import (
            apply_analysis_items_to_tree,
            create_initial_tree,
            validate_analysis_items,
        )

        tree = create_initial_tree("核電")
        result = validate_analysis_items(
            {
                "items": [
                    *fake_waste_items_response()["items"],
                    {
                        "claimText": "信心太低的模糊說法",
                        "anchorId": "anchor_safety",
                        "path": [],
                        "pointName": "模糊核安疑慮",
                        "stance": "中立",
                        "confidence": 0.54,
                        "rationale": "too low",
                    },
                    {
                        "claimText": "錯誤分類",
                        "anchorId": "anchor_unknown",
                        "path": [],
                        "pointName": "未知分類",
                        "stance": "中立",
                        "confidence": 0.7,
                        "rationale": "invalid anchor",
                    },
                ]
            },
            tree,
        )

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["invalidItems"]), 2)

        apply_result = apply_analysis_items_to_tree(
            tree,
            result["items"],
            source_message={
                "id": 7,
                "content": "核廢料處理會帶來長期負擔",
                "created_at": "2026-05-20T12:00:00Z",
                "sender_id": 1,
            },
        )
        waste_anchor = next(child for child in tree["children"] if child["id"] == "anchor_waste")
        category_node = waste_anchor["children"][0]
        point_node = category_node["children"][0]

        self.assertEqual(apply_result["appliedItems"][0]["applyMode"], "new")
        self.assertEqual(category_node["name"], "長期處置")
        self.assertEqual(point_node["name"], "核廢長期負擔")
        self.assertEqual(point_node["messages"][0]["sourceMessageId"], "7")

    def test_rejects_internal_ids_and_anchor_names_in_path(self):
        from apps.matching.services.semantic_tree import (
            create_initial_tree,
            validate_analysis_items,
        )

        result = validate_analysis_items(
            {
                "items": [
                    {
                        "claimText": "模型不應輸出內部 id",
                        "anchorId": "anchor_governance",
                        "path": ["category_4"],
                        "pointName": "資訊公開不足",
                        "stance": "中立",
                        "confidence": 0.8,
                        "rationale": "internal id",
                    },
                    {
                        "claimText": "模型不應重複 anchor 名稱",
                        "anchorId": "anchor_governance",
                        "path": ["民主治理"],
                        "pointName": "決策透明性不足",
                        "stance": "反對",
                        "confidence": 0.8,
                        "rationale": "anchor repeat",
                    },
                ]
            },
            create_initial_tree("核電"),
        )

        self.assertEqual(len(result["items"]), 0)
        self.assertEqual(len(result["invalidItems"]), 2)
        self.assertTrue(
            any("internal id" in item["error"] for item in result["invalidItems"])
        )
        self.assertTrue(
            any("anchor name" in item["error"] for item in result["invalidItems"])
        )

    def test_caps_items_at_two_and_path_at_depth_one(self):
        from apps.matching.services.semantic_tree import (
            create_initial_tree,
            validate_analysis_items,
        )

        result = validate_analysis_items(
            {
                "items": [
                    {
                        "claimText": "第一個可用重點",
                        "anchorId": "anchor_economy",
                        "path": ["經濟效益"],
                        "pointName": "經濟效益偏低",
                        "stance": "反對",
                        "confidence": 0.9,
                        "rationale": "valid",
                    },
                    {
                        "claimText": "第二個可用重點",
                        "anchorId": "anchor_waste",
                        "path": ["長期負擔"],
                        "pointName": "核廢長期負擔",
                        "stance": "反對",
                        "confidence": 0.8,
                        "rationale": "valid",
                    },
                    {
                        "claimText": "第三個超過上限",
                        "anchorId": "anchor_safety",
                        "path": ["風險治理"],
                        "pointName": "風險治理不足",
                        "stance": "反對",
                        "confidence": 0.9,
                        "rationale": "too many",
                    },
                    {
                        "claimText": "路徑太深",
                        "anchorId": "anchor_governance",
                        "path": ["政策", "溝通"],
                        "pointName": "地方溝通問題",
                        "stance": "中立",
                        "confidence": 0.8,
                        "rationale": "deep path",
                    },
                ]
            },
            create_initial_tree("核電"),
        )

        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(
            any("maximum of 2" in item["error"] for item in result["invalidItems"])
        )
        self.assertTrue(
            any("path too deep" in item["error"] for item in result["invalidItems"])
        )


class DialogueSessionApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="alice",
            password="secret123",
        )
        self.client.force_authenticate(user=self.user)

    def test_session_creation_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
                "survey_answers": {"1": 4, "2": 5},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_topic_list_returns_backend_config(self):
        response = self.client.get("/api/dialogue/topics/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        topic_ids = [topic["id"] for topic in response.data]
        self.assertIn(102, topic_ids)
        self.assertIn(103, topic_ids)
        topic_102 = next(t for t in response.data if t["id"] == 102)
        self.assertEqual(topic_102["title"], TOPIC_CONFIGS[102]["title"])
        self.assertEqual(topic_102["description"], TOPIC_CONFIGS[102]["topic_description"])

    def test_topic_survey_returns_backend_questions(self):
        response = self.client.get("/api/dialogue/topics/102/survey/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, SURVEY_CONFIGS[102])

    def test_topic_survey_returns_404_for_unknown_topic(self):
        response = self.client.get("/api/dialogue/topics/999/survey/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data["detail"],
            "找不到這個議題的問卷設定。",
        )

    def test_stance_thresholds_classify_support_oppose_and_neutral(self):
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.51),
            "support",
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=3.49),
            "oppose",
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=3.5),
            "neutral",
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.5),
            "neutral",
        )

    def test_session_calculates_seven_point_stance_score_with_reverse_items(self):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
                "survey_answers": {
                    "1": 7,
                    "2": 1,
                    "3": 7,
                    "4": 1,
                    "5": 1,
                    "6": 1,
                    "7": 7,
                    "8": 7,
                },
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        session_record = cache.get(f"dialogue_session:{session_id}")

        self.assertEqual(session_record["session"]["user_stance_score"], 7.0)
        self.assertEqual(session_record["session"]["user_stance_label"], "較支持核電")
        self.assertEqual(session_record["session"]["agent_stance"], "較反對核電")
        self.assertNotIn("user_stance_intensity", session_record["session"])

    def test_session_uses_q9_open_answer_as_initial_argument(self):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
                "survey_open_answers": {
                    "Q9": "我支持核電，因為它能穩定供電並協助減碳。",
                    "Q10": "反對者最強的論點是核安與核廢料風險。",
                },
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        session_record = cache.get(f"dialogue_session:{session_id}")

        self.assertEqual(
            session_record["session"]["user_initial_argument"],
            "我支持核電，因為它能穩定供電並協助減碳。",
        )
        self.assertEqual(
            session_record["survey_context"]["survey_open_answers"]["Q10"],
            "反對者最強的論點是核安與核廢料風險。",
        )
        self.assertEqual(
            session_record["survey_context"]["semantic_vector_interface"]["status"],
            "pending",
        )
        self.assertEqual(
            session_record["survey_context"]["semantic_vector_interface"][
                "target_question_code"
            ],
            "Q9",
        )

    @patch("api.views.get_dialogue_agent", return_value=FakeDialogueAgent())
    def test_session_create_and_reply_round_trip(self, mocked_get_agent):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
                "survey_answers": {"1": 4, "2": 5, "3": 4},
                "user_initial_argument": "我認為核能在減碳上有其必要性。",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]

        reply_response = self.client.post(
            f"/api/dialogue/sessions/{session_id}/reply/",
            {"message": "核能真的比其他方案更穩定嗎？"},
            format="json",
        )

        self.assertEqual(reply_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            reply_response.data["reply"],
            "AI reply to: 核能真的比其他方案更穩定嗎？",
        )
        self.assertEqual(len(reply_response.data["history"]), 2)
        self.assertEqual(reply_response.data["history"][0]["role"], "user")
        self.assertEqual(reply_response.data["history"][1]["role"], "agent")

        saved_turn = AIConversation.objects.get(session_id=session_id)
        self.assertEqual(saved_turn.user, self.user)
        self.assertEqual(saved_turn.topic_id, 102)
        self.assertEqual(saved_turn.user_prompt, "核能真的比其他方案更穩定嗎？")
        self.assertEqual(saved_turn.ai_response, "AI reply to: 核能真的比其他方案更穩定嗎？")
        mocked_get_agent.assert_called_once_with("nuclear_energy_all")

    @patch("chat.services.embedding.get_embedding", return_value=make_test_embedding(-1))
    @patch("api.views.build_q9_embedding", return_value=make_test_embedding(1), create=True)
    @patch("api.views.get_dialogue_agent", return_value=FakeDialogueAgent())
    def test_ai_reply_includes_session_stance_drift_value(
        self,
        mocked_get_agent,
        mocked_q9_embedding,
        mocked_message_embedding,
    ):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
                "survey_answers": build_supporting_answers(),
                "survey_open_answers": {
                    "Q9": "我支持核電，因為它能穩定供電並協助減碳。",
                },
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        session_record = cache.get(f"dialogue_session:{session_id}")
        self.assertEqual(
            session_record["survey_context"]["q9_embedding"],
            make_test_embedding(1),
        )

        reply_response = self.client.post(
            f"/api/dialogue/sessions/{session_id}/reply/",
            {"message": "核廢料和核安風險讓我開始擔心核電。"},
            format="json",
        )

        self.assertEqual(reply_response.status_code, status.HTTP_200_OK)
        self.assertEqual(reply_response.data["stance_drift"]["drift_value"], 2.0)
        self.assertIsNotNone(reply_response.data["stance_drift"]["measured_at"])
        self.assertEqual(
            cache.get(f"dialogue_session:{session_id}")["session"]["stance_drift"]["drift_value"],
            2.0,
        )
        mocked_q9_embedding.assert_called_once()
        mocked_message_embedding.assert_called_once_with("核廢料和核安風險讓我開始擔心核電。")
        mocked_get_agent.assert_called_once_with("nuclear_energy_all")

    @patch("api.views.get_dialogue_agent", return_value=FakeDialogueAgent())
    def test_latest_session_restores_history_after_cache_loss(self, mocked_get_agent):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
                "survey_answers": {"1": 4, "2": 5, "3": 4},
                "user_initial_argument": "我想先釐清核電與減碳的關係。",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]

        reply_response = self.client.post(
            f"/api/dialogue/sessions/{session_id}/reply/",
            {"message": "核電能不能補足再生能源不穩定？"},
            format="json",
        )
        self.assertEqual(reply_response.status_code, status.HTTP_200_OK)

        cache.clear()
        restore_response = self.client.get("/api/dialogue/sessions/latest/?topic_id=102")

        self.assertEqual(restore_response.status_code, status.HTTP_200_OK)
        self.assertEqual(restore_response.data["session_id"], session_id)
        self.assertEqual(restore_response.data["restored_from"], "database")
        self.assertEqual(len(restore_response.data["history"]), 2)
        self.assertEqual(
            restore_response.data["history"][0]["content"],
            "核電能不能補足再生能源不穩定？",
        )
        self.assertEqual(
            restore_response.data["history"][1]["content"],
            "AI reply to: 核電能不能補足再生能源不穩定？",
        )
        self.assertIsNotNone(cache.get(f"dialogue_session:{session_id}"))
        mocked_get_agent.assert_called_once_with("nuclear_energy_all")

    def test_session_restore_forbids_other_users(self):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        other_user = get_user_model().objects.create_user(
            username="mallory",
            password="secret123",
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other_user)

        response = other_client.get(f"/api/dialogue/sessions/{session_id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_ai_semantic_tree_analyzes_only_user_prompts(self):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        AIConversation.objects.create(
            user=self.user,
            session_id=session_id,
            topic_id=102,
            user_prompt="核能可以補足再生能源不穩定",
            ai_response="AI 回覆提到核廢料處理會帶來長期負擔",
        )

        analyzed_texts = []

        def fake_analyze(*, text, tree, anchors=None, anchor_descriptions=None, api_key=None, model=None):
            analyzed_texts.append(text)
            return fake_energy_items_response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "apps.matching.services.semantic_tree.analyze_with_openai",
            side_effect=fake_analyze,
        ):
            response = self.client.post(
                f"/api/dialogue/sessions/{session_id}/semantic-tree/analyze/"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(analyzed_texts, ["核能可以補足再生能源不穩定"])
        self.assertEqual(len(response.data["trees"]), 1)
        self.assertEqual(response.data["trees"][0]["label"], "我的脈絡")
        self.assertEqual(response.data["trees"][0]["analyzedSourceIds"], [str(AIConversation.objects.get().id)])
        self.assertNotIn("核廢料", str(response.data["treeData"]))

    def test_ai_semantic_tree_requires_openai_key_without_blocking_session(self):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        AIConversation.objects.create(
            user=self.user,
            session_id=session_id,
            topic_id=102,
            user_prompt="核能可以補足再生能源不穩定",
            ai_response="AI 回覆",
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            response = self.client.post(
                f"/api/dialogue/sessions/{session_id}/semantic-tree/analyze/"
            )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["error"], "missing_openai_api_key")
        self.assertIsNotNone(cache.get(f"dialogue_session:{session_id}"))


class HistoryApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="alice",
            password="secret123",
        )
        self.other_user = get_user_model().objects.create_user(
            username="bob",
            password="secret123",
        )
        self.third_user = get_user_model().objects.create_user(
            username="mallory",
            password="secret123",
        )
        self.client.force_authenticate(user=self.user)
        self.other_client = APIClient()
        self.other_client.force_authenticate(user=self.other_user)

    def _create_ai_history(self, *, user=None, session_id="session-alice"):
        user = user or self.user
        record = DialogueSessionRecord.objects.create(
            user=user,
            session_id=session_id,
            topic_id=102,
            topic_title="台灣核能議題討論",
            collection_name="nuclear_energy_all",
            session_state={
                "topic": "台灣核能議題討論",
                "history": [
                    {"role": "user", "content": "核能可以補足再生能源不穩定"},
                    {"role": "agent", "content": "AI 回覆"},
                ],
                "dialogue_phase": "engagement",
            },
            last_activity_at=timezone.now(),
        )
        turn = AIConversation.objects.create(
            user=user,
            session_id=session_id,
            topic_id=102,
            user_prompt="核能可以補足再生能源不穩定",
            ai_response="AI 回覆",
        )
        return record, turn

    def _create_match_history(self, *, room_id="history-room", with_messages=True):
        match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user,
            user_b=self.other_user,
            user_a_score=7,
            user_b_score=1,
            likert_distance=6,
            semantic_distance=0,
            match_score=1,
            room_id=room_id,
            status=DialogueMatch.Status.CLOSED,
            closed_at=timezone.now(),
        )
        if with_messages:
            own_message = MatchMessage.objects.create(
                match=match,
                sender=self.user,
                content="核廢料處理會帶來長期負擔",
            )
            partner_message = MatchMessage.objects.create(
                match=match,
                sender=self.other_user,
                content="核電除役與維護成本會增加負擔",
            )
            return match, own_message, partner_message
        return match, None, None

    def test_history_list_returns_own_ai_and_match_conversations(self):
        ai_record, _ = self._create_ai_history()
        self._create_ai_history(user=self.third_user, session_id="session-mallory")
        match, _, _ = self._create_match_history()
        self._create_match_history(room_id="empty-room", with_messages=False)

        response = self.client.get("/api/history/conversations/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        ids = {item["id"] for item in response.data["results"]}
        self.assertEqual(ids, {ai_record.session_id, match.room_id})
        self.assertNotIn("session-mallory", ids)
        ai_item = next(item for item in response.data["results"] if item["kind"] == "ai")
        match_item = next(item for item in response.data["results"] if item["kind"] == "match")
        self.assertEqual(ai_item["message_count"], 2)
        self.assertEqual(match_item["message_count"], 2)
        self.assertIn("核能可以", ai_item["last_message_preview"])
        self.assertIn("核電除役", match_item["last_message_preview"])

    def test_history_detail_returns_ai_messages_and_semantic_tree(self):
        ai_record, turn = self._create_ai_history()

        response = self.client.get(
            f"/api/history/conversations/ai/{ai_record.session_id}/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kind"], "ai")
        self.assertEqual(response.data["id"], ai_record.session_id)
        self.assertEqual(len(response.data["messages"]), 2)
        self.assertEqual(response.data["messages"][0]["role"], "user")
        self.assertEqual(response.data["messages"][0]["source_id"], str(turn.id))
        self.assertEqual(response.data["messages"][1]["role"], "agent")
        self.assertEqual(response.data["semantic_tree"]["trees"][0]["label"], "我的脈絡")

        other_response = self.other_client.get(
            f"/api/history/conversations/ai/{ai_record.session_id}/"
        )
        self.assertEqual(other_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_history_detail_returns_match_messages_without_partner_tree(self):
        match, own_message, partner_message = self._create_match_history()

        response = self.client.get(
            f"/api/history/conversations/match/{match.room_id}/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kind"], "match")
        self.assertEqual(response.data["id"], match.room_id)
        self.assertEqual(len(response.data["messages"]), 2)
        self.assertEqual(response.data["messages"][0]["role"], "user")
        self.assertEqual(response.data["messages"][0]["source_id"], str(own_message.id))
        self.assertEqual(response.data["messages"][1]["role"], "partner")
        self.assertEqual(response.data["messages"][1]["source_id"], str(partner_message.id))
        self.assertEqual(len(response.data["semantic_tree"]["trees"]), 1)
        self.assertEqual(response.data["semantic_tree"]["trees"][0]["label"], "我的脈絡")
        self.assertNotIn("匿名對話者", str(response.data["semantic_tree"]))

    def test_history_analyze_match_only_processes_current_user_messages(self):
        match, own_message, partner_message = self._create_match_history()
        analyzed_texts = []

        def fake_analyze(*, text, tree, anchors=None, anchor_descriptions=None, api_key=None, model=None):
            analyzed_texts.append(text)
            if "核廢料" in text:
                return fake_waste_items_response()
            return fake_economy_items_response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "apps.matching.services.semantic_tree.analyze_with_openai",
            side_effect=fake_analyze,
        ):
            response = self.client.post(
                f"/api/history/conversations/match/{match.room_id}/semantic-tree/analyze/"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(analyzed_texts, ["核廢料處理會帶來長期負擔"])
        self.assertEqual(response.data["semantic_tree"]["analyzedCount"], 1)
        tree = response.data["semantic_tree"]["trees"][0]
        self.assertIn(str(own_message.id), tree["analyzedSourceIds"])
        self.assertNotIn(str(partner_message.id), tree["analyzedSourceIds"])
        self.assertIn("核廢長期負擔", str(tree["treeData"]))
        self.assertNotIn("除役維護成本", str(tree["treeData"]))

    def test_history_analyze_missing_key_does_not_block_detail(self):
        ai_record, _ = self._create_ai_history()

        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            response = self.client.post(
                f"/api/history/conversations/ai/{ai_record.session_id}/semantic-tree/analyze/"
            )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["error"], "missing_openai_api_key")

        detail_response = self.client.get(
            f"/api/history/conversations/ai/{ai_record.session_id}/"
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(detail_response.data["messages"]), 2)


class MatchingApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="alice",
            password="secret123",
        )
        self.other_user = get_user_model().objects.create_user(
            username="bob",
            password="secret123",
        )
        self.third_user = get_user_model().objects.create_user(
            username="carol",
            password="secret123",
        )
        self.client.force_authenticate(user=self.user)
        self.other_client = APIClient()
        self.other_client.force_authenticate(user=self.other_user)
        self.third_client = APIClient()
        self.third_client.force_authenticate(user=self.third_user)

    def _create_match(self):
        first_response = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        self.assertEqual(
            first_response.data["status"],
            MatchQueueEntry.Status.MATCHING,
        )

        second_response = self.other_client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_opposing_answers(),
            },
            format="json",
        )
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.data["status"], MatchQueueEntry.Status.MATCHED)
        match = DialogueMatch.objects.get(id=second_response.data["match_id"])
        return match, second_response.data["room_id"]

    def test_matching_join_creates_profile_and_queue_entry(self):
        with patch(
            "apps.matching.services.matcher.build_q9_embedding",
            return_value=make_test_embedding(1),
        ):
            response = self.client.post(
                "/api/matching/join/",
                {
                    "topic_id": 102,
                    "survey_answers": build_supporting_answers(),
                    "survey_open_answers": {
                        "Q9": "我支持核電。",
                        "Q10": "反方會強調核安風險。",
                    },
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHING)
        self.assertEqual(response.data["stance_category"], "support")
        self.assertEqual(float(response.data["stance_score"]), 7.0)

        profile = UserStanceProfile.objects.get(user=self.user, topic_id=102)
        self.assertEqual(profile.stance_category, "support")
        self.assertEqual(float(profile.stance_score), 7.0)

        queue_entry = MatchQueueEntry.objects.get(
            user=self.user,
            topic_id=102,
            status=MatchQueueEntry.Status.MATCHING,
        )
        self.assertEqual(queue_entry.profile, profile)

    def test_matching_status_persists_after_join(self):
        self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHING)
        self.assertIsNotNone(response.data["queue_entry_id"])
        self.assertIsNone(response.data["match_id"])

    def test_matching_status_refreshes_queue_heartbeat(self):
        self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        queue_entry = MatchQueueEntry.objects.get(
            user=self.user,
            topic_id=102,
            status=MatchQueueEntry.Status.MATCHING,
        )
        old_timestamp = timezone.now() - timedelta(minutes=5)
        MatchQueueEntry.objects.filter(id=queue_entry.id).update(
            updated_at=old_timestamp
        )

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        queue_entry.refresh_from_db()
        self.assertGreater(queue_entry.updated_at, old_timestamp)

    def test_matching_ignores_stale_queue_entries(self):
        self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        old_timestamp = timezone.now() - timedelta(minutes=5)
        MatchQueueEntry.objects.filter(
            user=self.user,
            topic_id=102,
            status=MatchQueueEntry.Status.MATCHING,
        ).update(updated_at=old_timestamp)

        response = self.other_client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_opposing_answers(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHING)
        self.assertFalse(DialogueMatch.objects.exists())
        stale_entry = MatchQueueEntry.objects.get(user=self.user, topic_id=102)
        self.assertEqual(stale_entry.status, MatchQueueEntry.Status.CANCELLED)

    def test_matching_cancel_marks_queue_entry_cancelled(self):
        self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )

        response = self.client.post(
            "/api/matching/cancel/",
            {"topic_id": 102},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.CANCELLED)

        queue_entry = MatchQueueEntry.objects.filter(
            user=self.user,
            topic_id=102,
        ).latest("id")
        self.assertEqual(queue_entry.status, MatchQueueEntry.Status.CANCELLED)
        self.assertIsNotNone(queue_entry.cancelled_at)

    def test_matching_allows_rejoin_after_cancel(self):
        first_join = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        first_queue_id = first_join.data["queue_entry_id"]

        self.client.post(
            "/api/matching/cancel/",
            {"topic_id": 102},
            format="json",
        )

        second_join = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )

        self.assertEqual(second_join.status_code, status.HTTP_200_OK)
        self.assertEqual(second_join.data["status"], MatchQueueEntry.Status.MATCHING)
        self.assertNotEqual(second_join.data["queue_entry_id"], first_queue_id)

    def test_matching_pairs_users_with_opposite_stance_scores(self):
        match, _ = self._create_match()

        alice_status = self.client.get("/api/matching/status/?topic_id=102")
        self.assertEqual(alice_status.status_code, status.HTTP_200_OK)
        self.assertEqual(alice_status.data["status"], MatchQueueEntry.Status.MATCHED)
        self.assertEqual(alice_status.data["other_user_id"], self.other_user.id)
        self.assertEqual(alice_status.data["other_user_name"], "匿名對話者")

        self.assertEqual(match.status, DialogueMatch.Status.ACTIVE)
        self.assertEqual(
            {match.user_a_id, match.user_b_id},
            {self.user.id, self.other_user.id},
        )

    def test_matching_does_not_pair_same_stance_by_default(self):
        first_response = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["status"], MatchQueueEntry.Status.MATCHING)

        second_response = self.other_client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.data["status"], MatchQueueEntry.Status.MATCHING)
        self.assertFalse(DialogueMatch.objects.exists())

    @patch.dict("os.environ", {"MATCHING_ALLOW_SAME_STANCE_FALLBACK": "true"})
    def test_matching_allows_same_stance_pairing_during_testing(self):
        first_response = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["status"], MatchQueueEntry.Status.MATCHING)

        second_response = self.other_client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
            },
            format="json",
        )
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.data["status"], MatchQueueEntry.Status.MATCHED)
        self.assertEqual(second_response.data["other_user_id"], self.user.id)

        alice_status = self.client.get("/api/matching/status/?topic_id=102")
        self.assertEqual(alice_status.status_code, status.HTTP_200_OK)
        self.assertEqual(alice_status.data["status"], MatchQueueEntry.Status.MATCHED)
        self.assertEqual(alice_status.data["other_user_id"], self.other_user.id)

    def test_matching_recommends_ai_for_neutral_stance_by_default(self):
        response = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_neutral_answers(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ai_recommended")
        self.assertEqual(response.data["stance_category"], "neutral")
        self.assertEqual(float(response.data["stance_score"]), 4.0)
        self.assertFalse(MatchQueueEntry.objects.exists())

        status_response = self.client.get("/api/matching/status/?topic_id=102")
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data["status"], "ai_recommended")
        self.assertEqual(status_response.data["stance_category"], "neutral")

    def test_matching_join_persists_q9_embedding(self):
        with patch(
            "apps.matching.services.matcher.build_q9_embedding",
            return_value=make_test_embedding(1),
        ) as mocked_embedding:
            response = self.client.post(
                "/api/matching/join/",
                {
                    "topic_id": 102,
                    "survey_answers": build_supporting_answers(),
                    "survey_open_answers": {
                        "Q9": "我支持核電，因為它能穩定供電。",
                        "Q10": "反對者會擔心核廢料。",
                    },
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile = UserStanceProfile.objects.get(user=self.user, topic_id=102)
        self.assertEqual(list(profile.q9_embedding), make_test_embedding(1))
        mocked_embedding.assert_called_once_with(profile.survey_open_answers)

    def test_matching_selects_candidate_with_highest_weighted_match_score(self):
        def fake_embedding(open_answers):
            text = open_answers.get("Q9", "")
            if "opposite-vector" in text:
                return make_test_embedding(-1)
            return make_test_embedding(1)

        with patch(
            "apps.matching.services.matcher.build_q9_embedding",
            side_effect=fake_embedding,
        ):
            first_candidate = self.other_client.post(
                "/api/matching/join/",
                {
                    "topic_id": 102,
                    "survey_answers": build_opposing_answers(),
                    "survey_open_answers": {"Q9": "same-vector candidate"},
                },
                format="json",
            )
            self.assertEqual(
                first_candidate.data["status"],
                MatchQueueEntry.Status.MATCHING,
            )

            second_candidate = self.third_client.post(
                "/api/matching/join/",
                {
                    "topic_id": 102,
                    "survey_answers": build_mild_opposing_answers(),
                    "survey_open_answers": {"Q9": "opposite-vector candidate"},
                },
                format="json",
            )
            self.assertEqual(
                second_candidate.data["status"],
                MatchQueueEntry.Status.MATCHING,
            )

            response = self.client.post(
                "/api/matching/join/",
                {
                    "topic_id": 102,
                    "survey_answers": build_supporting_answers(),
                    "survey_open_answers": {"Q9": "same-vector requester"},
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHED)
        self.assertEqual(response.data["other_user_id"], self.third_user.id)

        match = DialogueMatch.objects.get(id=response.data["match_id"])
        self.assertEqual(
            {match.user_a_id, match.user_b_id},
            {self.user.id, self.third_user.id},
        )
        self.assertEqual(float(match.likert_distance), 4.0)
        self.assertEqual(float(match.semantic_distance), 2.0)
        self.assertAlmostEqual(float(match.match_score), 0.8, places=4)
        self.assertEqual(match.matching_algorithm_version, "likert-semantic-v1")

    def test_matching_join_can_restart_existing_active_match(self):
        old_match, _ = self._create_match()

        response = self.client.post(
            "/api/matching/join/",
            {
                "topic_id": 102,
                "survey_answers": build_supporting_answers(),
                "restart_existing_match": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHING)
        self.assertNotEqual(response.data["match_id"], old_match.id)

        old_match.refresh_from_db()
        self.assertEqual(old_match.status, DialogueMatch.Status.CLOSED)
        self.assertIsNotNone(old_match.closed_at)

        new_queue = MatchQueueEntry.objects.get(
            id=response.data["queue_entry_id"],
        )
        self.assertEqual(new_queue.user, self.user)
        self.assertEqual(new_queue.status, MatchQueueEntry.Status.MATCHING)
        self.assertIsNone(new_queue.match_id)

    def test_matched_users_can_exchange_room_messages(self):
        match, room_id = self._create_match()

        post_response = self.client.post(
            f"/api/matching/rooms/{room_id}/messages/",
            {"content": "你好，我想先從核安風險談起。"},
            format="json",
        )
        self.assertEqual(post_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(post_response.data["room_id"], room_id)
        self.assertEqual(len(post_response.data["messages"]), 1)
        self.assertEqual(
            post_response.data["messages"][0]["sender_id"],
            self.user.id,
        )
        self.assertEqual(
            post_response.data["messages"][0]["sender_name"],
            "匿名使用者",
        )

        fetch_response = self.other_client.get(
            f"/api/matching/rooms/{room_id}/messages/"
        )
        self.assertEqual(fetch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(fetch_response.data["match_id"], match.id)
        self.assertEqual(fetch_response.data["status"], MatchQueueEntry.Status.MATCHED)
        self.assertEqual(fetch_response.data["other_user_id"], self.user.id)
        self.assertEqual(fetch_response.data["other_user_name"], "匿名對話者")
        self.assertEqual(len(fetch_response.data["messages"]), 1)
        self.assertEqual(
            fetch_response.data["messages"][0]["content"],
            "你好，我想先從核安風險談起。",
        )
        self.assertEqual(MatchMessage.objects.filter(match=match).count(), 1)

    def test_room_messages_include_current_user_latest_drift_value(self):
        match, room_id = self._create_match()
        MatchStanceDrift.objects.create(
            match=match,
            user=self.user,
            drift_value=0.1234,
        )
        MatchStanceDrift.objects.create(
            match=match,
            user=self.other_user,
            drift_value=0.9876,
        )

        response = self.client.get(f"/api/matching/rooms/{room_id}/messages/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["stance_drift"]["drift_value"], 0.1234)
        self.assertIsNotNone(response.data["stance_drift"]["measured_at"])

    def test_semantic_tree_get_returns_initial_fixed_anchor_tree(self):
        _, room_id = self._create_match()

        response = self.client.get(f"/api/matching/rooms/{room_id}/semantic-tree/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["analysisStatus"], "ready")
        self.assertEqual(response.data["treeData"]["name"], "台灣核能議題討論")
        self.assertEqual(
            [child["name"] for child in response.data["treeData"]["children"]],
            ["核能安全", "經濟成本", "能源問題", "環境保護", "民主治理", "核廢處理"],
        )
        self.assertEqual(response.data["analysisHistory"], [])
        self.assertEqual(len(response.data["trees"]), 1)
        self.assertEqual(response.data["trees"][0]["label"], "我的脈絡")
        self.assertTrue(response.data["trees"][0]["isCurrentUser"])
        self.assertNotIn("匿名對話者", str(response.data))

    def test_semantic_tree_analyze_requires_openai_key_without_blocking_messages(self):
        match, room_id = self._create_match()
        MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            response = self.client.post(f"/api/matching/rooms/{room_id}/semantic-tree/analyze/")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["error"], "missing_openai_api_key")
        messages_response = self.client.get(f"/api/matching/rooms/{room_id}/messages/")
        self.assertEqual(messages_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(messages_response.data["messages"]), 1)

    def test_semantic_tree_analyze_updates_tree_and_skips_analyzed_messages(self):
        match, room_id = self._create_match()
        message = MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "apps.matching.services.semantic_tree.analyze_with_openai",
            return_value=fake_waste_items_response(),
        ) as mocked_analyze:
            first_response = self.client.post(
                f"/api/matching/rooms/{room_id}/semantic-tree/analyze/"
            )
            second_response = self.client.post(
                f"/api/matching/rooms/{room_id}/semantic-tree/analyze/"
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(mocked_analyze.call_count, 1)
        self.assertIn(str(message.id), first_response.data["analyzedMessageIds"])
        self.assertEqual(second_response.data["analyzedCount"], 0)

        tree_data = first_response.data["treeData"]
        waste_anchor = next(child for child in tree_data["children"] if child["id"] == "anchor_waste")
        self.assertEqual(waste_anchor["children"][0]["name"], "長期處置")
        self.assertEqual(waste_anchor["children"][0]["children"][0]["name"], "核廢長期負擔")

    def test_semantic_tree_timeline_shows_only_nodes_born_by_the_given_message(self):
        match, room_id = self._create_match()
        first_message = MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )
        second_message = MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="核能可以補足再生能源不穩定",
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "apps.matching.services.semantic_tree.analyze_with_openai",
            side_effect=[fake_waste_items_response(), fake_energy_items_response()],
        ):
            self.client.post(f"/api/matching/rooms/{room_id}/semantic-tree/analyze/")

        timeline_response = self.client.get(
            f"/api/matching/rooms/{room_id}/semantic-tree/timeline/",
            {"as_of_message_id": str(first_message.id)},
        )

        self.assertEqual(timeline_response.status_code, status.HTTP_200_OK)
        self.assertEqual(timeline_response.data["asOfMessageId"], str(first_message.id))

        tree_data = timeline_response.data["treeData"]
        waste_anchor = next(child for child in tree_data["children"] if child["id"] == "anchor_waste")
        energy_anchor = next(child for child in tree_data["children"] if child["id"] == "anchor_energy")
        self.assertEqual(waste_anchor["children"][0]["name"], "長期處置")
        self.assertEqual(energy_anchor["children"], [])
        self.assertTrue(energy_anchor["hiddenUntilUsed"])

        second_timeline_response = self.client.get(
            f"/api/matching/rooms/{room_id}/semantic-tree/timeline/",
            {"as_of_message_id": str(second_message.id)},
        )
        second_energy_anchor = next(
            child
            for child in second_timeline_response.data["treeData"]["children"]
            if child["id"] == "anchor_energy"
        )
        self.assertFalse(second_energy_anchor["hiddenUntilUsed"])

    def test_semantic_tree_timeline_requires_as_of_message_id(self):
        _, room_id = self._create_match()

        response = self.client.get(f"/api/matching/rooms/{room_id}/semantic-tree/timeline/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_semantic_tree_timeline_returns_404_for_unanalyzed_message(self):
        _, room_id = self._create_match()

        response = self.client.get(
            f"/api/matching/rooms/{room_id}/semantic-tree/timeline/",
            {"as_of_message_id": "does-not-exist"},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_semantic_tree_analyze_only_returns_and_updates_current_user_tree(self):
        match, room_id = self._create_match()
        own_message = MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )
        partner_message = MatchMessage.objects.create(
            match=match,
            sender=self.other_user,
            content="核電除役與維護成本會增加負擔",
        )
        tree_snapshots = []

        def fake_analyze(*, text, tree, anchors=None, anchor_descriptions=None, api_key=None, model=None):
            tree_snapshots.append(deepcopy(tree))
            if "核廢料" in text:
                return fake_waste_items_response()
            return fake_economy_items_response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "apps.matching.services.semantic_tree.analyze_with_openai",
            side_effect=fake_analyze,
        ) as mocked_analyze:
            response = self.client.post(f"/api/matching/rooms/{room_id}/semantic-tree/analyze/")
            partner_response = self.other_client.post(
                f"/api/matching/rooms/{room_id}/semantic-tree/analyze/"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(partner_response.status_code, status.HTTP_200_OK)
        self.assertEqual(mocked_analyze.call_count, 2)
        self.assertNotIn("核廢長期負擔", str(tree_snapshots[1]))
        self.assertEqual(response.data["analyzedCount"], 1)
        self.assertEqual(partner_response.data["analyzedCount"], 1)
        self.assertEqual(len(response.data["trees"]), 1)
        self.assertEqual(len(partner_response.data["trees"]), 1)
        own_tree = response.data["trees"][0]
        partner_tree = partner_response.data["trees"][0]
        self.assertIn(str(own_message.id), own_tree["analyzedSourceIds"])
        self.assertNotIn(str(partner_message.id), own_tree["analyzedSourceIds"])
        self.assertNotIn("匿名對話者", str(response.data))
        self.assertIn(str(partner_message.id), partner_tree["analyzedSourceIds"])
        self.assertNotIn(str(own_message.id), partner_tree["analyzedSourceIds"])
        self.assertNotIn("匿名對話者", str(partner_response.data))
        self.assertIn("核廢長期負擔", str(own_tree["treeData"]))
        self.assertNotIn("除役維護成本", str(own_tree["treeData"]))
        self.assertIn("除役維護成本", str(partner_tree["treeData"]))
        self.assertNotIn("核廢長期負擔", str(partner_tree["treeData"]))

    def test_room_messages_reject_blank_content(self):
        _, room_id = self._create_match()

        response = self.client.post(
            f"/api/matching/rooms/{room_id}/messages/",
            {"content": "   "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("content", response.data)

    def test_room_messages_forbid_non_participants(self):
        _, room_id = self._create_match()

        response = self.third_client.get(f"/api/matching/rooms/{room_id}/messages/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_idle_room_with_no_messages_closes_after_timeout_on_fetch(self):
        match, room_id = self._create_match()
        stale_time = timezone.now() - timedelta(minutes=11)
        DialogueMatch.objects.filter(id=match.id).update(created_at=stale_time)

        response = self.client.get(f"/api/matching/rooms/{room_id}/messages/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.CLOSED)
        self.assertIsNotNone(match.closed_at)

    def test_room_with_recent_message_stays_open_on_fetch(self):
        match, room_id = self._create_match()
        stale_time = timezone.now() - timedelta(minutes=11)
        recent_time = timezone.now() - timedelta(minutes=2)
        DialogueMatch.objects.filter(id=match.id).update(created_at=stale_time)
        message = MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="最近仍有對話。",
        )
        MatchMessage.objects.filter(id=message.id).update(created_at=recent_time)

        response = self.client.get(f"/api/matching/rooms/{room_id}/messages/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHED)
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.ACTIVE)

    def test_room_with_stale_last_message_closes_after_timeout_on_fetch(self):
        match, room_id = self._create_match()
        stale_time = timezone.now() - timedelta(minutes=11)
        message = MatchMessage.objects.create(
            match=match,
            sender=self.user,
            content="很久以前的訊息。",
        )
        DialogueMatch.objects.filter(id=match.id).update(created_at=stale_time)
        MatchMessage.objects.filter(id=message.id).update(created_at=stale_time)

        response = self.client.get(f"/api/matching/rooms/{room_id}/messages/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.CLOSED)

    def test_stale_room_closes_on_matching_status(self):
        match, _ = self._create_match()
        stale_time = timezone.now() - timedelta(minutes=11)
        DialogueMatch.objects.filter(id=match.id).update(created_at=stale_time)

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.CLOSED)

    def test_stale_room_rejects_new_messages_after_timeout(self):
        match, room_id = self._create_match()
        stale_time = timezone.now() - timedelta(minutes=11)
        DialogueMatch.objects.filter(id=match.id).update(created_at=stale_time)

        response = self.client.post(
            f"/api/matching/rooms/{room_id}/messages/",
            {"content": "這句話不應該送出。"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(MatchMessage.objects.filter(match=match).count(), 0)
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.CLOSED)

    def test_leaving_room_closes_match_for_both_participants(self):
        match, room_id = self._create_match()

        response = self.client.post(
            f"/api/matching/rooms/{room_id}/leave/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        self.assertEqual(response.data["room_id"], room_id)

        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.CLOSED)
        self.assertIsNotNone(match.closed_at)

        other_status = self.other_client.get("/api/matching/status/?topic_id=102")
        self.assertEqual(other_status.status_code, status.HTTP_200_OK)
        self.assertEqual(other_status.data["status"], "closed")

        send_response = self.other_client.post(
            f"/api/matching/rooms/{room_id}/messages/",
            {"content": "這句話不該送出"},
            format="json",
        )
        self.assertEqual(send_response.status_code, status.HTTP_409_CONFLICT)

    @patch.dict("os.environ", {"MATCH_ROOM_ABSENCE_TIMEOUT_SECONDS": "180"})
    def test_disconnected_participant_can_rejoin_before_absence_timeout(self):
        match, _ = self._create_match()
        from apps.matching.services.matcher import mark_match_participant_disconnected

        disconnected_at = timezone.now() - timedelta(seconds=60)
        mark_match_participant_disconnected(
            match=match,
            user_id=self.user.id,
            now=disconnected_at,
        )

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MatchQueueEntry.Status.MATCHED)
        self.assertIsNone(response.data["absence_deadline"])
        self.assertTrue(response.data["presence"]["current_user"]["connected"])
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.ACTIVE)

    @patch.dict("os.environ", {"MATCH_ROOM_ABSENCE_TIMEOUT_SECONDS": "180"})
    def test_disconnected_participant_closes_room_after_absence_timeout(self):
        match, _ = self._create_match()
        from apps.matching.services.matcher import mark_match_participant_disconnected

        disconnected_at = timezone.now() - timedelta(seconds=181)
        mark_match_participant_disconnected(
            match=match,
            user_id=self.user.id,
            now=disconnected_at,
        )

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "closed")
        match.refresh_from_db()
        self.assertEqual(match.status, DialogueMatch.Status.CLOSED)
        self.assertIsNotNone(match.closed_at)

    def test_session_uses_backend_title_for_known_topic(self):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "前端亂傳的舊標題",
                "topic_description": "",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]
        session_record = cache.get(f"dialogue_session:{session_id}")

        self.assertEqual(
            session_record["session"]["topic"],
            TOPIC_CONFIGS[102]["title"],
        )
        self.assertEqual(
            session_record["session"]["topic_description"],
            TOPIC_CONFIGS[102]["topic_description"],
        )

    @patch("api.views.get_dialogue_agent", return_value=FakeExplodingDialogueAgent())
    def test_reply_does_not_leak_internal_errors(self, mocked_get_agent):
        create_response = self.client.post(
            "/api/dialogue/sessions/",
            {
                "topic_id": 102,
                "topic_title": "核能發電在減碳中的角色",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["session_id"]

        reply_response = self.client.post(
            f"/api/dialogue/sessions/{session_id}/reply/",
            {"message": "請回應我"},
            format="json",
        )

        self.assertEqual(
            reply_response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            reply_response.data["detail"],
            "目前無法取得 AI 回覆，請稍後再試。",
        )
        self.assertNotIn("anthropic invalid key", reply_response.data["detail"])
        mocked_get_agent.assert_called_once_with("nuclear_energy_all")
