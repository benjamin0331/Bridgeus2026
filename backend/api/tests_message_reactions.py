"""
pytest tests for POST/GET /api/message-reactions/ — 讚/倒讚 on opponent messages.

Covers both dialogue modes and the toggle/remove + authorization rules:
  - AI: react to an AIConversation turn that has an ai_response
  - Match: react only to the *partner's* MatchMessage (not your own)
  - value 0 removes; re-value updates; GET lists the caller's own reactions

Run from backend/:
    pytest api/tests_message_reactions.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import (
    AIConversation,
    DialogueMatch,
    MatchMessage,
    MessageReaction,
)

User = get_user_model()


@pytest.fixture
def users(db):
    a = User.objects.create_user(username="react_a", password="pass1234!")
    b = User.objects.create_user(username="react_b", password="pass1234!")
    return a, b


@pytest.fixture
def client_a(users):
    client = APIClient()
    client.force_authenticate(user=users[0])
    return client


def _ai_turn(user, *, session_id="sess-react", topic_id=102, answered=True):
    return AIConversation.objects.create(
        user=user,
        session_id=session_id,
        topic_id=topic_id,
        user_prompt="核電安全嗎？",
        ai_response="從法規面來看…" if answered else "",
    )


def _match_with_messages(user_a, user_b, *, room_id="room-react"):
    match = DialogueMatch.objects.create(
        topic_id=102,
        user_a=user_a,
        user_b=user_b,
        user_a_score=7,
        user_b_score=1,
        room_id=room_id,
        status=DialogueMatch.Status.ACTIVE,
    )
    own = MatchMessage.objects.create(match=match, sender=user_a, content="我方發言")
    partner = MatchMessage.objects.create(match=match, sender=user_b, content="對方發言")
    return match, own, partner


@pytest.mark.django_db
class TestAIReactions:
    def test_like_ai_reply_creates_row(self, client_a, users):
        turn = _ai_turn(users[0])
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["value"] == 1
        row = MessageReaction.objects.get(target_type="ai", target_id=turn.id)
        assert row.value == 1
        assert row.topic_id == 102
        assert row.conversation_id == "sess-react"

    def test_switch_like_to_dislike_updates_same_row(self, client_a, users):
        turn = _ai_turn(users[0])
        client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": -1},
            format="json",
        )
        assert resp.status_code == 200
        assert MessageReaction.objects.filter(target_id=turn.id).count() == 1
        assert MessageReaction.objects.get(target_id=turn.id).value == -1

    def test_value_zero_removes_reaction(self, client_a, users):
        turn = _ai_turn(users[0])
        client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 0},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["value"] is None
        assert not MessageReaction.objects.filter(target_id=turn.id).exists()

    def test_unanswered_turn_rejected(self, client_a, users):
        turn = _ai_turn(users[0], answered=False)
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 404

    def test_cannot_react_to_another_users_turn(self, client_a, users):
        turn = _ai_turn(users[1])  # belongs to user b
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestMatchReactions:
    def test_like_partner_message(self, client_a, users):
        _, _own, partner = _match_with_messages(users[0], users[1])
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "match", "target_id": partner.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        row = MessageReaction.objects.get(target_type="match", target_id=partner.id)
        assert row.value == 1
        assert row.conversation_id == "room-react"
        assert row.topic_id == 102

    def test_cannot_react_to_own_message(self, client_a, users):
        _, own, _partner = _match_with_messages(users[0], users[1])
        resp = client_a.post(
            "/api/message-reactions/",
            {"target_type": "match", "target_id": own.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 404

    def test_non_participant_rejected(self, users, db):
        outsider = User.objects.create_user(username="react_c", password="pass1234!")
        _, _own, partner = _match_with_messages(users[0], users[1])
        client = APIClient()
        client.force_authenticate(user=outsider)
        resp = client.post(
            "/api/message-reactions/",
            {"target_type": "match", "target_id": partner.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestReactionListing:
    def test_get_lists_own_reactions_for_conversation(self, client_a, users):
        turn = _ai_turn(users[0], session_id="sess-x")
        other_turn = _ai_turn(users[0], session_id="sess-y")
        for t in (turn, other_turn):
            client_a.post(
                "/api/message-reactions/",
                {"target_type": "ai", "target_id": t.id, "value": 1},
                format="json",
            )
        resp = client_a.get(
            "/api/message-reactions/?target_type=ai&conversation_id=sess-x"
        )
        assert resp.status_code == 200
        target_ids = {r["target_id"] for r in resp.data["reactions"]}
        assert target_ids == {turn.id}

    def test_get_requires_valid_target_type(self, client_a):
        resp = client_a.get("/api/message-reactions/?target_type=bogus")
        assert resp.status_code == 400

    def test_reaction_requires_auth(self, users):
        turn = _ai_turn(users[0])
        client = APIClient()
        resp = client.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        assert resp.status_code == 401
