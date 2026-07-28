from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.models import DialogueMatch, MatchMessage, UserStanceProfile
from apps.matching.services.hh_analysis import calculate_match_stance_drift
from apps.matching.services.topic_relevance import (
    check_match_topic_relevance,
    get_topic_relevance_policy,
)


def _embedding(value: float) -> list[float]:
    return [value, *([0.0] * 383)]


class MatchingAnalysisStabilityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(username="analysis-a")
        self.user_b = user_model.objects.create_user(username="analysis-b")
        self.match = DialogueMatch.objects.create(
            topic_id=103,
            user_a=self.user_a,
            user_b=self.user_b,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="analysis-stability-room",
        )

    def _message(self, content: str):
        return MatchMessage.objects.create(
            match=self.match,
            sender=self.user_a,
            content=content,
            embedding=_embedding(1),
        )

    def test_topic_relevance_does_not_trigger_from_a_single_message(self):
        self._message("我想先談社會與家庭影響。")

        with patch(
            "apps.matching.services.topic_relevance.cosine_similarity",
            return_value=0.1,
        ) as similarity:
            result = check_match_topic_relevance(
                match_id=self.match.id,
                user_id=self.user_a.id,
                topic_id=self.match.topic_id,
                topic_anchor_embedding=_embedding(1),
            )

        assert result == {"relevance_score": 1.0, "is_off_topic": False}
        similarity.assert_not_called()

    def test_topic_relevance_uses_the_relaxed_threshold_after_two_messages(self):
        self._message("我想先談國防兵源。")
        self._message("也要考慮性別平等。")

        with patch(
            "apps.matching.services.topic_relevance.cosine_similarity",
            return_value=0.26,
        ):
            result = check_match_topic_relevance(
                match_id=self.match.id,
                user_id=self.user_a.id,
                topic_id=self.match.topic_id,
                topic_anchor_embedding=_embedding(1),
            )

        assert result == {"relevance_score": 0.26, "is_off_topic": False}

    def test_topic_relevance_policy_is_topic_specific(self):
        nuclear = get_topic_relevance_policy(102)
        military = get_topic_relevance_policy(103)

        assert nuclear.anchor_text == "台灣是否應該擴大發展核能發電"
        assert nuclear.threshold == 0.35
        assert nuclear.window_size == 3
        assert nuclear.min_messages == 2
        assert military.anchor_text == "台灣是否應將女性納入義務兵役制度"
        assert military.threshold == 0.25

    def test_nuclear_topic_uses_point_35_threshold(self):
        self.match.topic_id = 102
        self.match.save(update_fields=["topic_id"])
        self._message("第一則核能觀點")
        self._message("第二則核能觀點")

        with patch(
            "apps.matching.services.topic_relevance.cosine_similarity",
            return_value=0.34,
        ):
            result = check_match_topic_relevance(
                match_id=self.match.id,
                user_id=self.user_a.id,
                topic_id=self.match.topic_id,
                topic_anchor_embedding=_embedding(1),
            )

        assert result == {"relevance_score": 0.34, "is_off_topic": True}

    def test_stance_drift_uses_cumulative_messages_instead_of_only_the_latest(self):
        UserStanceProfile.objects.create(
            user=self.user_a,
            topic_id=103,
            stance_score=6.0,
            stance_category=UserStanceProfile.StanceCategory.SUPPORT,
            q9_embedding=_embedding(1),
        )
        observed_window_sizes = []

        def capture_mean(embeddings):
            vectors = list(embeddings)
            observed_window_sizes.append(len(vectors))
            return vectors[0]

        self._message("第一個觀點")
        with (
            patch("apps.matching.services.hh_analysis._mean_embedding", side_effect=capture_mean),
            patch("apps.matching.services.hh_analysis.cosine_distance", side_effect=[0.1, 0.3]),
        ):
            calculate_match_stance_drift(match_id=self.match.id, user_id=self.user_a.id)
            self._message("第二個觀點")
            result = calculate_match_stance_drift(
                match_id=self.match.id,
                user_id=self.user_a.id,
            )

        assert observed_window_sizes == [1, 2]
        assert result == {"drift_value": 0.3, "direction": "approaching"}
