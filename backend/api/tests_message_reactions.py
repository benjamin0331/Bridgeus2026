import pytest
from django.contrib.auth import get_user_model
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
    user_a = User.objects.create_user(username="react_a", password="pass1234!")
    user_b = User.objects.create_user(username="react_b", password="pass1234!")
    return user_a, user_b


@pytest.fixture
def client_a(users):
    client = APIClient()
    client.force_authenticate(user=users[0])
    return client


def _ai_turn(user, *, session_id="sess-react", answered=True):
    return AIConversation.objects.create(
        user=user,
        session_id=session_id,
        topic_id=102,
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
    )
    own = MatchMessage.objects.create(
        match=match,
        sender=user_a,
        content="我方發言",
    )
    partner = MatchMessage.objects.create(
        match=match,
        sender=user_b,
        content="對方發言",
    )
    return own, partner


@pytest.mark.django_db
class TestAIReactions:
    def test_create_update_and_remove_ai_reaction(self, client_a, users):
        turn = _ai_turn(users[0])

        created = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 1},
            format="json",
        )
        updated = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": -1},
            format="json",
        )
        removed = client_a.post(
            "/api/message-reactions/",
            {"target_type": "ai", "target_id": turn.id, "value": 0},
            format="json",
        )

        assert created.status_code == 200
        assert updated.status_code == 200
        assert removed.status_code == 200
        assert removed.data["value"] is None
        assert not MessageReaction.objects.filter(
            target_type="ai",
            target_id=turn.id,
        ).exists()

    def test_rejects_unanswered_or_another_users_turn(self, client_a, users):
        unanswered = _ai_turn(users[0], answered=False)
        another_users = _ai_turn(users[1], session_id="another-session")

        for turn in (unanswered, another_users):
            response = client_a.post(
                "/api/message-reactions/",
                {"target_type": "ai", "target_id": turn.id, "value": 1},
                format="json",
            )
            assert response.status_code == 404


@pytest.mark.django_db
class TestMatchReactions:
    def test_only_partner_message_is_reactable(self, client_a, users):
        own, partner = _match_with_messages(*users)

        own_response = client_a.post(
            "/api/message-reactions/",
            {"target_type": "match", "target_id": own.id, "value": 1},
            format="json",
        )
        partner_response = client_a.post(
            "/api/message-reactions/",
            {"target_type": "match", "target_id": partner.id, "value": 1},
            format="json",
        )

        assert own_response.status_code == 404
        assert partner_response.status_code == 200
        reaction = MessageReaction.objects.get(
            target_type="match",
            target_id=partner.id,
        )
        assert reaction.conversation_id == "room-react"
        assert reaction.topic_id == 102

    def test_non_participant_is_rejected(self, users):
        _, partner = _match_with_messages(*users)
        outsider = User.objects.create_user(username="react_outsider")
        client = APIClient()
        client.force_authenticate(user=outsider)

        response = client.post(
            "/api/message-reactions/",
            {"target_type": "match", "target_id": partner.id, "value": 1},
            format="json",
        )

        assert response.status_code == 404


@pytest.mark.django_db
class TestReactionListing:
    def test_lists_only_requested_conversation(self, client_a, users):
        expected = _ai_turn(users[0], session_id="sess-x")
        other = _ai_turn(users[0], session_id="sess-y")
        for turn in (expected, other):
            client_a.post(
                "/api/message-reactions/",
                {"target_type": "ai", "target_id": turn.id, "value": 1},
                format="json",
            )

        response = client_a.get(
            "/api/message-reactions/?target_type=ai&conversation_id=sess-x"
        )

        assert response.status_code == 200
        assert response.data["reactions"] == [
            {"target_id": expected.id, "value": 1}
        ]

    def test_requires_conversation_and_authentication(self, client_a, users):
        missing_conversation = client_a.get(
            "/api/message-reactions/?target_type=ai"
        )
        unauthenticated = APIClient().post(
            "/api/message-reactions/",
            {
                "target_type": "ai",
                "target_id": _ai_turn(users[0]).id,
                "value": 1,
            },
            format="json",
        )

        assert missing_conversation.status_code == 400
        assert unauthenticated.status_code == 401
