"""BridgeUsTokenObtainPairView adds an is_researcher claim to the access token
so the frontend can decide whether to show researcher-only links (e.g.
/viewpoint-review) without an extra API round-trip. The claim reflects
membership in the "研究者" Django Group (api.permissions.RESEARCHER_GROUP_NAME),
not is_staff — the real access control stays server-side (IsResearcher).
"""

import jwt
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


def _decode_access_token(token: str) -> dict:
    # Signature verification isn't the point here (that's SimpleJWT's own
    # test suite's job) — just check the claim we added is present/correct.
    return jwt.decode(token, options={"verify_signature": False})


class TokenIsResearcherClaimTests(APITestCase):
    def test_researcher_group_member_token_carries_is_researcher_true(self):
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        user = User.objects.create_user(username="researcher", password="pw")
        user.groups.add(group)

        response = self.client.post(
            "/api/token/", {"username": "researcher", "password": "pw"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = _decode_access_token(response.data["access"])
        self.assertTrue(payload["is_researcher"])

    def test_regular_user_token_carries_is_researcher_false(self):
        User.objects.create_user(username="participant", password="pw")

        response = self.client.post(
            "/api/token/", {"username": "participant", "password": "pw"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = _decode_access_token(response.data["access"])
        self.assertFalse(payload["is_researcher"])

    def test_is_staff_alone_does_not_grant_is_researcher(self):
        # is_staff (Django admin access) and being in the 研究者 group are
        # deliberately independent now — a plain is_staff=True user without
        # group membership should NOT get is_researcher: true.
        User.objects.create_user(username="admin_only", password="pw", is_staff=True)

        response = self.client.post(
            "/api/token/", {"username": "admin_only", "password": "pw"}
        )

        payload = _decode_access_token(response.data["access"])
        self.assertFalse(payload["is_researcher"])
