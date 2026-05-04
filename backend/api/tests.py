from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from api.dialogue_topics import SURVEY_CONFIGS, TOPIC_CONFIGS
from api.models import AIConversation, DialogueMatch, MatchMessage, MatchQueueEntry, UserStanceProfile


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

        saved_turn = AIConversation.objects.get(session_id=session_id)
        self.assertEqual(saved_turn.user, self.user)
        self.assertEqual(saved_turn.topic_id, 102)
        self.assertEqual(saved_turn.user_prompt, "核能真的比其他方案更穩定嗎？")
        self.assertEqual(saved_turn.ai_response, "AI reply to: 核能真的比其他方案更穩定嗎？")
        mocked_get_agent.assert_called_once_with("nuclear_energy_all")


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
        self.assertEqual(alice_status.data["other_user_name"], self.other_user.username)

        self.assertEqual(match.status, DialogueMatch.Status.ACTIVE)
        self.assertEqual(
            {match.user_a_id, match.user_b_id},
            {self.user.id, self.other_user.id},
        )

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
            self.user.username,
        )

        fetch_response = self.other_client.get(
            f"/api/matching/rooms/{room_id}/messages/"
        )
        self.assertEqual(fetch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(fetch_response.data["match_id"], match.id)
        self.assertEqual(fetch_response.data["other_user_id"], self.user.id)
        self.assertEqual(fetch_response.data["other_user_name"], self.user.username)
        self.assertEqual(len(fetch_response.data["messages"]), 1)
        self.assertEqual(
            fetch_response.data["messages"][0]["content"],
            "你好，我想先從核安風險談起。",
        )
        self.assertEqual(MatchMessage.objects.filter(match=match).count(), 1)

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
