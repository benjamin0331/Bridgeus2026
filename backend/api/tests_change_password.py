"""一般使用者自助修改密碼（POST /api/me/password/）。

重點不只是「密碼有沒有換掉」，還有換掉之後舊 JWT 會不會失效——沒有
token_blacklist 的話，舊的 refresh token 還能再用 7 天，這個功能對
「我密碼可能外流了」這個使用情境就等於沒做。見 tests_token_revocation。
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()

OLD_PASSWORD = "Xk9$mVpq2Lz"
NEW_PASSWORD = "Qw7!nBrt4Hs"

URL = "/api/me/password/"


def _payload(old=OLD_PASSWORD, new=NEW_PASSWORD, confirm=None):
    return {
        "old_password": old,
        "new_password": new,
        "new_password_confirm": new if confirm is None else confirm,
    }


class ChangePasswordTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="participant", password=OLD_PASSWORD
        )
        self.client.force_authenticate(user=self.user)

    def test_changes_password_with_correct_old_password(self):
        response = self.client.post(URL, _payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_rejects_wrong_old_password(self):
        response = self.client.post(
            URL, _payload(old="not-my-password"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("old_password", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_rejects_confirm_mismatch(self):
        response = self.client.post(
            URL, _payload(confirm="Qw7!nBrt4Hx"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password_confirm", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_rejects_new_password_too_similar_to_username(self):
        # UserAttributeSimilarityValidator 只有拿得到 user 才會作用——這條
        # 釘住 serializer 真的把 request.user 傳進 dj_validate_password。
        response = self.client.post(
            URL, _payload(new="participant"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)

    def test_rejects_new_password_same_as_old(self):
        response = self.client.post(URL, _payload(new=OLD_PASSWORD), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(URL, _payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ChangePasswordTokenRevocationTests(APITestCase):
    """改完密碼，舊 token 必須失效、同時發一組新的給前端接手。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="participant", password=OLD_PASSWORD
        )

    def _login(self):
        response = self.client.post(
            "/api/token/",
            {"username": "participant", "password": OLD_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return response.data["access"], response.data["refresh"]

    def test_old_refresh_token_stops_working_after_change(self):
        _, old_refresh = self._login()
        self.client.force_authenticate(user=self.user)
        self.assertEqual(
            self.client.post(URL, _payload(), format="json").status_code, 200
        )

        self.client.force_authenticate(user=None)
        response = self.client.post(
            "/api/token/refresh/", {"refresh": old_refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_fresh_usable_tokens(self):
        self._login()
        self.client.force_authenticate(user=self.user)
        response = self.client.post(URL, _payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        self.client.force_authenticate(user=None)
        refreshed = self.client.post(
            "/api/token/refresh/",
            {"refresh": response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK)


class ResearcherResetRevokesTargetTokensTests(APITestCase):
    """研究者重設別人的密碼，同樣要作廢對方既有的 token。

    停用帳號（is_active=False）也一樣：JWTAuthentication 每次都會查 DB 並
    擋掉 inactive user，所以停用不需要額外處理；重設密碼會。
    """

    def setUp(self):
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(
            username="researcher", password=OLD_PASSWORD
        )
        self.researcher.groups.add(group)
        self.target = User.objects.create_user(
            username="subject42", password=OLD_PASSWORD
        )

    def test_reset_blacklists_target_refresh_token(self):
        login = self.client.post(
            "/api/token/",
            {"username": "subject42", "password": OLD_PASSWORD},
            format="json",
        )
        old_refresh = login.data["refresh"]

        self.client.force_authenticate(user=self.researcher)
        response = self.client.post(
            f"/api/accounts/{self.target.id}/reset-password/",
            {"password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        self.client.force_authenticate(user=None)
        refreshed = self.client.post(
            "/api/token/refresh/", {"refresh": old_refresh}, format="json"
        )
        self.assertEqual(refreshed.status_code, status.HTTP_401_UNAUTHORIZED)


class ChangePasswordThrottleTests(APITestCase):
    """驗 old_password 的端點就是一個密碼 oracle，一定要限流。

    ⚠️ 直接 patch ScopedRateThrottle.THROTTLE_RATES，不能用 override_settings
    ——原因見 tests_auth_hardening.LoginThrottleTests 的註解。

    這裡也順帶釘住「依 user 計數而非 IP」：受試者集體在同一場地共用 NAT
    出口 IP，若照 IP 計數，一個人試錯就會鎖住整間教室。
    """

    RATE = 3

    def setUp(self):
        cache.clear()
        patcher = patch.dict(
            ScopedRateThrottle.THROTTLE_RATES, {"change_password": f"{self.RATE}/min"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="participant", password=OLD_PASSWORD
        )
        self.other = User.objects.create_user(username="other", password=OLD_PASSWORD)

    def _attempt(self):
        return self.client.post(URL, _payload(old="wrong-password"), format="json")

    def test_repeated_wrong_old_passwords_get_throttled(self):
        self.client.force_authenticate(user=self.user)
        for i in range(self.RATE):
            self.assertEqual(self._attempt().status_code, 400, f"第 {i + 1} 次應是 400")
        self.assertEqual(
            self._attempt().status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )

    def test_throttle_counts_per_user_not_per_ip(self):
        self.client.force_authenticate(user=self.user)
        for _ in range(self.RATE):
            self._attempt()
        self.assertEqual(self._attempt().status_code, 429)

        # 同一個 IP、另一個帳號：不應該被前一位用完的額度波及。
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self._attempt().status_code, 400)
