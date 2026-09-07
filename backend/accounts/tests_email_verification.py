"""信箱驗證：註冊前用 email 當鍵驗一次，之後才准建帳號；登入後也能補驗
自己目前的信箱。

測試重點：
- 沒先驗信箱就註冊 → 400「請先完成信箱驗證。」
- 驗過的紀錄在成功建帳號後被刪掉，不能拿去註冊第二個帳號
- 驗證碼只存雜湊、會過期、錯 5 次作廢、單次使用
- 已被註冊的 email 不給重新索取驗證碼（與註冊頁一致）
- 登入後補驗：驗成功會把 User.email_verified_at 設起來
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

from accounts.models import EmailVerificationCode

User = get_user_model()

REQ_URL = "/api/email-verification/request/"
CONFIRM_URL = "/api/email-verification/confirm/"
ME_REQ_URL = "/api/me/email/verify/request/"
ME_CONFIRM_URL = "/api/me/email/verify/confirm/"
REGISTER_URL = "/api/register/"

PASSWORD = "Zx9$kLmn2Rt"


def _code_from_outbox():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


def _verify(client, email):
    """跑完 request + confirm，回傳用過的碼。"""
    client.post(REQ_URL, {"email": email}, format="json")
    code = _code_from_outbox()
    resp = client.post(
        CONFIRM_URL, {"email": email, "code": code}, format="json"
    )
    assert resp.status_code == 200, resp.data
    return code


class PreRegistrationVerificationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_request_then_confirm_marks_the_email_verified(self):
        r = self.client.post(REQ_URL, {"email": "new@example.com"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

        code = _code_from_outbox()
        record = EmailVerificationCode.objects.get(email="new@example.com")
        self.assertEqual(record.code_hash, EmailVerificationCode.hash_code(code))
        self.assertNotIn(code, record.code_hash)

        r = self.client.post(
            CONFIRM_URL, {"email": "new@example.com", "code": code}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.assertIsNotNone(record.verified_at)
        self.assertTrue(
            EmailVerificationCode.is_pre_registration_verified("new@example.com")
        )

    def test_request_rejects_an_already_registered_email(self):
        User.objects.create_user(username="taken", email="taken@example.com")
        r = self.client.post(
            REQ_URL, {"email": "taken@example.com"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", r.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_code_bumps_attempts_and_stays_unverified(self):
        self.client.post(REQ_URL, {"email": "new@example.com"}, format="json")
        r = self.client.post(
            CONFIRM_URL, {"email": "new@example.com", "code": "000000"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        record = EmailVerificationCode.objects.get(email="new@example.com")
        self.assertEqual(record.attempt_count, 1)
        self.assertIsNone(record.verified_at)

    def test_code_locks_after_max_attempts(self):
        self.client.post(REQ_URL, {"email": "new@example.com"}, format="json")
        code = _code_from_outbox()
        for _ in range(EmailVerificationCode.MAX_ATTEMPTS):
            self.client.post(
                CONFIRM_URL,
                {"email": "new@example.com", "code": "000000"},
                format="json",
            )
        # 就算給對的碼也因為 attempt_count 到頂而作廢。
        r = self.client.post(
            CONFIRM_URL, {"email": "new@example.com", "code": code}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_code_is_rejected(self):
        self.client.post(REQ_URL, {"email": "new@example.com"}, format="json")
        code = _code_from_outbox()
        record = EmailVerificationCode.objects.get(email="new@example.com")
        record.expires_at = timezone.now() - timedelta(minutes=1)
        record.save(update_fields=["expires_at"])
        r = self.client.post(
            CONFIRM_URL, {"email": "new@example.com", "code": code}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_new_request_invalidates_the_previous_code(self):
        self.client.post(REQ_URL, {"email": "new@example.com"}, format="json")
        first = _code_from_outbox()
        self.client.post(REQ_URL, {"email": "new@example.com"}, format="json")
        r = self.client.post(
            CONFIRM_URL, {"email": "new@example.com", "code": first}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mail_failure_reports_502_and_burns_the_code(self):
        with patch(
            "accounts.views.send_email_verification_code",
            side_effect=RuntimeError("smtp down"),
        ):
            r = self.client.post(
                REQ_URL, {"email": "new@example.com"}, format="json"
            )
        self.assertEqual(r.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIsNotNone(
            EmailVerificationCode.objects.get(email="new@example.com").consumed_at
        )


class RegistrationGateTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _register_payload(self, **overrides):
        payload = {
            "username": "newbie",
            "email": "newbie@example.com",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "consent": True,
        }
        payload.update(overrides)
        return payload

    def test_register_is_blocked_without_email_verification(self):
        r = self.client.post(
            REGISTER_URL, self._register_payload(), format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", r.data)
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_register_succeeds_after_verification_and_marks_verified(self):
        _verify(self.client, "newbie@example.com")
        r = self.client.post(
            REGISTER_URL, self._register_payload(), format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="newbie")
        self.assertIsNotNone(user.email_verified_at)
        # 驗證紀錄被認領（刪除），不能再拿去註冊別的帳號。
        self.assertFalse(
            EmailVerificationCode.objects.filter(
                email__iexact="newbie@example.com"
            ).exists()
        )

    def test_a_verification_cannot_be_reused_for_a_second_account(self):
        _verify(self.client, "shared@example.com")
        first = self.client.post(
            REGISTER_URL,
            self._register_payload(username="first", email="shared@example.com"),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(
            REGISTER_URL,
            self._register_payload(username="second", email="shared@example.com"),
            format="json",
        )
        # email 已被 first 用掉 → 這次連「已註冊」都會先擋（順序上 email 唯一性
        # 先命中），重點是沒有第二個帳號生出來。
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="second").exists())

    def test_stale_verification_outside_grace_window_does_not_count(self):
        _verify(self.client, "slow@example.com")
        record = EmailVerificationCode.objects.get(email="slow@example.com")
        record.verified_at = timezone.now() - timedelta(hours=2)
        record.save(update_fields=["verified_at"])
        r = self.client.post(
            REGISTER_URL,
            self._register_payload(username="slow", email="slow@example.com"),
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class MeEmailVerificationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username="member", email="member@example.com", password=PASSWORD
        )
        self.user.email_verified_at = None
        self.user.save(update_fields=["email_verified_at"])
        self.client.force_authenticate(user=self.user)

    def test_request_then_confirm_sets_user_email_verified_at(self):
        r = self.client.post(ME_REQ_URL, {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["member@example.com"])

        code = _code_from_outbox()
        r = self.client.post(ME_CONFIRM_URL, {"code": code}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.email_verified_at)

    def test_request_is_rejected_when_already_verified(self):
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email_verified_at"])
        r = self.client.post(ME_REQ_URL, {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 0)

    def test_request_is_rejected_without_an_email_on_file(self):
        self.user.email = None
        self.user.save(update_fields=["email"])
        r = self.client.post(ME_REQ_URL, {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_requires_authentication(self):
        self.client.force_authenticate(user=None)
        r = self.client.post(ME_CONFIRM_URL, {"code": "123456"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_code_keeps_user_unverified(self):
        self.client.post(ME_REQ_URL, {}, format="json")
        r = self.client.post(ME_CONFIRM_URL, {"code": "000000"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)

    def test_a_pre_registration_code_does_not_satisfy_the_me_endpoint(self):
        # 註冊前那條（user=None）不能拿來驗登入者的信箱。
        self.client.post(
            REQ_URL, {"email": "member@example.com"}, format="json"
        )
        # 上一行其實會 400（email 已註冊），保險起見直接塞一筆 user=None 的碼。
        EmailVerificationCode.objects.create(
            email="member@example.com",
            user=None,
            code_hash=EmailVerificationCode.hash_code("111111"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        r = self.client.post(ME_CONFIRM_URL, {"code": "111111"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)


class EmailVerificationThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _patch_rate(self, scope, rate):
        patcher = patch.dict(ScopedRateThrottle.THROTTLE_RATES, {scope: rate})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_endpoint_is_throttled(self):
        self._patch_rate("email_verification_request", "2/hour")
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    REQ_URL, {"email": "x@example.com"}, format="json"
                ).status_code,
                status.HTTP_200_OK,
            )
        self.assertEqual(
            self.client.post(
                REQ_URL, {"email": "x@example.com"}, format="json"
            ).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
