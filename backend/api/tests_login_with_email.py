"""登入（POST /api/token/）可以用帳號或 email。

BridgeUsTokenObtainPairSerializer.validate 在交給 simplejwt 的帳密驗證之前，
把 email 換成對應帳號的 username。email 在 DB 是 unique，至多一個對應。
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

URL = "/api/token/"
PASSWORD = "Xk9$mVpq2Lz"


class LoginWithEmailTests(APITestCase):
    def setUp(self):
        # /api/token/ 掛了 ScopedRateThrottle（login, 預設 10/min，per IP）。
        # 這個檔案打十幾次登入，計數存在 process 全域的 cache——不清乾淨會溢到
        # 後面跑的測試檔（例如 tests_token_is_researcher_claim）。
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="subject01", email="subject01@example.com", password=PASSWORD
        )

    def _post(self, login, password=PASSWORD):
        return self.client.post(
            URL, {"username": login, "password": password}, format="json"
        )

    def test_login_with_username_still_works(self):
        response = self._post("subject01")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_login_with_email(self):
        response = self._post("subject01@example.com")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_login_with_email_is_case_insensitive(self):
        response = self._post("SUBJECT01@EXAMPLE.COM")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_email_with_wrong_password_is_rejected(self):
        response = self._post("subject01@example.com", password="not-it")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_email_is_rejected(self):
        response = self._post("nobody@example.com")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_account_cannot_log_in_by_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self._post("subject01@example.com")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_account_without_email_is_unaffected(self):
        # 研究者代開的帳號沒有 email；用帳號登入照常。
        User.objects.create_user(username="legacy", password=PASSWORD)
        self.assertEqual(self._post("legacy").status_code, status.HTTP_200_OK)

    def test_username_that_looks_like_an_email_wins_over_someone_elses_email(self):
        # username 允許含 "@"。若某人的 username 剛好等於別人的 email，
        # 按 username 登入必須進到 username 那個帳號，不能被 email 解析劫走。
        weird = User.objects.create_user(
            username="weird@example.com", password="WeirdPass#1"
        )
        User.objects.create_user(
            username="other", email="weird@example.com", password="OtherPass#1"
        )
        response = self._post("weird@example.com", password="WeirdPass#1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 換 other 的密碼就應該失敗（證明沒有被解析成 other）。
        self.assertEqual(
            self._post("weird@example.com", password="OtherPass#1").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
