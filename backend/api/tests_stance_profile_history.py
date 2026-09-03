"""前測立場問卷是 append-only：每填一次留一列，舊的不被覆寫。

會被覆寫的話，上一場對話當時用的 survey_answers / survey_open_answers /
q9_embedding 就消失了——那正是該場的 s_pre 依據，D1↔Q10 的向量比較與立場漂移
基準線都會對不回去。見 UserStanceProfile 的 docstring。
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import (
    DialogueMatch,
    MatchQueueEntry,
    UserStanceProfile,
)
from api.tests import (
    assign_entry_route,
    build_opposing_answers,
    build_supporting_answers,
)

User = get_user_model()

TOPIC_ID = 102


def _embedding(value: float) -> list[float]:
    return [value, *([0.0] * 383)]


class StanceProfileHistoryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="stance-history", password="pw-strong-12345"
        )
        self.client.force_authenticate(self.user)
        # participant_entry_mode 預設是 MIXED，沒有分流指派的話
        # POST /api/dialogue/sessions/ 只會撞到入口把關的 403。
        assign_entry_route(self.user, TOPIC_ID)

    def _create_session(self, *, answers: dict, q9: str, embedding_seed: float):
        with patch(
            "api.views.build_q9_embedding",
            return_value=_embedding(embedding_seed),
        ):
            return self.client.post(
                "/api/dialogue/sessions/",
                {
                    "topic_id": TOPIC_ID,
                    "topic_title": "核能發電在減碳中的角色",
                    "survey_answers": answers,
                    "survey_open_answers": {"Q9": q9, "Q10": "對方的論點。"},
                },
                format="json",
            )

    def test_refilling_the_survey_appends_instead_of_overwriting(self):
        first = self._create_session(
            answers=build_supporting_answers(), q9="我支持核電。", embedding_seed=1
        )
        second = self._create_session(
            answers=build_opposing_answers(),
            q9="我改成反對核電了。",
            embedding_seed=2,
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)

        profiles = list(
            UserStanceProfile.objects.filter(
                user=self.user, topic_id=TOPIC_ID
            ).order_by("created_at", "id")
        )
        self.assertEqual(len(profiles), 2)

        # 第一次那一列必須原封不動——這是第一場對話的 s_pre 依據。
        self.assertEqual(profiles[0].survey_answers, build_supporting_answers())
        self.assertEqual(profiles[0].survey_open_answers["Q9"], "我支持核電。")
        self.assertEqual(float(profiles[0].stance_score), 7.0)
        self.assertEqual(profiles[0].stance_category, "support")

        self.assertEqual(profiles[1].survey_answers, build_opposing_answers())
        self.assertEqual(profiles[1].survey_open_answers["Q9"], "我改成反對核電了。")
        self.assertEqual(profiles[1].stance_category, "oppose")

    def test_latest_for_returns_the_most_recent_row(self):
        self._create_session(
            answers=build_supporting_answers(), q9="我支持核電。", embedding_seed=1
        )
        self._create_session(
            answers=build_opposing_answers(),
            q9="我改成反對核電了。",
            embedding_seed=2,
        )

        latest = UserStanceProfile.latest_for(
            user_id=self.user.id, topic_id=TOPIC_ID
        )
        self.assertEqual(latest.survey_open_answers["Q9"], "我改成反對核電了。")
        self.assertEqual(latest.stance_category, "oppose")

    def test_stance_profile_endpoint_offers_the_latest_answers(self):
        self._create_session(
            answers=build_supporting_answers(), q9="我支持核電。", embedding_seed=1
        )
        self._create_session(
            answers=build_opposing_answers(),
            q9="我改成反對核電了。",
            embedding_seed=2,
        )

        response = self.client.get(
            f"/api/dialogue/topics/{TOPIC_ID}/stance-profile/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["exists"])
        self.assertEqual(response.data["survey_answers"], build_opposing_answers())
        self.assertEqual(
            response.data["survey_open_answers"]["Q9"], "我改成反對核電了。"
        )

    def test_earlier_session_keeps_its_own_pre_survey_snapshot(self):
        """重填問卷不能動到上一場 AI 對話當時用的那份答案。"""
        from api.models import DialogueSessionRecord

        first = self._create_session(
            answers=build_supporting_answers(), q9="我支持核電。", embedding_seed=1
        )
        self._create_session(
            answers=build_opposing_answers(),
            q9="我改成反對核電了。",
            embedding_seed=2,
        )

        record = DialogueSessionRecord.objects.get(
            session_id=first.data["session_id"]
        )
        survey_context = record.survey_context or {}
        self.assertEqual(
            survey_context["survey_answers"], build_supporting_answers()
        )
        self.assertEqual(
            survey_context["survey_open_answers"]["Q9"], "我支持核電。"
        )


class MatchBoundProfileTests(APITestCase):
    """H-H：一場配對綁定的是配對成立當下那一列，不是這個人最新的一列。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="bound-a", password="pw-strong-12345"
        )
        self.other = User.objects.create_user(
            username="bound-b", password="pw-strong-12345"
        )
        self.match = DialogueMatch.objects.create(
            topic_id=TOPIC_ID,
            user_a=self.user,
            user_b=self.other,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="bound-profile-room",
        )

    def _profile(self, *, q9: str, embedding_seed: float):
        return UserStanceProfile.objects.create(
            user=self.user,
            topic_id=TOPIC_ID,
            stance_score=6.0,
            stance_category=UserStanceProfile.StanceCategory.SUPPORT,
            survey_answers=build_supporting_answers(),
            survey_open_answers={"Q9": q9},
            q9_embedding=_embedding(embedding_seed),
        )

    def test_for_match_returns_the_profile_bound_at_match_time(self):
        at_match_time = self._profile(q9="配對當下的說法。", embedding_seed=1)
        MatchQueueEntry.objects.create(
            user=self.user,
            topic_id=TOPIC_ID,
            profile=at_match_time,
            stance_score=6.0,
            status=MatchQueueEntry.Status.MATCHED,
            match=self.match,
        )
        # 對話結束後，這個人為了下一場又填了一次問卷。
        refilled = self._profile(q9="之後重填的說法。", embedding_seed=2)

        bound = UserStanceProfile.for_match(
            match_id=self.match.id, user_id=self.user.id
        )
        self.assertEqual(bound.id, at_match_time.id)
        self.assertNotEqual(bound.id, refilled.id)
        self.assertEqual(
            UserStanceProfile.latest_for(
                user_id=self.user.id, topic_id=TOPIC_ID
            ).id,
            refilled.id,
        )

    def test_for_match_returns_none_without_a_binding(self):
        self._profile(q9="沒有 queue entry。", embedding_seed=1)
        self.assertIsNone(
            UserStanceProfile.for_match(
                match_id=self.match.id, user_id=self.user.id
            )
        )
