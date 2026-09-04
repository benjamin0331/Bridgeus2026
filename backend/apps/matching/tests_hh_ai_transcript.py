"""H-H 介入提示的 transcript 不可洩漏使用者身分。

離題／僵局提示是 LLM 生成的，prompt 裡怎麼標示發言者，模型就會照抄進提示
文字裡。舊版用 `User {sender_id}`，參與者因此會在提示中看到「USER 3」這種
內部主鍵。這裡把 transcript 的標示鎖成配對房的匿名代號。
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.models import DialogueMatch, MatchMessage
from apps.matching.services.anonymity import assign_anonymous_ids
from apps.matching.services.hh_ai import (
    redirect_match_to_topic,
    suggest_match_direction,
)


class MatchTranscriptAnonymityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(username="hh-ai-a")
        self.user_b = user_model.objects.create_user(username="hh-ai-b")
        self.match = DialogueMatch.objects.create(
            topic_id=103,
            user_a=self.user_a,
            user_b=self.user_b,
            room_id="hh-ai-transcript-room",
        )
        self.anon_ids = assign_anonymous_ids(
            self.match.room_id, [self.user_a.id, self.user_b.id]
        )
        MatchMessage.objects.create(
            match=self.match, sender=self.user_a, content="核電廠的除役成本很高"
        )
        MatchMessage.objects.create(
            match=self.match, sender=self.user_b, content="但是缺電更麻煩"
        )

    def _captured_prompt(self, func):
        with patch(
            "apps.matching.services.hh_ai._call_claude", return_value="提示"
        ) as call:
            func(self.match.id, "核能發電")
        self.assertTrue(call.called)
        return call.call_args.args[0]

    def test_redirect_prompt_uses_anonymous_ids(self):
        prompt = self._captured_prompt(redirect_match_to_topic)
        self.assertIn(self.anon_ids[self.user_a.id], prompt)
        self.assertIn(self.anon_ids[self.user_b.id], prompt)

    def test_direction_prompt_uses_anonymous_ids(self):
        prompt = self._captured_prompt(suggest_match_direction)
        self.assertIn(self.anon_ids[self.user_a.id], prompt)
        self.assertIn(self.anon_ids[self.user_b.id], prompt)

    def test_prompts_never_expose_user_primary_keys(self):
        for func in (redirect_match_to_topic, suggest_match_direction):
            prompt = self._captured_prompt(func)
            self.assertNotIn(f"User {self.user_a.id}", prompt)
            self.assertNotIn(f"User {self.user_b.id}", prompt)
            self.assertNotIn(self.user_a.username, prompt)
            self.assertNotIn(self.user_b.username, prompt)

    def test_prompts_tell_the_model_not_to_name_participants(self):
        for func in (redirect_match_to_topic, suggest_match_direction):
            prompt = self._captured_prompt(func)
            self.assertIn("不要提及任何參與者的名稱或代號", prompt)

    def test_transcript_falls_back_when_the_match_row_is_missing(self):
        """訊息查得到、match 查不到時仍不能落回主鍵標示。"""
        with patch(
            "apps.matching.services.hh_ai._room_anonymous_ids", return_value={}
        ):
            prompt = self._captured_prompt(redirect_match_to_topic)
        self.assertNotIn(f"User {self.user_a.id}", prompt)
        self.assertIn("參與者", prompt)
