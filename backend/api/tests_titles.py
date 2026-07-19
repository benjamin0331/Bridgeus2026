"""
pytest tests for GET/POST /api/titles/me/

Run from backend/:
    pytest api/tests_titles.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.models import Title, UserTitle

User = get_user_model()


@pytest.mark.django_db
def test_titles_me_empty_when_no_titles_owned():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/titles/me/")

    assert response.status_code == 200
    assert response.data == {"owned": [], "selected_id": None, "color": None}


@pytest.mark.django_db
def test_titles_me_lists_owned_and_selected():
    user = User.objects.create_user(username="u1", password="pw")
    t1 = Title.objects.create(name="探索者")
    t2 = Title.objects.create(name="傾聽者", color="#112233")
    UserTitle.objects.create(user=user, title=t1)
    UserTitle.objects.create(user=user, title=t2, is_selected=True)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/titles/me/")

    assert response.status_code == 200
    assert response.data["selected_id"] == t2.id
    assert response.data["color"] == "#112233"
    assert {row["id"] for row in response.data["owned"]} == {t1.id, t2.id}


@pytest.mark.django_db
def test_set_title_rejects_unowned_title():
    user = User.objects.create_user(username="u1", password="pw")
    other_title = Title.objects.create(name="不屬於我")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/titles/me/", {"title_id": other_title.id}, format="json")

    assert response.status_code == 403
    assert not UserTitle.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_set_title_selects_owned_title_and_overwrites_color():
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者")
    UserTitle.objects.create(user=user, title=title)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/titles/me/", {"title_id": title.id, "color": "#abcdef"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["selected_id"] == title.id
    assert response.data["color"] == "#abcdef"


@pytest.mark.django_db
def test_set_title_null_clears_selection():
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者")
    UserTitle.objects.create(user=user, title=title, is_selected=True)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/titles/me/", {"title_id": None}, format="json")

    assert response.status_code == 200
    assert response.data["selected_id"] is None
