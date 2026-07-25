"""殘留 AI 對話 session 的回填清理規則。

規則本體在 api/session_cleanup.py，由 migration 0018 與
manage.py close_stale_dialogue_sessions 共用。這裡固定住「哪些該關、哪些
絕對不能關」——關錯的話會把使用者正在進行的對話直接斷掉。
"""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api.models import DialogueSessionRecord, PostDialogueResponse
from api.session_cleanup import find_closable_session_ids

User = get_user_model()


def _closable():
    return set(
        find_closable_session_ids(
            DialogueSessionRecord=DialogueSessionRecord,
            PostDialogueResponse=PostDialogueResponse,
        )
    )


class FindClosableSessionsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="participant", password="pw")
        self.now = timezone.now()

    def _session(self, session_id, *, minutes_ago=0, topic_id=102, user=None):
        return DialogueSessionRecord.objects.create(
            user=user or self.user,
            session_id=session_id,
            topic_id=topic_id,
            topic_title="測試議題",
            collection_name="nuclear_energy_all",
            last_activity_at=self.now - timedelta(minutes=minutes_ago),
        )

    def _questionnaire_for(self, session_id, topic_id=102):
        return PostDialogueResponse.objects.create(
            user=self.user,
            topic_id=topic_id,
            session_id=session_id,
            experiment_condition="ai",
            **{f"post_likert_{i}": 4 for i in range(1, 9)},
            exp_stance_change_1=4,
            exp_stance_change_2=4,
            exp_quality_1=4,
            exp_quality_2=4,
            exp_reflection_1=4,
            exp_reflection_2=4,
            ccnd_attention=4,
            ccnd_awareness=4,
            ccnd_influence=4,
            opponent_judgment=2,
            post_open_comprehension="x" * 60,
        )

    def test_lone_unfinished_session_is_kept(self):
        """使用者還在進行、沒填問卷的對話絕對不能關。"""
        self._session("only_one")

        self.assertEqual(_closable(), set())

    def test_superseded_sessions_are_closable(self):
        self._session("newest", minutes_ago=0)
        self._session("older", minutes_ago=30)
        self._session("oldest", minutes_ago=60)

        self.assertEqual(_closable(), {"older", "oldest"})

    def test_newest_is_closable_once_questionnaire_submitted(self):
        self._session("newest", minutes_ago=0)
        self._session("older", minutes_ago=30)
        self._questionnaire_for("newest")

        self.assertEqual(_closable(), {"newest", "older"})

    def test_sessions_are_grouped_per_topic(self):
        self._session("topic102", topic_id=102)
        self._session("topic103", topic_id=103)

        self.assertEqual(_closable(), set())

    def test_sessions_are_grouped_per_user(self):
        stranger = User.objects.create_user(username="stranger", password="pw")
        self._session("mine")
        self._session("theirs", user=stranger)

        self.assertEqual(_closable(), set())

    def test_already_closed_sessions_are_ignored(self):
        self._session("newest", minutes_ago=0)
        stale = self._session("older", minutes_ago=30)
        stale.status = DialogueSessionRecord.Status.CLOSED
        stale.save(update_fields=["status"])

        self.assertEqual(_closable(), set())


class CloseStaleDialogueSessionsCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="participant", password="pw")
        now = timezone.now()
        for session_id, minutes_ago in (("newest", 0), ("older", 30)):
            DialogueSessionRecord.objects.create(
                user=self.user,
                session_id=session_id,
                topic_id=102,
                topic_title="測試議題",
                collection_name="nuclear_energy_all",
                last_activity_at=now - timedelta(minutes=minutes_ago),
            )

    def _status(self, session_id):
        return DialogueSessionRecord.objects.get(session_id=session_id).status

    def test_dry_run_does_not_write(self):
        call_command("close_stale_dialogue_sessions", "--dry-run", stdout=StringIO())

        self.assertEqual(self._status("older"), DialogueSessionRecord.Status.ACTIVE)

    def test_command_closes_superseded_session(self):
        call_command("close_stale_dialogue_sessions", stdout=StringIO())

        self.assertEqual(self._status("older"), DialogueSessionRecord.Status.CLOSED)
        self.assertEqual(self._status("newest"), DialogueSessionRecord.Status.ACTIVE)

    def test_command_clears_cache_so_closed_session_cannot_be_restored(self):
        cache.set("dialogue_session:older", {"user_id": self.user.id}, timeout=600)

        call_command("close_stale_dialogue_sessions", stdout=StringIO())

        self.assertIsNone(cache.get("dialogue_session:older"))

    def test_command_is_idempotent(self):
        call_command("close_stale_dialogue_sessions", stdout=StringIO())
        out = StringIO()
        call_command("close_stale_dialogue_sessions", stdout=out)

        self.assertIn("沒有需要關閉的 session", out.getvalue())
