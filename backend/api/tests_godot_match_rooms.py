"""
pytest tests for POST /api/godot/match-rooms/

Run from backend/:
    pytest api/tests_godot_match_rooms.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from api.models import DialogueMatch

User = get_user_model()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_creates_active_dialogue_match():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 201
    assert response.data["redirect_url"] == "/topic/102?mode=match"
    match = DialogueMatch.objects.get(room_id=response.data["room_id"])
    assert match.status == DialogueMatch.Status.ACTIVE
    assert match.matching_algorithm_version == "godot_manual"
    assert {match.user_a_id, match.user_b_id} == {user_a.id, user_b.id}


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_wrong_token():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="wrong",
    )

    assert response.status_code == 403
    assert not DialogueMatch.objects.exists()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="")
def test_match_room_fails_closed_when_token_unconfigured():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="",
    )

    assert response.status_code == 403
    assert not DialogueMatch.objects.exists()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_unknown_topic_id():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nope", "topic_id": 999, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 400
    assert not DialogueMatch.objects.exists()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_same_user_twice():
    user_a = User.objects.create_user(username="a", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, user_a.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 400
    assert not DialogueMatch.objects.exists()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_rejects_wrong_user_ids_shape():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    for bad in ([user_a.id], [user_a.id, user_b.id, 3], "not-a-list", None, []):
        response = client.post(
            "/api/godot/match-rooms/",
            {"topic": "nuclear_energy", "topic_id": 102, "user_ids": bad},
            format="json",
            HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
        )
        assert response.status_code == 400, f"{bad!r} should be rejected"
    assert not DialogueMatch.objects.exists()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_404_for_nonexistent_user():
    user_a = User.objects.create_user(username="a", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "nuclear_energy", "topic_id": 102, "user_ids": [user_a.id, 999999]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 404
    assert not DialogueMatch.objects.exists()


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_works_for_second_topic():
    user_a = User.objects.create_user(username="a", password="pw")
    user_b = User.objects.create_user(username="b", password="pw")
    client = APIClient()

    response = client.post(
        "/api/godot/match-rooms/",
        {"topic": "women_soldier", "topic_id": 103, "user_ids": [user_a.id, user_b.id]},
        format="json",
        HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
    )

    assert response.status_code == 201
    assert response.data["redirect_url"] == "/topic/103?mode=match"
    assert DialogueMatch.objects.get(room_id=response.data["room_id"]).topic_id == 103


@pytest.mark.django_db
@override_settings(GODOT_SERVICE_TOKEN="test-token-123")
def test_match_room_ids_are_unique_across_calls():
    users = [User.objects.create_user(username=f"u{i}", password="pw") for i in range(4)]
    client = APIClient()
    room_ids = set()

    for pair in ((0, 1), (2, 3)):
        response = client.post(
            "/api/godot/match-rooms/",
            {
                "topic": "nuclear_energy",
                "topic_id": 102,
                "user_ids": [users[pair[0]].id, users[pair[1]].id],
            },
            format="json",
            HTTP_X_GODOT_SERVICE_TOKEN="test-token-123",
        )
        assert response.status_code == 201
        room_ids.add(response.data["room_id"])

    assert len(room_ids) == 2
