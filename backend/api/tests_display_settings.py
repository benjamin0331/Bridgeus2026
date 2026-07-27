"""Supervisor 顯示設定（覆寫層）。

TOPIC_CONFIGS / SURVEY_CONFIGS 仍是議題內容的真實來源，這裡的 model 只存
被 Supervisor 改過的值；沒有對應列或欄位為 null＝沿用程式碼預設值。
見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md
"""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import PlatformDisplaySetting, TopicDisplayOverride


class PlatformDisplaySettingModelTests(TestCase):
    def test_load_creates_singleton_with_defaults(self):
        setting = PlatformDisplaySetting.load()

        self.assertEqual(setting.pk, 1)
        self.assertEqual(
            setting.participant_entry_mode, PlatformDisplaySetting.EntryMode.MIXED
        )
        self.assertEqual(
            setting.researcher_entry_mode, PlatformDisplaySetting.EntryMode.SPLIT
        )
        self.assertEqual(setting.match_fallback_timeout_minutes, 5)

    def test_load_is_idempotent(self):
        first = PlatformDisplaySetting.load()
        first.match_fallback_timeout_minutes = 9
        first.save()

        second = PlatformDisplaySetting.load()

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.match_fallback_timeout_minutes, 9)
        self.assertEqual(PlatformDisplaySetting.objects.count(), 1)

    def test_save_forces_single_row(self):
        PlatformDisplaySetting.load()
        extra = PlatformDisplaySetting(match_fallback_timeout_minutes=42)

        extra.save()

        self.assertEqual(PlatformDisplaySetting.objects.count(), 1)
        self.assertEqual(
            PlatformDisplaySetting.load().match_fallback_timeout_minutes, 42
        )

    def test_saving_a_fresh_instance_overwrites_other_fields(self):
        """直接建構新實例存檔會把沒帶到的欄位寫回預設值——這是強制 pk=1 的
        已知代價，測試把它釘住，避免有人誤以為那是部分更新。"""
        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        PlatformDisplaySetting(match_fallback_timeout_minutes=42).save()

        reloaded = PlatformDisplaySetting.load()
        self.assertEqual(reloaded.match_fallback_timeout_minutes, 42)
        self.assertEqual(
            reloaded.participant_entry_mode, PlatformDisplaySetting.EntryMode.MIXED
        )


class TopicDisplayOverrideModelTests(TestCase):
    def test_defaults_are_visible_and_thresholds_null(self):
        override = TopicDisplayOverride.objects.create(topic_id=102)

        self.assertTrue(override.visible_to_participant)
        self.assertTrue(override.visible_to_researcher)
        self.assertIsNone(override.support_threshold)
        self.assertIsNone(override.oppose_threshold)

    def test_topic_id_is_unique(self):
        from django.db import IntegrityError

        TopicDisplayOverride.objects.create(topic_id=102)

        with self.assertRaises(IntegrityError):
            TopicDisplayOverride.objects.create(topic_id=102)


class UserIsResearcherHelperTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        self.group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="ds_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(self.group)
        self.participant = User.objects.create_user(
            username="ds_participant", password="pw-strong-12345"
        )

    def test_group_member_is_researcher(self):
        from api.permissions import user_is_researcher

        self.assertTrue(user_is_researcher(self.researcher))

    def test_plain_user_is_not_researcher(self):
        from api.permissions import user_is_researcher

        self.assertFalse(user_is_researcher(self.participant))

    def test_none_is_not_researcher(self):
        from api.permissions import user_is_researcher

        self.assertFalse(user_is_researcher(None))

    def test_anonymous_user_is_not_researcher(self):
        from django.contrib.auth.models import AnonymousUser

        from api.permissions import user_is_researcher

        self.assertFalse(user_is_researcher(AnonymousUser()))


class StanceThresholdOverlayTests(TestCase):
    """議題 102 在 SURVEY_CONFIGS 的預設門檻是 support=4.5 / oppose=3.5。"""

    def test_defaults_when_no_override_row(self):
        from api.display_settings import get_stance_thresholds

        self.assertEqual(get_stance_thresholds(topic_id=102), (4.5, 3.5))

    def test_override_row_with_null_thresholds_keeps_defaults(self):
        from api.display_settings import get_stance_thresholds

        TopicDisplayOverride.objects.create(topic_id=102)

        self.assertEqual(get_stance_thresholds(topic_id=102), (4.5, 3.5))

    def test_full_override_wins(self):
        from api.display_settings import get_stance_thresholds

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.0, oppose_threshold=3.0
        )

        self.assertEqual(get_stance_thresholds(topic_id=102), (5.0, 3.0))

    def test_partial_override_keeps_other_side_default(self):
        from api.display_settings import get_stance_thresholds

        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=5.5)

        self.assertEqual(get_stance_thresholds(topic_id=102), (5.5, 3.5))

    def test_default_thresholds_ignores_override(self):
        from api.display_settings import default_stance_thresholds

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=6.0, oppose_threshold=2.0
        )

        self.assertEqual(default_stance_thresholds(topic_id=102), (4.5, 3.5))


class TopicVisibilityTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="vis_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(group)
        self.participant = User.objects.create_user(
            username="vis_participant", password="pw-strong-12345"
        )

    def test_all_topics_visible_without_overrides(self):
        from api.dialogue_topics import TOPIC_CONFIGS
        from api.display_settings import visible_topics

        for is_researcher in (True, False):
            ids = {topic["id"] for topic in visible_topics(is_researcher=is_researcher)}
            self.assertEqual(ids, set(TOPIC_CONFIGS))

    def test_hidden_from_participant_only(self):
        from api.display_settings import is_topic_visible, visible_topics

        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=True
        )

        participant_ids = {t["id"] for t in visible_topics(is_researcher=False)}
        researcher_ids = {t["id"] for t in visible_topics(is_researcher=True)}

        self.assertNotIn(102, participant_ids)
        self.assertIn(102, researcher_ids)
        self.assertFalse(is_topic_visible(topic_id=102, is_researcher=False))
        self.assertTrue(is_topic_visible(topic_id=102, is_researcher=True))

    def test_hidden_from_researcher_only(self):
        from api.display_settings import is_topic_visible, visible_topics

        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=True, visible_to_researcher=False
        )

        participant_ids = {t["id"] for t in visible_topics(is_researcher=False)}
        researcher_ids = {t["id"] for t in visible_topics(is_researcher=True)}

        self.assertIn(102, participant_ids)
        self.assertNotIn(102, researcher_ids)
        self.assertTrue(is_topic_visible(topic_id=102, is_researcher=False))
        self.assertFalse(is_topic_visible(topic_id=102, is_researcher=True))

    def test_unknown_topic_is_never_visible(self):
        from api.display_settings import is_topic_visible

        self.assertFalse(is_topic_visible(topic_id=999, is_researcher=True))

    def test_visible_topics_preserves_display_order(self):
        from api.dialogue_topics import get_dialogue_topics
        from api.display_settings import visible_topics

        expected = [topic["id"] for topic in get_dialogue_topics()]
        actual = [topic["id"] for topic in visible_topics(is_researcher=True)]

        self.assertEqual(actual, expected)


class EntryModeAndTimeoutTests(TestCase):
    def test_entry_mode_defaults_per_role(self):
        from api.display_settings import get_entry_mode

        self.assertEqual(get_entry_mode(is_researcher=False), "mixed")
        self.assertEqual(get_entry_mode(is_researcher=True), "split")

    def test_entry_mode_follows_setting(self):
        from api.display_settings import get_entry_mode

        setting = PlatformDisplaySetting.load()
        setting.participant_entry_mode = PlatformDisplaySetting.EntryMode.SPLIT
        setting.save()

        self.assertEqual(get_entry_mode(is_researcher=False), "split")

    def test_researcher_entry_mode_follows_setting(self):
        from api.display_settings import get_entry_mode

        setting = PlatformDisplaySetting.load()
        setting.researcher_entry_mode = PlatformDisplaySetting.EntryMode.MIXED
        setting.save()

        self.assertEqual(get_entry_mode(is_researcher=True), "mixed")
        self.assertEqual(get_entry_mode(is_researcher=False), "mixed")

    def test_fallback_timeout_seconds(self):
        from api.display_settings import get_match_fallback_timeout_seconds

        self.assertEqual(get_match_fallback_timeout_seconds(), 300)

        setting = PlatformDisplaySetting.load()
        setting.match_fallback_timeout_minutes = 2
        setting.save()

        self.assertEqual(get_match_fallback_timeout_seconds(), 120)


class ThresholdOverrideAffectsStanceCategoryTests(TestCase):
    """覆寫門檻後，立場分類的分界點要跟著移動。

    預設 support=4.5 / oppose=3.5：4.6 是 support、3.4 是 oppose、4.0 是 neutral。
    覆寫成 support=5.5 / oppose=2.5 後：4.6 與 3.4 都應變成 neutral。
    """

    def test_default_boundaries(self):
        from api.views import _resolve_stance_category

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "support"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=3.4), "oppose"
        )

    def test_override_moves_boundaries(self):
        from api.views import _resolve_stance_category

        TopicDisplayOverride.objects.create(
            topic_id=102, support_threshold=5.5, oppose_threshold=2.5
        )

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "neutral"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=3.4), "neutral"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=5.6), "support"
        )
        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=2.4), "oppose"
        )

    def test_override_takes_effect_without_restart(self):
        """_get_survey_scoring_config 不能加快取，否則改設定要重啟才生效。"""
        from api.views import _resolve_stance_category

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "support"
        )

        TopicDisplayOverride.objects.create(topic_id=102, support_threshold=5.5)

        self.assertEqual(
            _resolve_stance_category(topic_id=102, user_stance_score=4.6), "neutral"
        )


class DialogueTopicListVisibilityTests(APITestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        User = get_user_model()
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="topics_researcher", password="pw-strong-12345"
        )
        self.researcher.groups.add(group)
        self.participant = User.objects.create_user(
            username="topics_participant", password="pw-strong-12345"
        )

    def _topic_ids(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/dialogue/topics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["id"] for row in response.data}

    def test_both_roles_see_all_topics_by_default(self):
        from api.dialogue_topics import TOPIC_CONFIGS

        self.assertEqual(self._topic_ids(self.participant), set(TOPIC_CONFIGS))
        self.assertEqual(self._topic_ids(self.researcher), set(TOPIC_CONFIGS))

    def test_topic_hidden_from_participant_only(self):
        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=True
        )

        self.assertNotIn(102, self._topic_ids(self.participant))
        self.assertIn(102, self._topic_ids(self.researcher))

    def test_topic_hidden_from_everyone(self):
        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=False
        )

        self.assertNotIn(102, self._topic_ids(self.participant))
        self.assertNotIn(102, self._topic_ids(self.researcher))

    def test_role_filtering_works_through_real_jwt_auth(self):
        """用真的 access token 打，而不是 force_authenticate。

        force_authenticate 會直接塞 request.user、完全跳過 authentication_classes，
        所以上面那些測試驗證得到過濾邏輯，卻驗證不到「這個 view 必須用
        JWTAuthentication」這件事。若有人把 JWTStatelessUserAuthentication 加回去，
        它回傳的 TokenUser.groups 是 EmptyManager，user_is_researcher() 會永遠是
        False，研究者就會被當成一般使用者而看不到這個議題——這個測試就是為了在
        那種情況下失敗。
        """
        from rest_framework_simplejwt.tokens import AccessToken

        TopicDisplayOverride.objects.create(
            topic_id=102, visible_to_participant=False, visible_to_researcher=True
        )

        token = str(AccessToken.for_user(self.researcher))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/api/dialogue/topics/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(102, {row["id"] for row in response.data})
