# 受試者自助註冊 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓受試者用瀏覽器自行註冊帳號、在註冊當下留下研究同意紀錄，並直接取得可用的 token。

**Architecture:** 註冊放在 `accounts` app（它擁有 `User` model），新增 `POST /api/register/`。同意書版本由後端從程式碼常數寫入、不接受前端傳值。註冊成功直接回傳 token，前端與登入共用同一段「登入成功要做什麼」的邏輯。

**Tech Stack:** Django 6.0.4、DRF、djangorestframework-simplejwt 5.5.1、uv（Python 3.13）、React + Vite、SQLite（dev）＋ PostgreSQL（prod）

**Spec:** [../specs/2026-08-18-self-service-registration-design.md](../specs/2026-08-18-self-service-registration-design.md)

---

## 執行方式（subagent 必讀）

**不要執行任何 `git commit`、`git add`、`git push`。** 提交由 orchestrator 在檢查點統一處理。完成分配到的 task 之後，把改動留在工作區，並在回報中列出：

1. 你建立或修改的每一個檔案路徑
2. 你實際執行過的指令與**真實輸出**（不要複述計畫裡的「預期」當成結果）
3. 任何與計畫預期不符之處 —— 這比「一切順利」有價值得多

所有 subagent 共用同一個工作區，上一個 task 未提交的改動會直接被你看到。

### 兩個檢查點（orchestrator 執行）

| 檢查點 | 位置 | 內容 |
|---|---|---|
| 1 | Task 5 完成後 | 後端完整（migration、常數、serializer、view、路由、`MeView`）且測試全綠 |
| 2 | Task 8 完成後 | 前端完整且 lint／build 通過 |

---

## 前置知識（實作前必讀）

1. **`accounts` app 已存在**，內含 `models.py`（`User`）、`admin.py`、`apps.py`、`migrations/0001`–`0003`、三個測試檔。本計畫要新增它的 `serializers.py`、`views.py`、`urls.py`。

2. **`consent_version` 欄位還不存在**，Task 1 才會加。它在 custom user model 的設計階段就已選定但實作時遺漏，因此補在這裡。

3. **限流測試不能用 `override_settings`。** `SimpleRateThrottle` 在 class body 就把 `THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES` 綁成類別屬性（`rest_framework/throttling.py:66`），那是 import 時求值的。`override_settings` 會讓 `api_settings` 產生新 dict，但類別屬性仍指向舊的 —— 測試會安靜地跑在正式值上。改用 `patch.dict(ScopedRateThrottle.THROTTLE_RATES, ...)`，見 `api/tests_auth_hardening.py` 的既有作法。

4. **未登入時前端是 catch-all 路由。** `App.jsx:230-237` 在 `if (!user)` 分支只有一條 `<Route path="*">` 指向 `LoginPage`。`/register` 必須加在那條 catch-all **之前**，否則會被吃掉。

5. **token 必須用 `BridgeUsTokenObtainPairSerializer.get_token(user)` 產生**，不可用 `RefreshToken.for_user(user)` —— 後者不帶 `is_researcher` claim，會讓剛註冊與剛登入的使用者拿到結構不同的 token。

### 測試指令

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts --noinput
```

**後端測試共用同一個 Postgres test DB，絕對不可同時執行兩個 `manage.py test`。**

後端指令都在 `backend/` 執行，前端指令都在 `frontend/` 執行。

---

## File Structure

**新建**

| 檔案 | 責任 |
|---|---|
| `backend/accounts/migrations/0004_user_consent_version.py` | 加 `consent_version` 欄位 |
| `backend/accounts/consent.py` | `CONSENT_VERSION`、`CONSENT_DOCUMENT_PATH` 常數 |
| `backend/accounts/serializers.py` | `RegistrationSerializer` |
| `backend/accounts/views.py` | `RegistrationView` |
| `backend/accounts/urls.py` | 路由 |
| `backend/accounts/tests_registration.py` | 註冊測試 |
| `frontend/src/api/auth.js` | `register()` 與共用的登入成功處理 |
| `frontend/src/pages/RegisterPage.jsx` | 註冊頁 |
| `frontend/src/pages/RegisterPage.css` | 註冊頁樣式 |
| `frontend/src/pages/ConsentPage.jsx` | 同意書全文（佔位文字，內容待研究團隊提供） |

**修改**

| 檔案 | 改動 |
|---|---|
| `backend/accounts/models.py` | 加 `consent_version` |
| `backend/BridgeUs_Django/urls.py` | include `accounts.urls` |
| `backend/BridgeUs_Django/settings.py` | `register` 限流預設 `5/hour` → `60/hour` |
| `backend/api/views.py` | `MeView` 回傳加 `display_name` |
| `frontend/src/App.jsx` | 加 `/register`、`/consent` 路由（在 catch-all 之前） |
| `frontend/src/pages/LoginPage.jsx` | 改用 `src/api/auth.js` 的共用函式；加註冊連結 |

---

### Task 1: `consent_version` 欄位

**Files:**
- Modify: `backend/accounts/models.py`
- Create: `backend/accounts/migrations/0004_user_consent_version.py`
- Modify: `backend/accounts/tests_user_model.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/accounts/tests_user_model.py` 末尾的 `RegistrationFieldTests` 類別內追加：

```python
    def test_consent_version_defaults_to_empty_string(self):
        """空字串 = 沒有同意紀錄。研究者代開的帳號與遷移前的既有帳號都是這一類，
        它們本來就不該進入分析樣本。
        """
        user = User.objects.create_user(username="c1", password="Xk9$mVpq2Lz")
        self.assertEqual(user.consent_version, "")
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_user_model.RegistrationFieldTests --noinput
```

預期：`AttributeError: 'User' object has no attribute 'consent_version'`。

- [ ] **Step 3: 加欄位**

在 `backend/accounts/models.py` 的 `display_name` 之後、`class Meta` 之前加入：

```python
    # 使用者註冊時同意的研究說明版本（accounts.consent.CONSENT_VERSION）。
    # 空字串 = 沒有同意紀錄——研究者代開的帳號與遷移前的既有帳號都是這一類。
    # 存的是簽署當下的版本字串，日後改版不會動到既有紀錄。
    consent_version = models.CharField(max_length=32, blank=True)
```

- [ ] **Step 4: 產生 migration**

```bash
DB_ENGINE=sqlite uv run python manage.py makemigrations accounts --name user_consent_version
```

預期產生 `accounts/migrations/0004_user_consent_version.py`，內含單一 `AddField`。

- [ ] **Step 5: 確認是純新增、沒有夾帶其他變更**

```bash
grep -c "operations" accounts/migrations/0004_user_consent_version.py
grep -E "AddField|AlterField|RemoveField|RunPython" accounts/migrations/0004_user_consent_version.py
```

預期：只出現一行 `AddField`。若出現 `AlterField` 或 `RunPython`，停下來回報。

- [ ] **Step 6: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts --noinput
```

預期：全部 OK。

- [ ] **Step 7: 回報，不要 commit**

---

### Task 2: 同意書版本常數

**Files:**
- Create: `backend/accounts/consent.py`

- [ ] **Step 1: 建立檔案**

建立 `backend/accounts/consent.py`：

```python
"""研究同意說明的版本與全文位置。

放程式碼常數而不是資料庫，理由與 api.dialogue_topics.TOPIC_CONFIGS、
api.achievements.CATALOG 一致：這是研究設計的一部分，不是使用者產生的內容，
沒有「線上新增一個版本」的需求，也就沒有理由付出 fixture 與 data migration
同步的代價。

改版規則：同意書內文一旦有實質變動就換一個新字串，不要沿用。
User.consent_version 存的是簽署當下的值，改這裡不會動到任何既有紀錄——
這正是要的行為，受試者同意的是他當時看到的那一版。
"""

CONSENT_VERSION = "2026-08-v1"

# 全文頁面的前端路徑，供註冊頁連結。內容由研究團隊提供。
CONSENT_DOCUMENT_PATH = "/consent"
```

- [ ] **Step 2: 確認可匯入**

```bash
DB_ENGINE=sqlite uv run python -c "
from accounts.consent import CONSENT_VERSION, CONSENT_DOCUMENT_PATH
print('CONSENT_VERSION =', CONSENT_VERSION)
print('CONSENT_DOCUMENT_PATH =', CONSENT_DOCUMENT_PATH)
"
```

預期輸出：

```
CONSENT_VERSION = 2026-08-v1
CONSENT_DOCUMENT_PATH = /consent
```

- [ ] **Step 3: 回報，不要 commit**

---

### Task 3: `RegistrationSerializer`

**Files:**
- Create: `backend/accounts/serializers.py`
- Create: `backend/accounts/tests_registration.py`

本 task 只做 serializer 層，view 在 Task 4。

- [ ] **Step 1: 寫失敗測試**

建立 `backend/accounts/tests_registration.py`：

```python
"""自助註冊。

對應 spec：docs/superpowers/specs/2026-08-18-self-service-registration-design.md
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

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
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_registration --noinput
```

預期：`ModuleNotFoundError: No module named 'accounts.serializers'`。

- [ ] **Step 3: 寫 serializer**

建立 `backend/accounts/serializers.py`：

```python
"""自助註冊的輸入驗證與帳號建立。

與 api.serializers.AccountCreateSerializer（研究者代開帳號）平行存在，刻意
不共用：兩者的必填欄位、同意紀錄、is_research_subject 預設值都不同，硬要
抽共同基底只會讓兩邊的差異變得難讀。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as dj_validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .consent import CONSENT_VERSION

User = get_user_model()


class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(
        max_length=150, validators=[UnicodeUsernameValidator()]
    )
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)
    display_name = serializers.CharField(
        max_length=50, required=False, allow_blank=True, default=""
    )
    consent = serializers.BooleanField()

    # consent_version 與 is_research_subject 刻意不是輸入欄位——它們由 create()
    # 決定。讓 client 宣稱自己同意了哪一版、或自己是不是受試者，等於讓受試者
    # 填寫自己的實驗紀錄。

    def validate_username(self, value):
        # iexact 而非精確比對：Django 的登入是大小寫敏感的，允許 Alice 與 alice
        # 並存會讓使用者穩定產生「我明明註冊過卻登不進去」的困惑。
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("這個帳號名稱已經有人用了。")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("這個 email 已經註冊過了。")
        return value

    def validate_consent(self, value):
        if not value:
            raise serializers.ValidationError("必須閱讀並同意研究說明才能註冊。")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "兩次輸入的密碼不一致。"}
            )

        # 傳入未存檔的 User，UserAttributeSimilarityValidator 才有東西可比對。
        # username 與 email 都要帶：該 validator 預設比對 username、first_name、
        # last_name、email 四個屬性，少了 email 就擋不住「密碼跟自己的信箱很像」。
        try:
            dj_validate_password(
                attrs["password"],
                user=User(username=attrs["username"], email=attrs["email"]),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        try:
            # savepoint：Postgres 在 IntegrityError 之後會讓當前交易進入 aborted
            # 狀態，沒有 atomic 包住的話，後續查詢會拋 TransactionManagementError
            # 而不是我們想回的 400。
            with transaction.atomic():
                user = User.objects.create_user(
                    username=validated_data["username"],
                    email=validated_data["email"],
                    password=validated_data["password"],
                )
                user.display_name = validated_data.get("display_name", "")
                user.consent_version = CONSENT_VERSION
                user.is_research_subject = True
                user.save(
                    update_fields=[
                        "display_name",
                        "consent_version",
                        "is_research_subject",
                    ]
                )
        except IntegrityError:
            # validate_username / validate_email 的 exists() 與這裡之間有空窗。
            # 併發註冊撞上時要回 400，不能讓 IntegrityError 冒成 500。
            # 兩個唯一約束都可能命中，訊息寫得涵蓋兩者。
            raise serializers.ValidationError(
                {"username": ["這個帳號名稱或 email 已經有人用了，請再試一次。"]}
            )
        return user
```

- [ ] **Step 4: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_registration --noinput
```

預期：`Ran 18 tests` 全部 OK（成功路徑 6 + 拒絕路徑 10 + 忽略前端傳值 2）。

- [ ] **Step 5: 回報，不要 commit**

---

### Task 4: `RegistrationView` 與路由

**Files:**
- Create: `backend/accounts/views.py`
- Create: `backend/accounts/urls.py`
- Modify: `backend/BridgeUs_Django/urls.py`
- Modify: `backend/BridgeUs_Django/settings.py`
- Modify: `backend/accounts/tests_registration.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/accounts/tests_registration.py` 末尾追加，並在檔案頂端的 import 區補上
`from rest_framework.test import APIClient` 與 `from unittest.mock import patch`
以及 `from rest_framework.throttling import ScopedRateThrottle`：

```python
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
```

同時在檔案頂端 import 區補 `from django.core.cache import cache`。

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_registration.RegistrationEndpointTests --noinput
```

預期：404（`/api/register/` 尚未存在）而非 201。

- [ ] **Step 3: 寫 view**

建立 `backend/accounts/views.py`：

```python
"""自助註冊端點。

與 api/views.py 的 AccountListCreateView（研究者代開帳號）平行存在。
放在 accounts app 而不是 api：後者的 views.py 已經 4400 行，而 accounts
本來就擁有 User model。
"""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.serializers import BridgeUsTokenObtainPairSerializer

from .serializers import RegistrationSerializer


class RegistrationView(APIView):
    """POST /api/register/ — 受試者自助註冊，成功後直接發 token。

    直接發 token（自動登入）而不是導回登入頁：受試者少一道手續，降低流失。

    限流的預設值刻意放寬（見 settings 的 register scope）：DRF 對未認證請求
    依 IP 計數，而受試者會在同一個場地集體註冊、共用 NAT 出口 IP。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # 用登入用的同一個 serializer 產生 token，確保 is_researcher claim 存在。
        # RefreshToken.for_user(user) 不會帶那個 claim，前端解 token 會讀不到。
        refresh = BridgeUsTokenObtainPairSerializer.get_token(user)

        return Response(
            {
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "is_researcher": False,
                },
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )
```

- [ ] **Step 4: 寫路由**

建立 `backend/accounts/urls.py`：

```python
from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegistrationView.as_view()),
]
```

在 `backend/BridgeUs_Django/urls.py` 的 `path('api/', include('api.urls')),` 之後加入：

```python
    path('api/', include('accounts.urls')),  # 自助註冊
```

- [ ] **Step 5: 放寬 register 限流預設值**

在 `backend/BridgeUs_Django/settings.py` 把

```python
        'register': os.getenv('THROTTLE_REGISTER', '5/hour'),
```

改為

```python
        # 60 而非個位數：DRF 對未認證請求依 IP 計數，而受試者會在同一個場地
        # 集體註冊、共用 NAT 出口 IP。5/hour 會讓第 6 位受試者開始就註冊失敗，
        # 直接毀掉一次資料收集。本平台不是公開商業服務、URL 未對外宣傳，要防的
        # 是隨機掃描而非有組織的濫用；真的遇到濫用可用環境變數即時調低。
        'register': os.getenv('THROTTLE_REGISTER', '60/hour'),
```

- [ ] **Step 6: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_registration --noinput
```

預期：`Ran 24 tests` 全部 OK（Task 3 的 18 個 + 端點 5 個 + 限流 1 個）。

- [ ] **Step 7: 回報，不要 commit**

---

### Task 5: `MeView` 補 `display_name`

**Files:**
- Modify: `backend/api/views.py`（`MeView.get`）
- Modify: `backend/accounts/tests_registration.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/accounts/tests_registration.py` 的 `RegistrationEndpointTests` 類別內追加：

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_registration.RegistrationEndpointTests.test_me_endpoint_exposes_display_name --noinput
```

預期：`KeyError: 'display_name'`。

- [ ] **Step 3: 實作**

在 `backend/api/views.py` 的 `MeView.get` 回應字典中，`"username"` 之後加入一行：

```python
                "display_name": request.user.display_name,
```

- [ ] **Step 4: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts api.tests_auth_hardening api.tests_account_management --noinput
```

預期：全部 OK。

- [ ] **Step 5: 後端整體驗證**

```bash
DB_ENGINE=sqlite uv run python manage.py check
DB_ENGINE=sqlite uv run python manage.py makemigrations --check --dry-run
```

預期：`System check identified no issues`、`No changes detected`。

- [ ] **Step 6: 回報，不要 commit**

> **檢查點 1（orchestrator）**：後端完整且測試全綠，提交第一個 commit。

---

### Task 6: 前端 auth 共用模組

**Files:**
- Create: `frontend/src/api/auth.js`
- Modify: `frontend/src/pages/LoginPage.jsx`

「登入成功要做什麼」目前寫在 `LoginPage.jsx` 裡。註冊頁需要完全相同的一段，
複製一份等於兩處各自維護，抽出來共用。

- [ ] **Step 1: 建立共用模組**

建立 `frontend/src/api/auth.js`：

```javascript
import api, { getAccessTokenPayload } from './client';

// 登入與註冊成功後要做的事完全一樣：存 token、組出前端用的 user 物件、
// 寫進 localStorage。抽出來共用，避免兩個頁面各自維護一份而長歪。
export function persistSession({ access, refresh, username }) {
  localStorage.setItem('access', access);
  localStorage.setItem('refresh', refresh);

  const tokenPayload = getAccessTokenPayload();
  const nextUser = {
    name: username,
    username,
    id: tokenPayload?.user_id ?? username,
    // 只用來決定前端要不要顯示研究者專用連結（例如 /viewpoint-review）；
    // 實際的存取控制一律由後端 IsResearcher 把關，這裡不是安全邊界。
    isResearcher: Boolean(tokenPayload?.is_researcher),
  };

  localStorage.setItem('bridgeus_user', JSON.stringify(nextUser));
  return nextUser;
}

export function login({ username, password }) {
  // Django 預設的欄位名是 username。
  return api.post('/api/token/', { username, password }).then((response) => {
    return persistSession({
      access: response.data.access,
      refresh: response.data.refresh,
      username,
    });
  });
}

export function register(payload) {
  return api.post('/api/register/', payload).then((response) => {
    return persistSession({
      access: response.data.access,
      refresh: response.data.refresh,
      username: response.data.user.username,
    });
  });
}
```

- [ ] **Step 2: 讓 LoginPage 改用它**

在 `frontend/src/pages/LoginPage.jsx` 把第 4 行

```javascript
import api, { getAccessTokenPayload } from '../api/client';
```

**整行刪除**，改為

```javascript
import { login } from '../api/auth';
```

`api` 與 `getAccessTokenPayload` 目前只出現在 `handleLogin` 內（第 19、27 行），
改完之後不再有任何用途，留著 eslint 會報 unused。

把 `handleLogin` 中從 `const response = await api.post(...)` 到
`navigate('/', { replace: true });` 之間的整段替換為：

```javascript
      const nextUser = await login({ username: userId, password });
      setUser(nextUser);
      navigate('/', { replace: true });
```

`catch` 區塊完全不動（含既有的 401／429／5xx 三段式訊息）。

- [ ] **Step 3: 驗證登入頁沒壞**

```bash
cd frontend
npx eslint src/pages/LoginPage.jsx src/api/auth.js
npm run build
```

預期：eslint 無輸出（通過）、build 成功。

- [ ] **Step 4: 回報，不要 commit**

---

### Task 7: 註冊頁

**Files:**
- Create: `frontend/src/pages/RegisterPage.jsx`
- Create: `frontend/src/pages/RegisterPage.css`

- [ ] **Step 1: 建立樣式**

建立 `frontend/src/pages/RegisterPage.css`：

```css
.register-page-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
}

.register-card {
  width: 100%;
  max-width: 420px;
  padding: 32px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
}

.register-header {
  text-align: center;
  margin-bottom: 24px;
}

.register-field {
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
}

.register-field label {
  margin-bottom: 6px;
  font-size: 14px;
}

.register-field input[type='text'],
.register-field input[type='email'],
.register-field input[type='password'] {
  padding: 10px 12px;
  border: 1px solid #ccc;
  border-radius: 8px;
  font-size: 15px;
}

.register-field-error {
  margin-top: 6px;
  color: #c0392b;
  font-size: 13px;
}

.register-consent {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 20px 0;
  font-size: 14px;
}

.register-submit {
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  cursor: pointer;
}

.register-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.register-error {
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fdecea;
  color: #c0392b;
  font-size: 14px;
}

.register-footer {
  margin-top: 20px;
  text-align: center;
  font-size: 14px;
}
```

- [ ] **Step 2: 建立註冊頁**

建立 `frontend/src/pages/RegisterPage.jsx`：

```jsx
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import './RegisterPage.css';
import { register } from '../api/auth';

// 後端 accounts/consent.py 的 CONSENT_DOCUMENT_PATH。兩邊都是常數，
// 改動時要一起改——目前沒有把後端常數送到前端的機制，為了一個字串
// 不值得新增一支端點。
const CONSENT_DOCUMENT_PATH = '/consent';

const EMPTY_FIELD_ERRORS = {};

function RegisterPage({ setUser }) {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    username: '',
    email: '',
    password: '',
    password_confirm: '',
    display_name: '',
  });
  const [consent, setConsent] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState(EMPTY_FIELD_ERRORS);
  const [generalError, setGeneralError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const updateField = (name) => (event) => {
    setForm((prev) => ({ ...prev, [name]: event.target.value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setFieldErrors(EMPTY_FIELD_ERRORS);
    setGeneralError('');
    setSubmitting(true);

    try {
      const nextUser = await register({ ...form, consent });
      setUser(nextUser);
      navigate('/', { replace: true });
    } catch (error) {
      const status = error?.response?.status;
      const data = error?.response?.data;

      if (status === 400 && data && typeof data === 'object') {
        // DRF 的欄位錯誤是 { 欄位: [訊息, ...] }。逐欄位顯示，使用者才知道
        // 要改哪一格；只丟一句「註冊失敗」等於要他們自己猜。
        setFieldErrors(data);
      } else if (status === 429) {
        setGeneralError('註冊嘗試過於頻繁，請稍候再試。');
      } else if (status && status >= 500) {
        setGeneralError('註冊服務暫時無法使用，請稍後再試。');
      } else {
        setGeneralError('目前無法註冊，請檢查網路連線後再試。');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const errorFor = (name) => {
    const messages = fieldErrors[name];
    if (!messages) return null;
    const text = Array.isArray(messages) ? messages.join(' ') : String(messages);
    return <span className="register-field-error">{text}</span>;
  };

  return (
    <div className="register-page-container">
      <div className="register-card">
        <div className="register-header">
          <h1>建立帳號</h1>
          <p>歡迎加入 BridgeUs</p>
        </div>

        {generalError && <div className="register-error">{generalError}</div>}

        <form onSubmit={handleSubmit}>
          <div className="register-field">
            <label htmlFor="username">帳號名稱</label>
            <input
              id="username"
              type="text"
              value={form.username}
              onChange={updateField('username')}
              autoComplete="username"
              required
            />
            {errorFor('username')}
          </div>

          <div className="register-field">
            <label htmlFor="email">電子信箱</label>
            <input
              id="email"
              type="email"
              value={form.email}
              onChange={updateField('email')}
              autoComplete="email"
              required
            />
            {errorFor('email')}
          </div>

          <div className="register-field">
            <label htmlFor="display_name">顯示名稱（選填）</label>
            <input
              id="display_name"
              type="text"
              value={form.display_name}
              onChange={updateField('display_name')}
              maxLength={50}
            />
            {errorFor('display_name')}
          </div>

          <div className="register-field">
            <label htmlFor="password">密碼</label>
            <input
              id="password"
              type={showPassword ? 'text' : 'password'}
              value={form.password}
              onChange={updateField('password')}
              autoComplete="new-password"
              required
            />
            {errorFor('password')}
          </div>

          <div className="register-field">
            <label htmlFor="password_confirm">再次輸入密碼</label>
            <input
              id="password_confirm"
              type={showPassword ? 'text' : 'password'}
              value={form.password_confirm}
              onChange={updateField('password_confirm')}
              autoComplete="new-password"
              required
            />
            {errorFor('password_confirm')}
          </div>

          {/* 沒有「忘記密碼」流程，打錯密碼的代價是要麻煩研究者重設，
              所以提供顯示切換讓使用者自己確認。 */}
          <label className="register-consent">
            <input
              type="checkbox"
              checked={showPassword}
              onChange={(event) => setShowPassword(event.target.checked)}
            />
            <span>顯示密碼</span>
          </label>

          <label className="register-consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
            <span>
              我已閱讀並同意{' '}
              <Link to={CONSENT_DOCUMENT_PATH} target="_blank" rel="noreferrer">
                研究參與說明
              </Link>
            </span>
          </label>
          {errorFor('consent')}

          <button
            className="register-submit"
            type="submit"
            disabled={!consent || submitting}
          >
            {submitting ? '註冊中…' : '建立帳號'}
          </button>
        </form>

        <div className="register-footer">
          已經有帳號了？<Link to="/">返回登入</Link>
        </div>
      </div>
    </div>
  );
}

export default RegisterPage;
```

- [ ] **Step 3: 建立同意書頁**

建立 `frontend/src/pages/ConsentPage.jsx`：

```jsx
import { Link } from 'react-router-dom';

// 全文內容由研究團隊提供，目前是佔位文字。替換內容不需要改任何其他檔案；
// 若同意書有實質變動，記得同步更新後端 accounts/consent.py 的 CONSENT_VERSION，
// 否則新舊兩版的簽署紀錄會混在同一個版本字串底下。
function ConsentPage() {
  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 24 }}>
      <h1>研究參與說明</h1>
      <p>（此處為同意書全文，內容待研究團隊提供。）</p>
      <p>
        <Link to="/register">返回註冊</Link>
      </p>
    </div>
  );
}

export default ConsentPage;
```

- [ ] **Step 4: 驗證**

```bash
cd frontend
npx eslint src/pages/RegisterPage.jsx src/pages/ConsentPage.jsx
```

預期：無輸出。此時尚未接路由，頁面還進不去，Task 8 才會掛上。

- [ ] **Step 5: 回報，不要 commit**

---

### Task 8: 接上路由與登入頁連結

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/pages/LoginPage.jsx`

- [ ] **Step 1: 加 import**

在 `frontend/src/App.jsx` 的 `import LoginPage from './pages/LoginPage'` 之後加入：

```javascript
import RegisterPage from './pages/RegisterPage'
import ConsentPage from './pages/ConsentPage'
```

- [ ] **Step 2: 在未登入分支加路由**

`App.jsx` 的 `if (!user)` 分支目前只有一條 catch-all。把它替換為：

```jsx
  if (!user) {
    return (
      <Routes>
        {/* 這兩條必須排在 catch-all 之前，否則 path="*" 會把它們吃掉。 */}
        <Route path="/register" element={<RegisterPage setUser={handleLogin} />} />
        <Route path="/consent" element={<ConsentPage />} />
        <Route path="*" element={<LoginPage setUser={handleLogin} authMessage={authMessage} />} />
      </Routes>
    );
  }
```

`handleLogin` 就是登入頁用的同一個 callback（它只做 `setUser` 與清空狀態），
註冊成功後走同一條路徑，不需要另外寫一個。

- [ ] **Step 3: 登入頁加註冊連結**

在 `frontend/src/pages/LoginPage.jsx` 的表單之後、卡片結束之前加入：

```jsx
        <div className="login-footer">
          還沒有帳號？<Link to="/register">立即註冊</Link>
        </div>
```

並在檔案頂端 import 區加入 `Link`：

```javascript
import { Link, useNavigate } from 'react-router-dom';
```

在 `frontend/src/pages/LoginPage.css` 末尾加入：

```css
.login-footer {
  margin-top: 20px;
  text-align: center;
  font-size: 14px;
}
```

- [ ] **Step 4: 驗證**

```bash
cd frontend
npx eslint src/
npm run build
```

預期：eslint 無輸出、build 成功。

- [ ] **Step 5: 回報，不要 commit**

> **檢查點 2（orchestrator）**：前端完整，提交第二個 commit。

---

### Task 9: 端對端實機驗證

**Files:** 無（純驗證）

- [ ] **Step 1: 啟動後端**

```bash
cd backend
DB_ENGINE=sqlite uv run python manage.py migrate --noinput
DB_ENGINE=sqlite uv run python manage.py runserver 0.0.0.0:8005 --noreload
```

- [ ] **Step 2: 用 curl 驗證註冊契約**

另開一個終端機：

```bash
curl -s -X POST http://127.0.0.1:8005/api/register/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"e2e01","email":"e2e01@example.com","password":"Xk9$mVpq2Lz","password_confirm":"Xk9$mVpq2Lz","display_name":"測試","consent":true}'
```

預期：`201`，回應含 `user`、`access`、`refresh`，且 `user.display_name` 為 `測試`。

- [ ] **Step 3: 驗證回來的 token 真的能用**

把上一步的 `access` 值填入：

```bash
curl -s http://127.0.0.1:8005/api/me/ -H 'Authorization: Bearer <access>'
```

預期：`200`，含 `"username": "e2e01"`、`"display_name": "測試"`、`"is_researcher": false`。

- [ ] **Step 4: 驗證未勾同意會被擋**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8005/api/register/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"e2e02","email":"e2e02@example.com","password":"Xk9$mVpq2Lz","password_confirm":"Xk9$mVpq2Lz","consent":false}'
```

預期：`400`。

- [ ] **Step 5: 瀏覽器實測**

啟動前端（`cd frontend && npm run dev`），在瀏覽器：

1. 開 `/register`，確認頁面正常顯示（不是被 catch-all 導到登入頁）
2. 未勾同意時「建立帳號」按鈕是停用的
3. 點「研究參與說明」會開啟同意書頁
4. 故意讓兩次密碼不一致 → 該欄位下方出現錯誤訊息
5. 故意用已存在的帳號名稱 → username 欄位下方出現錯誤訊息
6. 正常填寫送出 → 直接進入首頁（已登入狀態），不需要再登入一次
7. 從登入頁點「立即註冊」連結會到註冊頁

- [ ] **Step 6: 後端全量回歸**

```bash
cd backend
uv run python manage.py test --noinput
```

**執行期間不可同時啟動另一個 `manage.py test`。** 預期失敗數與改動前相同：
已知的 16 個 error 分別是 6 個 `chat/tests_*` 匯入已刪除的 model、8 個核能分類器
缺 ML 權重檔、2 個 tunnel 斷線。若出現其他失敗，回報。

- [ ] **Step 7: 回報，不要 commit**

逐項附上實際輸出。這是唯一一次完整驗證，回報請詳細。

---

## 完成標準

- [ ] `POST /api/register/` 回 201 且回傳的 token 可直接呼叫 `/api/me/`
- [ ] `consent_version` 存的是後端常數值，前端傳什麼都不影響
- [ ] 自助註冊的帳號 `is_research_subject=True`；研究者代開的維持 `False`
- [ ] access token 帶 `is_researcher` claim
- [ ] 未勾同意、密碼不一致、密碼太像帳號或信箱、重複 username／email（含只差大小寫）皆回 400
- [ ] 超過 `register` 限流回 429
- [ ] `/register` 與 `/consent` 在未登入狀態可達
- [ ] 註冊成功直接進入已登入狀態
- [ ] `npm run lint` 與 `npm run build` 通過
- [ ] 整個功能只落成 2 個 commit

## 本計畫刻意不做

見 spec 的「不做的事」：email 驗證信、忘記密碼、email 變更、以 email 登入 —— 四項都卡在專案沒有 SMTP。另外不動既有的 `POST /api/accounts/`（研究者代開帳號）。

同意書全文由研究團隊提供，`ConsentPage.jsx` 目前是佔位文字。替換內容不需要改其他檔案，但**若同意書有實質變動，必須同步更新 `accounts/consent.py` 的 `CONSENT_VERSION`**，否則新舊兩版的簽署紀錄會混在同一個版本字串底下。
