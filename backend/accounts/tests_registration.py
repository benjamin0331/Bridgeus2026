"""自助註冊。

對應 spec：docs/superpowers/specs/2026-08-18-self-service-registration-design.md
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.consent import CONSENT_VERSION
from accounts.serializers import RegistrationSerializer

User = get_user_model()

STRONG_PASSWORD = "Xk9$mVpq2Lz"


def _payload(**overrides):
    data = {
        "username": "subject01",
        "email": "subject01@example.com",
        "password": STRONG_PASSWORD,
        "password_confirm": STRONG_PASSWORD,
        "display_name": "小明",
        "consent": True,
    }
    data.update(overrides)
    return data


class RegistrationSerializerSuccessTests(TestCase):
    def test_creates_user_with_expected_fields(self):
        serializer = RegistrationSerializer(data=_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()

        self.assertEqual(user.username, "subject01")
        self.assertEqual(user.email, "subject01@example.com")
        self.assertEqual(user.display_name, "小明")
        self.assertTrue(user.check_password(STRONG_PASSWORD))

    def test_records_consent_version_from_the_constant(self):
        """版本由後端寫入。讓 client 宣稱自己同意了哪一版，等於讓受試者
        自己填寫實驗紀錄。
        """
        serializer = RegistrationSerializer(data=_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertEqual(user.consent_version, CONSENT_VERSION)

    def test_marks_self_registered_user_as_research_subject(self):
        """語意是「自助註冊且留下同意紀錄的人」，讓分析時
        filter(is_research_subject=True) 直接就是有效樣本。
        """
        serializer = RegistrationSerializer(data=_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.save().is_research_subject)

    def test_display_name_is_optional_and_defaults_to_empty(self):
        serializer = RegistrationSerializer(data=_payload(display_name=""))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.save().display_name, "")

    def test_display_name_may_be_omitted_entirely(self):
        payload = _payload()
        del payload["display_name"]
        serializer = RegistrationSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.save().display_name, "")

    def test_new_user_is_not_staff_and_not_researcher(self):
        serializer = RegistrationSerializer(data=_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.groups.count(), 0)


class RegistrationSerializerRejectionTests(TestCase):
    def _assert_invalid(self, field, **overrides):
        serializer = RegistrationSerializer(data=_payload(**overrides))
        self.assertFalse(serializer.is_valid())
        self.assertIn(field, serializer.errors)

    def test_rejects_missing_consent(self):
        payload = _payload()
        del payload["consent"]
        serializer = RegistrationSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("consent", serializer.errors)

    def test_rejects_unchecked_consent(self):
        self._assert_invalid("consent", consent=False)

    def test_rejects_mismatched_password_confirm(self):
        self._assert_invalid("password_confirm", password_confirm="different-pw-99")

    def test_rejects_password_equal_to_username(self):
        self._assert_invalid(
            "password", password="subject01", password_confirm="subject01"
        )

    def test_rejects_password_similar_to_email(self):
        """UserAttributeSimilarityValidator 預設比對 username/email 等四個屬性，
        所以建立比對用的 User 時兩者都要帶上。
        """
        self._assert_invalid(
            "password",
            password="subject01@example.com",
            password_confirm="subject01@example.com",
        )

    def test_rejects_duplicate_username(self):
        User.objects.create_user(username="subject01", password=STRONG_PASSWORD)
        self._assert_invalid("username")

    def test_rejects_username_differing_only_by_case(self):
        User.objects.create_user(username="SUBJECT01", password=STRONG_PASSWORD)
        self._assert_invalid("username")

    def test_rejects_duplicate_email(self):
        User.objects.create_user(
            username="other", password=STRONG_PASSWORD, email="subject01@example.com"
        )
        self._assert_invalid("email")

    def test_rejects_email_differing_only_by_case(self):
        User.objects.create_user(
            username="other", password=STRONG_PASSWORD, email="SUBJECT01@EXAMPLE.COM"
        )
        self._assert_invalid("email")

    def test_rejects_missing_email(self):
        payload = _payload()
        del payload["email"]
        serializer = RegistrationSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)


class RegistrationSerializerIgnoresClientControlledFieldsTests(TestCase):
    """前端不得自行決定同意版本或受試者身分。"""

    def test_ignores_client_supplied_consent_version(self):
        serializer = RegistrationSerializer(
            data=_payload(consent_version="forged-version")
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.save().consent_version, CONSENT_VERSION)

    def test_ignores_client_supplied_is_staff(self):
        serializer = RegistrationSerializer(data=_payload(is_staff=True))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertFalse(serializer.save().is_staff)


class RegistrationEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registers_and_returns_usable_tokens(self):
        response = self.client.post("/api/register/", _payload(), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], "subject01")
        self.assertEqual(response.data["user"]["display_name"], "小明")
        self.assertFalse(response.data["user"]["is_researcher"])

        # 回來的 token 必須真的能用——這是「自動登入」的全部意義。
        authed = APIClient()
        authed.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me = authed.get("/api/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["username"], "subject01")

    def test_access_token_carries_is_researcher_claim(self):
        """必須用 BridgeUsTokenObtainPairSerializer.get_token 產生，否則剛註冊
        與剛登入的使用者會拿到結構不同的 token，前端 getAccessTokenPayload()
        會讀不到 is_researcher。
        """
        from rest_framework_simplejwt.tokens import AccessToken

        response = self.client.post("/api/register/", _payload(), format="json")
        claims = AccessToken(response.data["access"])
        self.assertIn("is_researcher", claims)
        self.assertFalse(claims["is_researcher"])

    def test_endpoint_is_reachable_without_authentication(self):
        """DEFAULT_PERMISSION_CLASSES 是 IsAuthenticated（fail-closed），
        註冊端點必須明確標 AllowAny。
        """
        response = self.client.post("/api/register/", _payload(), format="json")
        self.assertNotEqual(response.status_code, 401)

    def test_rejection_returns_400_with_field_errors(self):
        response = self.client.post(
            "/api/register/", _payload(consent=False), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("consent", response.data)

    def test_does_not_leak_password_in_response(self):
        response = self.client.post("/api/register/", _payload(), format="json")
        self.assertNotIn("password", str(response.data))

    def test_me_endpoint_exposes_display_name(self):
        """設定頁要顯示暱稱，不該為了一個欄位另開端點。"""
        registered = self.client.post("/api/register/", _payload(), format="json")

        authed = APIClient()
        authed.credentials(
            HTTP_AUTHORIZATION=f"Bearer {registered.data['access']}"
        )
        me = authed.get("/api/me/")

        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["display_name"], "小明")


class RegistrationThrottleTests(TestCase):
    """⚠️ 直接改 ScopedRateThrottle.THROTTLE_RATES，不能用 override_settings：
    SimpleRateThrottle 在 class body 就把 THROTTLE_RATES 綁成類別屬性
    （rest_framework/throttling.py:66），override_settings 影響不到它，測試會
    安靜地跑在正式值上。
    """

    RATE = 3

    def setUp(self):
        cache.clear()
        patcher = patch.dict(
            ScopedRateThrottle.THROTTLE_RATES, {"register": f"{self.RATE}/hour"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)
        self.client = APIClient()

    def test_excess_registrations_are_throttled(self):
        for i in range(self.RATE):
            response = self.client.post(
                "/api/register/",
                _payload(username=f"s{i}", email=f"s{i}@example.com"),
                format="json",
            )
            self.assertEqual(response.status_code, 201, f"第 {i + 1} 次應成功")

        response = self.client.post(
            "/api/register/",
            _payload(username="overflow", email="overflow@example.com"),
            format="json",
        )
        self.assertEqual(response.status_code, 429)
