"""混合型對話入口：分流、逾時 fallback、後端把關。

一般使用者只有一個入口，由後端依立場分流；直接呼叫 join/sessions 會被擋。
見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from api.models import DialogueEntryAssignment
from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


def make_researcher(username):
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user = User.objects.create_user(username=username, password="pw-strong-12345")
    user.groups.add(group)
    return user


class DialogueEntryAssignmentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="assign_owner", password="pw-strong-12345"
        )

    def _create(self, **overrides):
        defaults = {
            "user": self.user,
            "topic_id": 102,
            "route": DialogueEntryAssignment.Route.MATCH,
            "stance_score": "6.00",
            "stance_category": "support",
            "support_threshold": 4.5,
            "oppose_threshold": 3.5,
            "entry_mode_at_assignment": "mixed",
        }
        defaults.update(overrides)
        return DialogueEntryAssignment.objects.create(**defaults)

    def test_fallback_fields_start_empty(self):
        assignment = self._create()

        self.assertIsNone(assignment.fallback_offered_at)
        self.assertIsNone(assignment.fallback_accepted_at)
        self.assertIsNotNone(assignment.assigned_at)

    def test_one_assignment_per_user_and_topic(self):
        from django.db import IntegrityError

        self._create()

        with self.assertRaises(IntegrityError):
            self._create()

    def test_same_user_can_have_assignment_per_topic(self):
        self._create(topic_id=102)
        self._create(topic_id=103)

        self.assertEqual(
            DialogueEntryAssignment.objects.filter(user=self.user).count(), 2
        )


class CanEnterHumanMatchingTests(TestCase):
    def test_neutral_cannot_enter_matching(self):
        from apps.matching.services.matcher import can_enter_human_matching

        self.assertFalse(can_enter_human_matching("neutral"))

    def test_support_and_oppose_can_enter_matching(self):
        from apps.matching.services.matcher import can_enter_human_matching

        self.assertTrue(can_enter_human_matching("support"))
        self.assertTrue(can_enter_human_matching("oppose"))

    def test_public_and_private_agree(self):
        from apps.matching.services.matcher import (
            _can_enter_human_matching,
            can_enter_human_matching,
        )

        for category in ("support", "neutral", "oppose"):
            self.assertEqual(
                can_enter_human_matching(category),
                _can_enter_human_matching(category),
            )
