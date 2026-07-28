"""
pytest tests for GET/POST /api/issues/<issue_id>/reactions/

Run from backend/:
    pytest api/tests_issue_reactions.py -v
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.models import Issue, IssueReaction

User = get_user_model()


@pytest.mark.django_db
def test_react_creates_reaction_and_get_reflects_it():
    author = User.objects.create_user(username="author", password="pw")
    reader = User.objects.create_user(username="reader", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=reader)

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": 3}, format="json"
    )

    assert response.status_code == 200
    assert response.data["counts"] == {"3": 1}
    assert response.data["mine"] == 3


@pytest.mark.django_db
def test_react_again_overwrites_previous_choice_not_duplicates():
    author = User.objects.create_user(username="author", password="pw")
    reader = User.objects.create_user(username="reader", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=reader)
    client.post(f"/api/issues/{issue.id}/reactions/", {"emoji_index": 1}, format="json")

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": 4}, format="json"
    )

    assert response.status_code == 200
    assert response.data["counts"] == {"4": 1}
    assert IssueReaction.objects.filter(issue=issue, reactor=reader).count() == 1


@pytest.mark.django_db
def test_react_rejects_out_of_range_emoji_index():
    author = User.objects.create_user(username="author", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=author)

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": 9}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_reactions_404_for_missing_issue():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/issues/999999/reactions/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_reactions_requires_authentication():
    author = User.objects.create_user(username="author", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")

    response = APIClient().get(f"/api/issues/{issue.id}/reactions/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_counts_aggregate_multiple_readers():
    author = User.objects.create_user(username="author", password="pw")
    r1 = User.objects.create_user(username="r1", password="pw")
    r2 = User.objects.create_user(username="r2", password="pw")
    r3 = User.objects.create_user(username="r3", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")

    for reader, idx in ((r1, 2), (r2, 2), (r3, 0)):
        client = APIClient()
        client.force_authenticate(user=reader)
        client.post(f"/api/issues/{issue.id}/reactions/", {"emoji_index": idx}, format="json")

    client = APIClient()
    client.force_authenticate(user=r1)
    response = client.get(f"/api/issues/{issue.id}/reactions/")

    assert response.status_code == 200
    assert response.data["counts"] == {"0": 1, "2": 2}
    assert response.data["mine"] == 2


@pytest.mark.django_db
def test_mine_is_null_when_reader_has_not_reacted():
    author = User.objects.create_user(username="author", password="pw")
    reader = User.objects.create_user(username="reader", password="pw")
    other = User.objects.create_user(username="other", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    IssueReaction.objects.create(issue=issue, reactor=other, emoji_index=1)
    client = APIClient()
    client.force_authenticate(user=reader)

    response = client.get(f"/api/issues/{issue.id}/reactions/")

    assert response.status_code == 200
    assert response.data["mine"] is None
    assert response.data["counts"] == {"1": 1}


@pytest.mark.django_db
def test_react_rejects_non_integer_emoji_index():
    author = User.objects.create_user(username="author", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=author)

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": "3"}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_post_404_for_missing_issue():
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/issues/999999/reactions/", {"emoji_index": 1}, format="json"
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_react_rejects_boolean_emoji_index():
    """bool 是 int 的子類別，JSON 的 true 不擋就會變成 emoji_index=1。
    這是驗證條件裡 isinstance(..., bool) 那一句存在的唯一理由——
    沒有這個測試，未來有人「簡化」掉那句，測試還是全綠。"""
    author = User.objects.create_user(username="author", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=author)

    for value in (True, False):
        response = client.post(
            f"/api/issues/{issue.id}/reactions/",
            {"emoji_index": value},
            format="json",
        )
        assert response.status_code == 400, f"{value!r} 應該被拒絕"
    assert not IssueReaction.objects.filter(issue=issue).exists()


@pytest.mark.django_db
def test_react_rejects_negative_emoji_index():
    """-1 若沒在 view 擋掉，會一路撞到 PositiveSmallIntegerField 變成 500。"""
    author = User.objects.create_user(username="author", password="pw")
    issue = Issue.objects.create(author=author, title="核能", body="...")
    client = APIClient()
    client.force_authenticate(user=author)

    response = client.post(
        f"/api/issues/{issue.id}/reactions/", {"emoji_index": -1}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_reactions_are_isolated_per_issue():
    """對 A 議題按表情不能影響 B 議題的統計。counts/mine 都有 issue_id 過濾，
    但沒有測試釘住的話，過濾條件被拿掉不會有人發現。"""
    author = User.objects.create_user(username="author", password="pw")
    reader = User.objects.create_user(username="reader", password="pw")
    issue_a = Issue.objects.create(author=author, title="核能", body="...")
    issue_b = Issue.objects.create(author=author, title="兵役", body="...")
    client = APIClient()
    client.force_authenticate(user=reader)

    client.post(f"/api/issues/{issue_a.id}/reactions/", {"emoji_index": 2}, format="json")

    response_b = client.get(f"/api/issues/{issue_b.id}/reactions/")
    assert response_b.status_code == 200
    assert response_b.data["counts"] == {}
    assert response_b.data["mine"] is None

    response_a = client.get(f"/api/issues/{issue_a.id}/reactions/")
    assert response_a.data["counts"] == {"2": 1}
    assert response_a.data["mine"] == 2
