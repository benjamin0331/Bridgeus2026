"""開放自助註冊前的認證強化：密碼驗證、限流、fail-closed 權限、username 唯一性。

對應審查報告第 2.2、2.3、3.4、4.1 節。這四項都是「現在不做，開註冊入口就會出事」
的類別，所以每一項都要有測試釘住，而不是只靠設定看起來對。
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase
from django.urls import get_resolver
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()

STRONG_PASSWORD = "Xk9$mVpq2Lz"


def _researcher(username="res1"):
    user = User.objects.create_user(username=username, password=STRONG_PASSWORD)
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user.groups.add(group)
    return user


# ═══════════════════════════════════════════════════════════
# 2.3 密碼驗證要拿得到 user
# ═══════════════════════════════════════════════════════════

class PasswordSimilarityTests(TestCase):
    """UserAttributeSimilarityValidator 必須真的生效。

    它在 user=None 時會直接 return，而原本的實作在單欄位的
    validate_password() 裡呼叫、拿不到 username——等於 settings 裡設的第一個
    validator 從來沒有作用過。既有測試測的是長度與常見密碼（那兩個 validator
    不需要 user），所以掩蓋了這件事。
    """

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=_researcher())

    def test_create_rejects_password_equal_to_username(self):
        response = self.client.post(
            "/api/accounts/",
            {"username": "subject42", "password": "subject42"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="subject42").exists())

    def test_create_rejects_password_too_similar_to_username(self):
        response = self.client.post(
            "/api/accounts/",
            {"username": "bridgeuser", "password": "bridgeuser1"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_create_still_accepts_a_strong_password(self):
        response = self.client.post(
            "/api/accounts/",
            {"username": "subject43", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_reset_rejects_password_equal_to_target_username(self):
        """重設密碼走的是另一個 serializer，target 由 view 用 context 傳入。"""
        target = User.objects.create_user(username="victim9", password=STRONG_PASSWORD)
        response = self.client.post(
            f"/api/accounts/{target.pk}/reset-password/",
            {"password": "victim9"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        target.refresh_from_db()
        self.assertTrue(target.check_password(STRONG_PASSWORD))  # 密碼沒被換掉


# ═══════════════════════════════════════════════════════════
# 4.1 / 4.2 username 唯一性
# ═══════════════════════════════════════════════════════════

class UsernameUniquenessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=_researcher())

    def test_rejects_username_differing_only_by_case(self):
        """Django 的登入是大小寫敏感的，允許 Alice 與 alice 並存會讓使用者
        穩定產生「我明明註冊過卻登不進去」的困惑。
        """
        User.objects.create_user(username="Alice", password=STRONG_PASSWORD)
        response = self.client.post(
            "/api/accounts/",
            {"username": "alice", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.data)

    def test_integrity_error_becomes_400_not_500(self):
        """validate_username 的 exists() 與 create_user 之間有空窗。
        併發註冊撞上時要回 400，不能讓 IntegrityError 冒成 500。
        """
        with patch.object(
            User.objects, "create_user", side_effect=IntegrityError("duplicate key")
        ):
            response = self.client.post(
                "/api/accounts/",
                {"username": "raced", "password": STRONG_PASSWORD},
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.data)


# ═══════════════════════════════════════════════════════════
# 2.2 登入限流
# ═══════════════════════════════════════════════════════════

class LoginThrottleTests(TestCase):
    """暴力破解與帳號枚舉的正門。

    ⚠️ 這裡直接改 ScopedRateThrottle.THROTTLE_RATES，**不能用
    override_settings(REST_FRAMEWORK=...)**：SimpleRateThrottle 在 class body
    就把 `THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES` 綁成類別屬性
    （rest_framework/throttling.py:66），那是 import 時求值的。override_settings
    會讓 api_settings 產生新的 dict，但類別屬性仍指向舊的——測試會安靜地跑在
    正式值上，看起來「限流沒生效」。
    """

    RATE = 3

    def setUp(self):
        cache.clear()  # throttle 計數存在 cache，測試之間必須清乾淨
        self._patcher = patch.dict(
            ScopedRateThrottle.THROTTLE_RATES, {"login": f"{self.RATE}/min"}
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(cache.clear)
        self.client = APIClient()
        User.objects.create_user(username="target", password=STRONG_PASSWORD)

    def _attempt(self, password="wrong-password"):
        return self.client.post(
            "/api/token/", {"username": "target", "password": password}, format="json"
        )

    def test_repeated_failed_logins_get_throttled(self):
        for i in range(self.RATE):
            self.assertEqual(self._attempt().status_code, 401, f"第 {i + 1} 次應是 401")
        self.assertEqual(self._attempt().status_code, 429)

    def test_throttle_counts_successful_logins_too(self):
        """限流不看成敗——只看嘗試次數，否則猜中的那一次就繞過了計數。"""
        for _ in range(self.RATE):
            self._attempt(password=STRONG_PASSWORD)
        self.assertEqual(self._attempt(password=STRONG_PASSWORD).status_code, 429)


class TokenRefreshNotThrottledByPermissionsTests(TestCase):
    """DEFAULT_PERMISSION_CLASSES 改成 IsAuthenticated 之後，兩個 token 端點
    仍必須是公開的——simplejwt 的 TokenViewBase 明確設了 permission_classes = ()，
    會壓過專案預設。這個測試確保日後有人動了那個假設會被抓到。
    """

    def test_token_obtain_is_reachable_without_authentication(self):
        client = APIClient()
        User.objects.create_user(username="opener", password=STRONG_PASSWORD)
        response = client.post(
            "/api/token/",
            {"username": "opener", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_token_refresh_is_reachable_without_authentication(self):
        client = APIClient()
        User.objects.create_user(username="refresher", password=STRONG_PASSWORD)
        obtained = client.post(
            "/api/token/",
            {"username": "refresher", "password": STRONG_PASSWORD},
            format="json",
        )
        response = client.post(
            "/api/token/refresh/", {"refresh": obtained.data["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)


# ═══════════════════════════════════════════════════════════
# 3.4 fail-closed 權限
# ═══════════════════════════════════════════════════════════

class DefaultPermissionIsFailClosedTests(TestCase):
    def test_default_permission_class_is_is_authenticated(self):
        from rest_framework.permissions import IsAuthenticated
        from rest_framework.settings import api_settings

        self.assertEqual(api_settings.DEFAULT_PERMISSION_CLASSES, [IsAuthenticated])

    def test_every_api_view_declares_its_permissions_explicitly(self):
        """預設改成 fail-closed 之後，忘了寫 permission_classes 不再是安全漏洞。

        但「每個 view 都明確宣告」仍然是我們要維持的紀律——它讓讀 code 的人
        一眼看得出這支端點的存取範圍，不必回頭查 settings。這個測試列出沒有
        自己宣告的 view，供 review 時判斷是否刻意。
        """
        resolver = get_resolver()
        missing = []
        for pattern in resolver.url_patterns:
            for sub in getattr(pattern, "url_patterns", []):
                cls = getattr(sub.callback, "cls", None)
                if cls is None or not cls.__module__.startswith("api."):
                    continue
                if "permission_classes" not in vars(cls):
                    missing.append(f"{cls.__module__}.{cls.__name__}")
        self.assertEqual(missing, [], f"這些 api view 沒有自己宣告權限：{missing}")


# ═══════════════════════════════════════════════════════════
# 3.1 / 3.2 停用帳號要在所有入口一致失效
# ═══════════════════════════════════════════════════════════

class DeactivatedAccountTests(TestCase):
    """專案刻意不開放刪除帳號、改用停用（見 supervisor 帳號管理 spec），
    所以停用是唯一的處置手段——它必須在每一個入口都生效，而不是只有 REST。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="banned", password=STRONG_PASSWORD)
        self.client = APIClient()
        obtained = self.client.post(
            "/api/token/",
            {"username": "banned", "password": STRONG_PASSWORD},
            format="json",
        )
        self.access = obtained.data["access"]
        # 先拿到 token 才停用——模擬「token 已簽發、事後才被停用」。
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

    def test_rest_rejects_token_of_deactivated_user(self):
        """基準行為，由 simplejwt 的 JWTAuthentication.get_user 提供。"""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.assertEqual(self.client.get("/api/me/").status_code, 401)

    def test_survey_view_rejects_token_of_deactivated_user(self):
        """DialogueSurveyView 原本用 JWTStatelessUserAuthentication，
        而 TokenUser.is_active 是寫死的 True——停用者在 token 有效期內照樣進得來。
        """
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.get("/api/dialogue/topics/102/survey/")
        self.assertEqual(response.status_code, 401)

    def test_stance_profile_view_rejects_token_of_deactivated_user(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.get("/api/dialogue/topics/102/stance-profile/")
        self.assertEqual(response.status_code, 401)

    def test_websocket_authenticate_rejects_deactivated_user(self):
        """WebSocket 走自寫的 _authenticate_access_token，不經過 DRF，
        所以 simplejwt 的檢查幫不上忙——必須自己擋。
        """
        from asgiref.sync import async_to_sync

        from api.consumers import _authenticate_access_token

        self.assertIsNone(async_to_sync(_authenticate_access_token)(self.access))

    def test_websocket_authenticate_still_accepts_active_user(self):
        """確認上一個測試不是因為 token 本身壞掉才回 None。"""
        from asgiref.sync import async_to_sync

        from api.consumers import _authenticate_access_token

        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        resolved = async_to_sync(_authenticate_access_token)(self.access)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.pk, self.user.pk)


class UpdateLastLoginTests(TestCase):
    """3.5：/api/token/ 登入要真的更新 last_login。

    simplejwt 預設 UPDATE_LAST_LOGIN=False，而帳號管理面板與 admin 列表都在
    顯示這一欄——不開的話研究者看到的是永遠不動的舊值。
    """

    def test_token_obtain_updates_last_login(self):
        user = User.objects.create_user(username="tracked", password=STRONG_PASSWORD)
        self.assertIsNone(user.last_login)

        APIClient().post(
            "/api/token/",
            {"username": "tracked", "password": STRONG_PASSWORD},
            format="json",
        )

        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)
