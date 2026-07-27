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

    def test_stance_score_must_be_within_scale(self):
        """跟 UserStanceProfile／MatchQueueEntry 一樣有 [1,7] 的 DB 約束。

        這筆是「為什麼這個人被分到這一組」的稽核紀錄，範圍外的分數代表
        分流依據本身壞了，不能默默存進去。
        """
        from django.db import IntegrityError, transaction

        # 每次嘗試各自包一層 atomic：IntegrityError 會讓當前交易進入不可用
        # 狀態，不隔離的話第二次拋的是 TransactionManagementError，assertRaises
        # 接不到，測試會假性失敗。
        for bad_score in ("0.99", "7.01"):
            with self.subTest(stance_score=bad_score):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self._create(topic_id=104, stance_score=bad_score)


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


class CreateAiDialogueSessionHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ai_session_owner", password="pw-strong-12345"
        )

    def test_creates_session_and_profile(self):
        from api.models import DialogueSessionRecord, UserStanceProfile
        from api.views import _create_ai_dialogue_session

        payload = _create_ai_dialogue_session(
            user=self.user,
            topic_id=102,
            survey_answers={str(i): 4 for i in range(1, 9)},
            survey_open_answers={"Q9": "我覺得需要更多討論。", "Q10": "對方會說安全。"},
        )

        self.assertIn("session_id", payload)
        self.assertEqual(payload["stance_category"], "neutral")
        self.assertTrue(
            DialogueSessionRecord.objects.filter(
                user=self.user, session_id=payload["session_id"]
            ).exists()
        )
        self.assertTrue(
            UserStanceProfile.objects.filter(user=self.user, topic_id=102).exists()
        )

    def test_topic_metadata_defaults_come_from_topic_configs(self):
        """混合入口不送 topic_title/description，後端要自己從 TOPIC_CONFIGS 補。"""
        from api.dialogue_topics import TOPIC_CONFIGS
        from api.models import DialogueSessionRecord
        from api.views import _create_ai_dialogue_session

        payload = _create_ai_dialogue_session(
            user=self.user,
            topic_id=102,
            survey_answers={str(i): 4 for i in range(1, 9)},
            survey_open_answers={"Q9": "我覺得需要更多討論。"},
        )

        record = DialogueSessionRecord.objects.get(session_id=payload["session_id"])
        self.assertEqual(record.topic_id, 102)
        self.assertEqual(record.user_id, self.user.id)
        # 關鍵斷言：沒帶 topic_title 時，存下來的必須是 TOPIC_CONFIGS 的標題。
        # 只斷言 TOPIC_CONFIGS 自己的內容是空的——那跟 helper 有沒有跑無關。
        self.assertEqual(record.topic_title, TOPIC_CONFIGS[102]["title"])
        self.assertEqual(
            record.collection_name, TOPIC_CONFIGS[102]["collection_name"]
        )

    def test_client_supplied_topic_title_never_wins_for_known_topics(self):
        """已知議題的標題一律以 TOPIC_CONFIGS 為準，客戶端送什麼都不算數。

        _build_topic_config 回傳的是 topic_meta.get("title", topic_title)，
        所以呼叫端的 topic_title 只是「議題不存在時」的後備值。這對混合入口
        很重要：受試者就算自己偽造 topic_title 也改不了對話紀錄上的議題名稱。
        """
        from api.dialogue_topics import TOPIC_CONFIGS
        from api.models import DialogueSessionRecord
        from api.views import _create_ai_dialogue_session

        payload = _create_ai_dialogue_session(
            user=self.user,
            topic_id=102,
            survey_answers={str(i): 4 for i in range(1, 9)},
            survey_open_answers={"Q9": "我覺得需要更多討論。"},
            topic_title="偽造的標題",
        )

        record = DialogueSessionRecord.objects.get(session_id=payload["session_id"])
        self.assertEqual(record.topic_title, TOPIC_CONFIGS[102]["title"])
