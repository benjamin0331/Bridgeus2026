"""混合型對話入口：分流、逾時 fallback、後端把關。

一般使用者只有一個入口，由後端依立場分流；直接呼叫 join/sessions 會被擋。
見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

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


# 議題 102 的反向題是 Q2/Q4/Q5/Q6（SURVEY_CONFIGS 的 reverse_question_ids）。
# 全部答 4 → 反轉後仍是 4 → 平均 4.0 → neutral。
NEUTRAL_ANSWERS = {str(i): 4 for i in range(1, 9)}
# 正向題答 7、反向題答 1 → 反轉後也是 7 → 平均 7.0 → support。
SUPPORT_ANSWERS = {"1": 7, "2": 1, "3": 7, "4": 1, "5": 1, "6": 1, "7": 7, "8": 7}
OPEN_ANSWERS = {"Q9": "我認為需要更多公共討論才能決定。", "Q10": "對方會強調供電穩定。"}


class DialogueEntryRoutingTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(
            username="entry_participant", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=self.participant)

    def _enter(self, answers, topic_id=102):
        return self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": topic_id,
                "survey_answers": answers,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

    def test_neutral_stance_routes_to_ai(self):
        response = self._enter(NEUTRAL_ANSWERS)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["route"], "ai")
        self.assertIn("session_id", response.data)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.AI)
        self.assertEqual(assignment.stance_category, "neutral")
        self.assertEqual(assignment.entry_mode_at_assignment, "mixed")

    def test_extreme_stance_routes_to_match(self):
        response = self._enter(SUPPORT_ANSWERS)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["route"], "match")
        self.assertEqual(response.data["status"], "matching")

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.MATCH)
        self.assertEqual(assignment.stance_category, "support")

    def test_assignment_records_thresholds_in_force(self):
        from api.models import TopicDisplayOverride

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.5, oppose_threshold=2.5
        )

        self._enter(NEUTRAL_ANSWERS)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.support_threshold, 5.5)
        self.assertEqual(assignment.oppose_threshold, 2.5)

    def test_threshold_override_changes_routing(self):
        """預設門檻下 SUPPORT_ANSWERS 走配對；把 support 門檻拉到 7.5 後走 AI。"""
        from api.models import TopicDisplayOverride

        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=7.5)

        response = self._enter(SUPPORT_ANSWERS)

        self.assertEqual(response.data["route"], "ai")

    def test_hidden_topic_returns_404(self):
        from api.models import TopicDisplayOverride

        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False
        )

        response = self._enter(NEUTRAL_ANSWERS)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            DialogueEntryAssignment.objects.filter(user=self.participant).exists()
        )

    def test_researcher_still_sees_topic_hidden_from_participants(self):
        """可見性是分角色的：對受試者關閉不影響研究者自己測試。"""
        from api.models import TopicDisplayOverride

        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=True
        )
        researcher = make_researcher("entry_researcher")
        self.client.force_authenticate(user=researcher)

        response = self._enter(NEUTRAL_ANSWERS)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_resubmitting_survey_overwrites_assignment_and_clears_fallback(self):
        from django.utils import timezone

        self._enter(SUPPORT_ANSWERS)
        DialogueEntryAssignment.objects.filter(
            user=self.participant, topic_id=102
        ).update(fallback_offered_at=timezone.now())

        self._enter(NEUTRAL_ANSWERS)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertEqual(assignment.route, DialogueEntryAssignment.Route.AI)
        self.assertIsNone(assignment.fallback_offered_at)
        self.assertEqual(
            DialogueEntryAssignment.objects.filter(user=self.participant).count(), 1
        )

    def test_unknown_topic_returns_404(self):
        response = self._enter(NEUTRAL_ANSWERS, topic_id=999)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_client_cannot_smuggle_topic_metadata(self):
        """序列化器不收 topic_title 等欄位，多送了也不該影響結果。"""
        response = self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": NEUTRAL_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
                "topic_title": "偽造標題",
                "user_initial_argument": "偽造論述",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        from api.dialogue_topics import TOPIC_CONFIGS
        from api.models import DialogueSessionRecord

        record = DialogueSessionRecord.objects.get(
            session_id=response.data["session_id"]
        )
        self.assertEqual(record.topic_title, TOPIC_CONFIGS[102]["title"])


class FallbackOfferTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(
            username="fallback_participant", password="pw-strong-12345"
        )
        self.client.force_authenticate(user=self.participant)
        self.client.post(
            "/api/dialogue/entry/",
            {
                "topic_id": 102,
                "survey_answers": SUPPORT_ANSWERS,
                "survey_open_answers": OPEN_ANSWERS,
            },
            format="json",
        )

    def _age_queue_entry(self, seconds):
        from datetime import timedelta

        from django.utils import timezone

        from api.models import MatchQueueEntry

        MatchQueueEntry.objects.filter(
            user=self.participant, topic_id=102
        ).update(waiting_started_at=timezone.now() - timedelta(seconds=seconds))

    def test_offer_unavailable_before_timeout(self):
        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["fallback_offer"]["available"])
        self.assertEqual(response.data["fallback_offer"]["timeout_seconds"], 300)

    def test_offer_available_after_timeout_and_records_time(self):
        self._age_queue_entry(400)

        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertTrue(response.data["fallback_offer"]["available"])
        self.assertGreaterEqual(response.data["fallback_offer"]["waited_seconds"], 400)

        assignment = DialogueEntryAssignment.objects.get(
            user=self.participant, topic_id=102
        )
        self.assertIsNotNone(assignment.fallback_offered_at)

    def test_offer_respects_configured_timeout(self):
        from api.models import PlatformDisplaySetting

        setting = PlatformDisplaySetting.load()
        setting.match_fallback_timeout_minutes = 1
        setting.save()

        self._age_queue_entry(90)
        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertTrue(response.data["fallback_offer"]["available"])
        self.assertEqual(response.data["fallback_offer"]["timeout_seconds"], 60)

    def test_split_mode_gets_no_offer(self):
        from api.models import PlatformDisplaySetting

        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        self._age_queue_entry(400)
        response = self.client.get("/api/matching/status/?topic_id=102")

        self.assertIsNone(response.data["fallback_offer"])

