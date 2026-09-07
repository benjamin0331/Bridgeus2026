"""忘記密碼：寄驗證碼到信箱 → 驗碼 → 設新密碼。

兩支端點都免登入，所以測試重點除了「功能對不對」，還有：
- 不論信箱有沒有註冊，request 一律回同一句話（不可被拿來列舉帳號）；
- 驗證碼只存雜湊、單次使用、會過期、錯太多次就作廢；
- 換完密碼舊 refresh token 立刻失效（見 api/tests_change_password）。
"""

import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import PasswordResetCode

User = get_user_model()

REQUEST_URL = "/api/password-reset/request/"
CONFIRM_URL = "/api/password-reset/confirm/"

OLD_PASSWORD = "Xk9$mVpq2Lz"
NEW_PASSWORD = "Qw7!nBrt4Hs"


def _code_from_outbox():
    """把最新一封信裡的六位數字驗證碼挖出來。"""
    body = mail.outbox[-1].body
    return re.search(r"\b(\d{6})\b", body).group(1)


class PasswordResetRequestTests(APITestCase):
    def setUp(self):
        # 兩支端點依 IP 限流，計數存在 cache——測試之間不清會互相波及。
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="participant",
            email="p@example.com",
            password=OLD_PASSWORD,
        )

    def test_known_email_sends_a_code_and_stores_only_its_hash(self):
        response = self.client.post(
            REQUEST_URL, {"email": "p@example.com"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

        record = PasswordResetCode.objects.get(user=self.user)
        code = _code_from_outbox()
        self.assertEqual(record.code_hash, PasswordResetCode.hash_code(code))
        self.assertNotIn(code, record.code_hash)  # 明碼沒落庫

    def test_unknown_email_looks_identical_but_sends_nothing(self):
        known = self.client.post(
            REQUEST_URL, {"email": "p@example.com"}, format="json"
        )
        mail.outbox.clear()
        unknown = self.client.post(
            REQUEST_URL, {"email": "nobody@example.com"}, format="json"
        )

        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.data, known.data)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(
            PasswordResetCode.objects.filter(
                user__email="nobody@example.com"
            ).exists()
        )

    def test_email_match_is_case_insensitive(self):
        response = self.client.post(
            REQUEST_URL, {"email": "P@Example.com"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    def test_inactive_account_gets_no_mail(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(
            REQUEST_URL, {"email": "p@example.com"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_new_request_invalidates_the_previous_code(self):
        self.client.post(REQUEST_URL, {"email": "p@example.com"}, format="json")
        first = _code_from_outbox()
        self.client.post(REQUEST_URL, {"email": "p@example.com"}, format="json")

        # 舊碼已被標記為已用，拿去 confirm 應該不通。
        response = self.client.post(
            CONFIRM_URL,
            {
                "email": "p@example.com",
                "code": first,
                "new_password": NEW_PASSWORD,
                "new_password_confirm": NEW_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_malformed_email_is_rejected(self):
        response = self.client.post(
            REQUEST_URL, {"email": "not-an-email"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mail_send_failure_does_not_leak_and_burns_the_code(self):
        with patch(
            "accounts.views.send_password_reset_code",
            side_effect=RuntimeError("smtp down"),
        ):
            response = self.client.post(
                REQUEST_URL, {"email": "p@example.com"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record = PasswordResetCode.objects.get(user=self.user)
        self.assertIsNotNone(record.consumed_at)  # 寄不出去的碼不留成有效憑證


class PasswordResetConfirmTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="participant",
            email="p@example.com",
            password=OLD_PASSWORD,
        )
        self.client.post(
            REQUEST_URL, {"email": "p@example.com"}, format="json"
        )
        self.code = _code_from_outbox()

    def _confirm(self, **overrides):
        payload = {
            "email": "p@example.com",
            "code": self.code,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        }
        payload.update(overrides)
        return self.client.post(CONFIRM_URL, payload, format="json")

    def test_correct_code_resets_password_and_verifies_email(self):
        response = self._confirm()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertIsNotNone(self.user.email_verified_at)

        record = PasswordResetCode.objects.get(user=self.user)
        self.assertIsNotNone(record.consumed_at)

    def test_code_cannot_be_reused(self):
        self.assertEqual(self._confirm().status_code, status.HTTP_200_OK)
        again = self._confirm(
            new_password="Zz2@kLmn9Rt", new_password_confirm="Zz2@kLmn9Rt"
        )
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_code_bumps_attempt_count_and_keeps_old_password(self):
        response = self._confirm(code="000000")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        record = PasswordResetCode.objects.get(user=self.user)
        self.assertEqual(record.attempt_count, 1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_code_locks_after_max_attempts(self):
        for _ in range(PasswordResetCode.MAX_ATTEMPTS):
            self.assertEqual(
                self._confirm(code="000000").status_code,
                status.HTTP_400_BAD_REQUEST,
            )
        # 即使這次給對的碼，也因為 attempt_count 到頂而作廢。
        self.assertEqual(
            self._confirm().status_code, status.HTTP_400_BAD_REQUEST
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_expired_code_is_rejected(self):
        record = PasswordResetCode.objects.get(user=self.user)
        record.expires_at = timezone.now() - timedelta(minutes=1)
        record.save(update_fields=["expires_at"])
        self.assertEqual(
            self._confirm().status_code, status.HTTP_400_BAD_REQUEST
        )

    def test_unknown_email_is_rejected_with_the_same_body_as_wrong_code(self):
        wrong = self._confirm(code="000000")
        unknown = self._confirm(email="nobody@example.com")
        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.data, wrong.data)

    def test_weak_password_is_reported_per_field(self):
        response = self._confirm(
            new_password="participant", new_password_confirm="participant"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)

    def test_password_confirm_mismatch(self):
        response = self._confirm(new_password_confirm="Qw7!nBrt4Hx")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password_confirm", response.data)

    def test_non_numeric_code_is_rejected_by_the_serializer(self):
        response = self._confirm(code="abcdef")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", response.data)

    def test_old_refresh_token_stops_working_after_reset(self):
        login = self.client.post(
            "/api/token/",
            {"username": "participant", "password": OLD_PASSWORD},
            format="json",
        )
        old_refresh = login.data["refresh"]

        self.assertEqual(self._confirm().status_code, status.HTTP_200_OK)

        refreshed = self.client.post(
            "/api/token/refresh/", {"refresh": old_refresh}, format="json"
        )
        self.assertEqual(
            refreshed.status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_success_does_not_hand_back_tokens(self):
        # 忘記密碼流程不自動登入：使用者拿新密碼回登入頁。
        response = self._confirm()
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)


class PasswordResetThrottleTests(APITestCase):
    """兩支端點都免登入，DRF 依 IP 計數。直接 patch THROTTLE_RATES，理由見
    api/tests_auth_hardening.LoginThrottleTests 的註解。
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="participant",
            email="p@example.com",
            password=OLD_PASSWORD,
        )

    def _patch_rate(self, scope, rate):
        patcher = patch.dict(
            ScopedRateThrottle.THROTTLE_RATES, {scope: rate}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_endpoint_is_throttled(self):
        self._patch_rate("password_reset_request", "2/hour")
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    REQUEST_URL, {"email": "p@example.com"}, format="json"
                ).status_code,
                status.HTTP_200_OK,
            )
        self.assertEqual(
            self.client.post(
                REQUEST_URL, {"email": "p@example.com"}, format="json"
            ).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def test_confirm_endpoint_is_throttled(self):
        self._patch_rate("password_reset_confirm", "3/hour")
        payload = {
            "email": "p@example.com",
            "code": "000000",
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        }
        for _ in range(3):
            self.assertEqual(
                self.client.post(
                    CONFIRM_URL, payload, format="json"
                ).status_code,
                status.HTTP_400_BAD_REQUEST,
            )
        self.assertEqual(
            self.client.post(CONFIRM_URL, payload, format="json").status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
