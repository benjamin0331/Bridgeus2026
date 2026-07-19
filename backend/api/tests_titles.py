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
    UserTitle.objects.create(user=user, title=title, color="#000000")
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


@pytest.mark.django_db
def test_switching_titles_leaves_exactly_one_selected():
    """A → B 切換：這是 post() 裡 clear-then-set 順序存在的理由——UserTitle 有
    partial unique constraint（user + is_selected=True），順序反了就會撞 DB。"""
    user = User.objects.create_user(username="u1", password="pw")
    a = Title.objects.create(name="A")
    b = Title.objects.create(name="B")
    UserTitle.objects.create(user=user, title=a, is_selected=True)
    UserTitle.objects.create(user=user, title=b)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/titles/me/", {"title_id": b.id}, format="json")

    assert response.status_code == 200
    assert response.data["selected_id"] == b.id
    selected = UserTitle.objects.filter(user=user, is_selected=True)
    assert selected.count() == 1
    assert selected.first().title_id == b.id


@pytest.mark.django_db
def test_missing_title_id_is_rejected_not_treated_as_clear():
    """只帶 color 不帶 title_id 不能把既有選擇清掉。"""
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者")
    UserTitle.objects.create(user=user, title=title, is_selected=True)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/titles/me/", {"color": "#abcdef"}, format="json")

    assert response.status_code == 400
    assert UserTitle.objects.get(user=user, title=title).is_selected is True


@pytest.mark.django_db
def test_invalid_color_rejected():
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者")
    UserTitle.objects.create(user=user, title=title)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/titles/me/",
        {"title_id": title.id, "color": "not-a-hex-color"},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_user_title_color_wins_over_title_default_color():
    user = User.objects.create_user(username="u1", password="pw")
    title = Title.objects.create(name="探索者", color="#000000")
    UserTitle.objects.create(user=user, title=title, is_selected=True, color="#ffffff")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/titles/me/")

    assert response.status_code == 200
    assert response.data["color"] == "#ffffff"


@pytest.mark.django_db
def test_titles_me_requires_authentication():
    response = APIClient().get("/api/titles/me/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_selecting_title_does_not_affect_other_users():
    user_a = User.objects.create_user(username="ua", password="pw")
    user_b = User.objects.create_user(username="ub", password="pw")
    title = Title.objects.create(name="共同頭銜")
    UserTitle.objects.create(user=user_a, title=title, is_selected=True)
    ut_b = UserTitle.objects.create(user=user_b, title=title)
    client = APIClient()
    client.force_authenticate(user=user_b)

    client.post("/api/titles/me/", {"title_id": title.id}, format="json")

    assert UserTitle.objects.get(pk=ut_b.pk).is_selected is True
    assert UserTitle.objects.get(user=user_a, title=title).is_selected is True
