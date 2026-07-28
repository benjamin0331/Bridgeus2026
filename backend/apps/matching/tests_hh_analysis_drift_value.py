"""Unit tests for get_message_drift_value — the read-only lookup M6's
quality_filter.py uses to source ccnd_semantic_dist from hh_analysis.py's
already-persisted MatchStanceDrift records (see hh_analysis.py docstring).
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueMatch, MatchStanceDrift
from apps.matching.services.hh_analysis import get_message_drift_value


class GetMessageDriftValueTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="x")
        self.user_b = User.objects.create_user(username="user_b", password="x")
        self.match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user_a,
            user_b=self.user_b,
            user_a_score=6.0,
            user_b_score=2.0,
            room_id="test-room-drift-value",
        )

    def test_returns_zero_when_no_drift_recorded_yet(self):
        value = get_message_drift_value(
            match_id=self.match.id, user_id=self.user_a.id, as_of=timezone.now()
        )
        self.assertEqual(value, 0.0)

    def test_returns_latest_drift_at_or_before_as_of(self):
        MatchStanceDrift.objects.create(
            match=self.match, user=self.user_a, drift_value=0.10,
        )
        later_record = MatchStanceDrift.objects.create(
            match=self.match, user=self.user_a, drift_value=0.40,
        )

        value = get_message_drift_value(
            match_id=self.match.id, user_id=self.user_a.id,
            as_of=later_record.measured_at,
        )
        self.assertEqual(value, 0.40)

    def test_ignores_drift_recorded_after_as_of(self):
        earlier_record = MatchStanceDrift.objects.create(
            match=self.match, user=self.user_a, drift_value=0.10,
        )
        MatchStanceDrift.objects.create(
            match=self.match, user=self.user_a, drift_value=0.40,
        )

        value = get_message_drift_value(
            match_id=self.match.id, user_id=self.user_a.id,
            as_of=earlier_record.measured_at,
        )
        self.assertEqual(value, 0.10)

    def test_scoped_to_the_requested_user_only(self):
        MatchStanceDrift.objects.create(
            match=self.match, user=self.user_b, drift_value=0.99,
        )

        value = get_message_drift_value(
            match_id=self.match.id, user_id=self.user_a.id, as_of=timezone.now()
        )
        self.assertEqual(value, 0.0)
