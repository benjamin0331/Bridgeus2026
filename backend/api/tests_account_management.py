"""研究者帳號管理 REST API（前端設定頁用）。

權限一律 IsResearcher（「研究者」Django Group）。升/降研究者＝加入/移出該
Group，is_staff 由 api/signals.py 的 m2m_changed signal 自動連動。見
docs/superpowers/specs/2026-07-21-frontend-settings-account-management-design.md
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


def _make_researcher(username="researcher"):
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user = User.objects.create_user(username=username, password="pw-strong-123")
    user.groups.add(group)
    return user


class AccountListCreateTests(APITestCase):
    def setUp(self):
        self.researcher = _make_researcher()
        self.participant = User.objects.create_user(
            username="participant", password="pw-strong-123"
        )

    def test_participant_cannot_list_accounts(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.get("/api/accounts/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_researcher_can_list_accounts(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.get("/api/accounts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames = {row["username"] for row in response.data}
        self.assertIn("researcher", usernames)
        self.assertIn("participant", usernames)
        researcher_row = next(r for r in response.data if r["username"] == "researcher")
        self.assertTrue(researcher_row["is_researcher"])
        participant_row = next(r for r in response.data if r["username"] == "participant")
        self.assertFalse(participant_row["is_researcher"])

    def test_create_participant_account_defaults(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.post(
            "/api/accounts/",
            {"username": "newbie", "password": "pw-strong-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username="newbie")
        self.assertTrue(created.is_active)
        self.assertFalse(created.is_staff)
        self.assertFalse(created.groups.filter(name=RESEARCHER_GROUP_NAME).exists())

    def test_create_researcher_account_joins_group_and_gets_staff(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.post(
            "/api/accounts/",
            {"username": "newsup", "password": "pw-strong-123", "is_researcher": True},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username="newsup")
        self.assertTrue(created.groups.filter(name=RESEARCHER_GROUP_NAME).exists())
        self.assertTrue(created.is_staff)  # signal 連動

    def test_create_rejects_weak_password(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.post(
            "/api/accounts/", {"username": "weak", "password": "123"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_create_rejects_duplicate_username(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.post(
            "/api/accounts/",
            {"username": "participant", "password": "pw-strong-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
