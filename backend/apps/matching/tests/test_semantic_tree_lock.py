from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction as django_transaction
from django.test import TestCase
from django.utils import timezone

from api.models import AIConversation, DialogueMatch, DialogueSessionRecord, MatchMessage
from apps.matching.services.semantic_tree import (
    OWNER_AI_USER,
    OWNER_USER_A,
    OWNER_USER_B,
    SEMANTIC_TREE_STATS_KEY,
    analyze_pending_ai_conversations,
    analyze_pending_room_messages,
    get_ai_semantic_tree_state,
    get_semantic_tree_state,
    save_semantic_tree_state,
)


def _fake_items(point_name="核廢長期負擔"):
    return {
        "items": [
            {
                "claimText": "核廢料處理會帶來長期負擔",
                "anchorId": "anchor_waste",
                "path": ["長期處置"],
                "pointName": point_name,
                "stance": "反對",
                "confidence": 0.86,
                "rationale": "test",
            }
        ],
        "invalidItems": [],
        "model": "test-model",
    }


class SemanticTreeLockTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="alice", password="secret123")
        self.other_user = User.objects.create_user(username="bob", password="secret123")
        self.match = DialogueMatch.objects.create(
            topic_id=102,
            user_a=self.user,
            user_b=self.other_user,
            user_a_score=7,
            user_b_score=1,
            room_id="room-lock-test",
            stats={},
        )

    def _create_session(self, *, session_id="session-lock-test"):
        return DialogueSessionRecord.objects.create(
            user=self.user,
            session_id=session_id,
            topic_id=102,
            topic_title="台灣核能議題討論",
            collection_name="nuclear_energy_all",
            session_state={"history": ["original"]},
            semantic_tree_state={},
            last_activity_at=timezone.now(),
        )

    def test_room_llm_call_runs_outside_transaction(self):
        MatchMessage.objects.create(
            match=self.match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )

        atomic_depth = 0
        real_atomic = django_transaction.atomic

        def tracked_atomic(*args, **kwargs):
            real_context = real_atomic(*args, **kwargs)

            class TrackedAtomic:
                def __enter__(self):
                    nonlocal atomic_depth
                    atomic_depth += 1
                    return real_context.__enter__()

                def __exit__(self, exc_type, exc, traceback):
                    nonlocal atomic_depth
                    try:
                        return real_context.__exit__(exc_type, exc, traceback)
                    finally:
                        atomic_depth -= 1

            return TrackedAtomic()

        def fake_analyze(**kwargs):
            self.assertEqual(atomic_depth, 0)
            return _fake_items()

        with patch("apps.matching.services.semantic_tree.transaction.atomic", tracked_atomic), patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            side_effect=fake_analyze,
        ):
            payload = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )

        self.assertEqual(payload["analyzedCount"], 1)

    def test_ai_llm_call_runs_outside_transaction(self):
        record = self._create_session()
        turn = AIConversation.objects.create(
            user=self.user,
            session_id=record.session_id,
            topic_id=102,
            user_prompt="核廢料處理會帶來長期負擔",
            ai_response="AI 回覆",
        )
        session_record = {
            "user_id": self.user.id,
            "session_id": record.session_id,
            "topic_id": record.topic_id,
            SEMANTIC_TREE_STATS_KEY: {},
        }

        atomic_depth = 0
        real_atomic = django_transaction.atomic

        def tracked_atomic(*args, **kwargs):
            real_context = real_atomic(*args, **kwargs)

            class TrackedAtomic:
                def __enter__(self):
                    nonlocal atomic_depth
                    atomic_depth += 1
                    return real_context.__enter__()

                def __exit__(self, exc_type, exc, traceback):
                    nonlocal atomic_depth
                    try:
                        return real_context.__exit__(exc_type, exc, traceback)
                    finally:
                        atomic_depth -= 1

            return TrackedAtomic()

        def fake_analyze(**kwargs):
            self.assertEqual(atomic_depth, 0)
            return _fake_items()

        with patch("apps.matching.services.semantic_tree.transaction.atomic", tracked_atomic), patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            side_effect=fake_analyze,
        ):
            payload = analyze_pending_ai_conversations(
                session_record=session_record,
                session_id=record.session_id,
                user_id=self.user.id,
                root_name="台灣核能議題討論",
            )

        self.assertEqual(payload["analyzedCount"], 1)
        self.assertIn(str(turn.id), payload["analyzedSourceIds"])

    def test_room_skips_already_analyzed_source_without_duplicate_llm_call(self):
        message = MatchMessage.objects.create(
            match=self.match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )

        with patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            return_value=_fake_items(),
        ) as mocked_analyze:
            first = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )
            second = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )

        self.match.refresh_from_db()
        state = get_semantic_tree_state(self.match, root_name="台灣核能議題討論")
        owner_state = state["participants"][OWNER_USER_A]
        self.assertEqual(first["analyzedCount"], 1)
        self.assertEqual(second["analyzedCount"], 0)
        self.assertEqual(mocked_analyze.call_count, 1)
        self.assertEqual(owner_state["analyzedSourceIds"].count(str(message.id)), 1)
        self.assertEqual(
            [
                entry["sourceId"]
                for entry in owner_state["analysisHistory"]
                if entry["sourceId"] == str(message.id)
            ],
            [str(message.id)],
        )

    def test_active_claim_returns_in_progress_without_llm_call(self):
        message = MatchMessage.objects.create(
            match=self.match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )
        state = get_semantic_tree_state(self.match, root_name="台灣核能議題討論")
        state["participants"][OWNER_USER_A]["pendingClaims"] = {
            str(message.id): timezone.now().isoformat()
        }
        save_semantic_tree_state(self.match, state)

        with patch("apps.matching.services.semantic_tree.analyze_text_for_tree") as mocked_analyze:
            payload = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )

        self.assertEqual(payload["analysisStatus"], "in_progress")
        mocked_analyze.assert_not_called()

    def test_expired_claim_can_be_reclaimed(self):
        message = MatchMessage.objects.create(
            match=self.match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )
        state = get_semantic_tree_state(self.match, root_name="台灣核能議題討論")
        state["participants"][OWNER_USER_A]["pendingClaims"] = {
            str(message.id): (timezone.now() - timedelta(seconds=151)).isoformat()
        }
        save_semantic_tree_state(self.match, state)

        with patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            return_value=_fake_items(),
        ) as mocked_analyze:
            payload = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )

        self.assertEqual(payload["analyzedCount"], 1)
        self.assertEqual(mocked_analyze.call_count, 1)

    def test_room_conflict_discards_batch_and_clears_claim(self):
        message = MatchMessage.objects.create(
            match=self.match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )

        def fake_analyze(**kwargs):
            concurrent_match = DialogueMatch.objects.get(pk=self.match.pk)
            state = get_semantic_tree_state(concurrent_match, root_name="台灣核能議題討論")
            state["participants"][OWNER_USER_A]["analyzedSourceIds"].append("external-source")
            save_semantic_tree_state(concurrent_match, state)
            return _fake_items()

        with patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            side_effect=fake_analyze,
        ):
            payload = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )

        self.match.refresh_from_db()
        state = get_semantic_tree_state(self.match, root_name="台灣核能議題討論")
        owner_state = state["participants"][OWNER_USER_A]
        self.assertEqual(payload["analysisStatus"], "conflict_retry")
        self.assertEqual(payload["analyzedCount"], 0)
        self.assertIn("external-source", owner_state["analyzedSourceIds"])
        self.assertNotIn(str(message.id), owner_state["analyzedSourceIds"])
        self.assertEqual(owner_state["pendingClaims"], {})

    def test_cross_owner_update_is_preserved_and_not_a_conflict(self):
        message = MatchMessage.objects.create(
            match=self.match,
            sender=self.user,
            content="核廢料處理會帶來長期負擔",
        )

        def fake_analyze(**kwargs):
            concurrent_match = DialogueMatch.objects.get(pk=self.match.pk)
            state = get_semantic_tree_state(concurrent_match, root_name="台灣核能議題討論")
            partner_state = state["participants"][OWNER_USER_B]
            partner_state["analyzedSourceIds"].append("partner-source")
            partner_state["analysisHistory"].append({"sourceId": "partner-source"})
            save_semantic_tree_state(concurrent_match, state)
            return _fake_items()

        with patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            side_effect=fake_analyze,
        ):
            payload = analyze_pending_room_messages(
                match=self.match,
                root_name="台灣核能議題討論",
                current_user_id=self.user.id,
            )

        self.match.refresh_from_db()
        state = get_semantic_tree_state(self.match, root_name="台灣核能議題討論")
        self.assertEqual(payload["analysisStatus"], "ready")
        self.assertEqual(payload["analyzedCount"], 1)
        self.assertIn(str(message.id), state["participants"][OWNER_USER_A]["analyzedSourceIds"])
        self.assertIn("partner-source", state["participants"][OWNER_USER_B]["analyzedSourceIds"])

    def test_ai_writeback_preserves_session_state_and_invalidates_cache(self):
        record = self._create_session()
        turn = AIConversation.objects.create(
            user=self.user,
            session_id=record.session_id,
            topic_id=102,
            user_prompt="核廢料處理會帶來長期負擔",
            ai_response="AI 回覆",
        )
        cache.set(f"dialogue_session:{record.session_id}", {"stale": True})
        session_record = {
            "user_id": self.user.id,
            "session_id": record.session_id,
            "topic_id": record.topic_id,
            SEMANTIC_TREE_STATS_KEY: {},
        }

        def fake_analyze(**kwargs):
            concurrent_record = DialogueSessionRecord.objects.get(pk=record.pk)
            concurrent_record.session_state = {"history": ["concurrent"]}
            concurrent_record.save(update_fields=["session_state", "updated_at"])
            return _fake_items()

        with patch(
            "apps.matching.services.semantic_tree.analyze_text_for_tree",
            side_effect=fake_analyze,
        ):
            payload = analyze_pending_ai_conversations(
                session_record=session_record,
                session_id=record.session_id,
                user_id=self.user.id,
                root_name="台灣核能議題討論",
            )

        record.refresh_from_db()
        state = get_ai_semantic_tree_state(
            {
                "topic_id": record.topic_id,
                SEMANTIC_TREE_STATS_KEY: record.semantic_tree_state,
            },
            root_name="台灣核能議題討論",
        )
        self.assertEqual(payload["analyzedCount"], 1)
        self.assertEqual(record.session_state, {"history": ["concurrent"]})
        self.assertIn(str(turn.id), state["participants"][OWNER_AI_USER]["analyzedSourceIds"])
        self.assertIsNone(cache.get(f"dialogue_session:{record.session_id}"))
