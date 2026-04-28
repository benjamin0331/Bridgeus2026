from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from api.dialogue_topics import SURVEY_CONFIGS, TOPIC_CONFIGS


class FakeDialogueAgent:
    def respond(self, session):
        return f"AI reply to: {session.history[-1].content}"


class FakeExplodingDialogueAgent:
    def respond(self, session):
        raise RuntimeError("anthropic invalid key")


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
        self.assertEqual(
            response.data,
            [
                {
                    "id": 102,
                    "title": TOPIC_CONFIGS[102]["title"],
                    "description": TOPIC_CONFIGS[102]["topic_description"],
                    "date": TOPIC_CONFIGS[102]["date"],
                }
            ],
        )

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
        mocked_get_agent.assert_called_once_with("nuclear_energy_all")

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
