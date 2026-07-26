"""Supervisor 顯示設定（覆寫層）。

TOPIC_CONFIGS / SURVEY_CONFIGS 仍是議題內容的真實來源，這裡的 model 只存
被 Supervisor 改過的值；沒有對應列或欄位為 null＝沿用程式碼預設值。
見 docs/superpowers/specs/2026-07-27-supervisor-display-settings-and-mixed-entry-design.md
"""

from django.test import TestCase

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
