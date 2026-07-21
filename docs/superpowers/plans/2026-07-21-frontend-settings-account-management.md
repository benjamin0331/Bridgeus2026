# 前端設定介面 — 研究者帳號管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sidebar 齒輪進到 `/settings`；研究者在該頁用前端面板管理帳號（新增／停用啟用／升降研究者／重設密碼），背後由新增的 `IsResearcher` REST API 支援。

**Architecture:** 後端在既有 `api` app 新增 `accounts/` 系列 endpoint（沿用 `IsResearcher`；升降研究者＝加入/移出「研究者」Group，`is_staff` 由既有 signal 自動連動）。前端新增 `SettingsPage`，齒輪改成按鈕導向 `/settings`，非研究者顯示佔位。安全護欄：不能動 superuser、不能停用/取消自己。

**Tech Stack:** Django 6.0.4 + DRF、pytest-django（後端 TDD）；React + Vite（前端，無測試 runner，用 `npm run lint` + `npm run build` 驗證）。

參考 spec：[docs/superpowers/specs/2026-07-21-frontend-settings-account-management-design.md](../specs/2026-07-21-frontend-settings-account-management-design.md)

後端指令在 `backend/` 下用 `uv run pytest ...`（測試套件慢，每次 ~2-3 分鐘 DB 設定，只跑指定的類別）。前端指令在 `frontend/` 下。

---

## File Structure

- **Modify** `backend/api/serializers.py` — 新增 `AccountListSerializer` / `AccountCreateSerializer` / `AccountUpdateSerializer` / `PasswordResetSerializer`。
- **Modify** `backend/api/views.py` — 新增 `AccountListCreateView` / `AccountDetailView` / `AccountPasswordResetView` + 一個護欄 helper。
- **Modify** `backend/api/urls.py` — 新增 3 條 route。
- **Create** `backend/api/tests_account_management.py` — 後端測試。
- **Create** `frontend/src/pages/SettingsPage.jsx` + `frontend/src/pages/SettingsPage.css` — 設定頁。
- **Modify** `frontend/src/App.jsx` — 加 `/settings` route。
- **Modify** `frontend/src/components/Sidebar.jsx` — 齒輪改按鈕。

---

## Task 1: 帳號清單 + 建立帳號 API（GET/POST /api/accounts/）

**Files:**
- Modify: `backend/api/serializers.py`
- Modify: `backend/api/views.py`
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_account_management.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/api/tests_account_management.py`：

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest api/tests_account_management.py::AccountListCreateTests -v`
Expected: FAIL — `/api/accounts/` 尚未存在（404 / URL 解析失敗）。

- [ ] **Step 3: 新增 serializers**

在 `backend/api/serializers.py`，於頂端 import 區塊追加：

```python
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password as dj_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
```

在檔案末端（或 `ViewpointNodeReviewDecisionSerializer` 之後）追加：

```python
class AccountListSerializer(serializers.ModelSerializer):
    """帳號管理清單／回傳用（唯讀）。is_researcher 由「研究者」Group 推導。"""

    is_researcher = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "is_active",
            "is_researcher",
            "is_superuser",
            "last_login",
            "date_joined",
        ]
        read_only_fields = fields

    def get_is_researcher(self, obj):
        return obj.groups.filter(name=RESEARCHER_GROUP_NAME).exists()


class AccountCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)
    is_researcher = serializers.BooleanField(required=False, default=False)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("這個帳號名稱已經有人用了。")
        return value

    def validate_password(self, value):
        try:
            dj_validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        is_researcher = validated_data.pop("is_researcher", False)
        user = User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )
        if is_researcher:
            group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
            user.groups.add(group)  # signal 會把 is_staff 設成 True
        return user


class AccountUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    is_researcher = serializers.BooleanField(required=False)


class PasswordResetSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        try:
            dj_validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value
```

- [ ] **Step 4: 新增 view + URL**

在 `backend/api/views.py`，把第 9 行 `from django.contrib.auth.models import User` 改成：

```python
from django.contrib.auth.models import Group, User
```

在 import 區塊的 `from .serializers import ...`（若無則新增一行）帶入新 serializer。先確認 views.py 目前怎麼 import serializer——用 grep：`grep -n "from .serializers import" api/views.py`。若已有該行，把下列名稱補進去；若沒有，新增：

```python
from .serializers import (
    AccountCreateSerializer,
    AccountListSerializer,
)
```

在 `ViewpointReviewDecisionView` 之後（約 line 1483）追加：

```python
class AccountListCreateView(generics.ListCreateAPIView):
    """研究者專用：帳號清單 + 新增帳號（前端設定頁）。"""

    permission_classes = [IsResearcher]

    def get_queryset(self):
        return User.objects.prefetch_related("groups").order_by("-date_joined")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AccountCreateSerializer
        return AccountListSerializer

    def create(self, request, *args, **kwargs):
        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            AccountListSerializer(user).data, status=status.HTTP_201_CREATED
        )
```

在 `backend/api/urls.py`，於 `summary/viewpoints/...` 兩條之後追加：

```python
    path('accounts/', views.AccountListCreateView.as_view()),
```

- [ ] **Step 5: 執行測試確認通過**

Run: `uv run pytest api/tests_account_management.py::AccountListCreateTests -v`
Expected: PASS（6 個測試全過）。

- [ ] **Step 6: Commit**

```bash
git add api/serializers.py api/views.py api/urls.py api/tests_account_management.py
git commit -m "feat(accounts): add researcher-only account list/create API"
```

---

## Task 2: 更新帳號 API（PATCH /api/accounts/<id>/）+ 護欄

**Files:**
- Modify: `backend/api/views.py`
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_account_management.py`（追加類別）

- [ ] **Step 1: 寫失敗測試**

在 `backend/api/tests_account_management.py` 末端追加：

```python
class AccountUpdateTests(APITestCase):
    def setUp(self):
        self.researcher = _make_researcher()
        self.other = _make_researcher(username="other_researcher")
        self.participant = User.objects.create_user(
            username="participant", password="pw-strong-123"
        )
        self.superuser = User.objects.create_superuser(
            username="root", password="pw-strong-123"
        )
        self.client.force_authenticate(user=self.researcher)

    def _url(self, user):
        return f"/api/accounts/{user.id}/"

    def test_deactivate_and_reactivate_participant(self):
        response = self.client.patch(self._url(self.participant), {"is_active": False})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.participant.refresh_from_db()
        self.assertFalse(self.participant.is_active)

        response = self.client.patch(self._url(self.participant), {"is_active": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.participant.refresh_from_db()
        self.assertTrue(self.participant.is_active)

    def test_promote_participant_to_researcher_sets_staff(self):
        response = self.client.patch(
            self._url(self.participant), {"is_researcher": True}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.participant.refresh_from_db()
        self.assertTrue(
            self.participant.groups.filter(name=RESEARCHER_GROUP_NAME).exists()
        )
        self.assertTrue(self.participant.is_staff)

    def test_demote_researcher_clears_staff(self):
        response = self.client.patch(self._url(self.other), {"is_researcher": False})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.other.refresh_from_db()
        self.assertFalse(self.other.groups.filter(name=RESEARCHER_GROUP_NAME).exists())
        self.assertFalse(self.other.is_staff)

    def test_cannot_modify_superuser(self):
        response = self.client.patch(self._url(self.superuser), {"is_active": False})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_active)

    def test_cannot_deactivate_self(self):
        response = self.client.patch(self._url(self.researcher), {"is_active": False})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.researcher.refresh_from_db()
        self.assertTrue(self.researcher.is_active)

    def test_cannot_demote_self(self):
        response = self.client.patch(
            self._url(self.researcher), {"is_researcher": False}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.researcher.refresh_from_db()
        self.assertTrue(
            self.researcher.groups.filter(name=RESEARCHER_GROUP_NAME).exists()
        )
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest api/tests_account_management.py::AccountUpdateTests -v`
Expected: FAIL — PATCH endpoint 尚未存在。

- [ ] **Step 3: 新增護欄 helper + PATCH view**

在 `backend/api/views.py`，把 Task 1 的 serializer import 補上 `AccountUpdateSerializer`：

```python
from .serializers import (
    AccountCreateSerializer,
    AccountListSerializer,
    AccountUpdateSerializer,
)
```

在 `AccountListCreateView` 之後追加：

```python
def _account_target_or_response(pk):
    """取目標帳號；找不到回 (None, 404 Response)。"""
    try:
        return User.objects.get(pk=pk), None
    except User.DoesNotExist:
        return None, Response(
            {"detail": "找不到這個帳號。"}, status=status.HTTP_404_NOT_FOUND
        )


class AccountDetailView(APIView):
    """研究者專用：更新單一帳號（停用/啟用、升/降研究者）。"""

    permission_classes = [IsResearcher]

    def patch(self, request, pk: int):
        target, error = _account_target_or_response(pk)
        if error:
            return error

        # 護欄 1：不能動 superuser。
        if target.is_superuser:
            return Response(
                {"detail": "不能對系統管理員帳號執行這個操作。"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 護欄 2：不能停用自己、不能取消自己的研究者身分。
        if target == request.user:
            if data.get("is_active") is False:
                return Response(
                    {"detail": "不能停用自己的帳號。"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if data.get("is_researcher") is False:
                return Response(
                    {"detail": "不能取消自己的研究者身分。"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if "is_active" in data:
            target.is_active = data["is_active"]
            target.save(update_fields=["is_active"])

        if "is_researcher" in data:
            group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
            if data["is_researcher"]:
                target.groups.add(group)  # signal 連動 is_staff
            else:
                target.groups.remove(group)

        return Response(AccountListSerializer(target).data)
```

`RESEARCHER_GROUP_NAME` 需可用——確認 views.py 是否已 import：`grep -n "RESEARCHER_GROUP_NAME" api/views.py`。若沒有，把 line 24 的 import 改成：

```python
from .permissions import IsGodotServiceToken, IsResearcher, RESEARCHER_GROUP_NAME
```

在 `backend/api/urls.py` 的 `accounts/` 之後追加：

```python
    path('accounts/<int:pk>/', views.AccountDetailView.as_view()),
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest api/tests_account_management.py::AccountUpdateTests -v`
Expected: PASS（6 個測試全過）。

- [ ] **Step 5: Commit**

```bash
git add api/views.py api/urls.py api/tests_account_management.py
git commit -m "feat(accounts): add account update API with superuser/self guardrails"
```

---

## Task 3: 重設密碼 API（POST /api/accounts/<id>/reset-password/）

**Files:**
- Modify: `backend/api/views.py`
- Modify: `backend/api/urls.py`
- Test: `backend/api/tests_account_management.py`（追加類別）

- [ ] **Step 1: 寫失敗測試**

在 `backend/api/tests_account_management.py` 末端追加：

```python
class AccountPasswordResetTests(APITestCase):
    def setUp(self):
        self.researcher = _make_researcher()
        self.participant = User.objects.create_user(
            username="participant", password="old-pw-123456"
        )
        self.superuser = User.objects.create_superuser(
            username="root", password="pw-strong-123"
        )
        self.client.force_authenticate(user=self.researcher)

    def _url(self, user):
        return f"/api/accounts/{user.id}/reset-password/"

    def test_reset_sets_new_password(self):
        response = self.client.post(
            self._url(self.participant), {"password": "brand-new-987654"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.participant.refresh_from_db()
        self.assertTrue(self.participant.check_password("brand-new-987654"))
        self.assertFalse(self.participant.check_password("old-pw-123456"))

    def test_reset_rejects_weak_password(self):
        response = self.client.post(self._url(self.participant), {"password": "123"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.participant.refresh_from_db()
        self.assertTrue(self.participant.check_password("old-pw-123456"))

    def test_cannot_reset_superuser_password(self):
        response = self.client.post(
            self._url(self.superuser), {"password": "brand-new-987654"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.check_password("pw-strong-123"))
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest api/tests_account_management.py::AccountPasswordResetTests -v`
Expected: FAIL — reset-password endpoint 尚未存在。

- [ ] **Step 3: 新增 view + URL**

在 `backend/api/views.py` 的 serializer import 補上 `PasswordResetSerializer`：

```python
from .serializers import (
    AccountCreateSerializer,
    AccountListSerializer,
    AccountUpdateSerializer,
    PasswordResetSerializer,
)
```

在 `AccountDetailView` 之後追加：

```python
class AccountPasswordResetView(APIView):
    """研究者專用：重設某帳號的密碼。"""

    permission_classes = [IsResearcher]

    def post(self, request, pk: int):
        target, error = _account_target_or_response(pk)
        if error:
            return error

        if target.is_superuser:
            return Response(
                {"detail": "不能重設系統管理員帳號的密碼。"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target.set_password(serializer.validated_data["password"])
        target.save(update_fields=["password"])
        return Response({"detail": "密碼已重設。"})
```

在 `backend/api/urls.py` 的 `accounts/<int:pk>/` 之後追加：

```python
    path('accounts/<int:pk>/reset-password/', views.AccountPasswordResetView.as_view()),
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest api/tests_account_management.py -v`
Expected: PASS（Task 1-3 共 15 個測試全過）。

- [ ] **Step 5: Commit**

```bash
git add api/views.py api/urls.py api/tests_account_management.py
git commit -m "feat(accounts): add researcher password-reset API with superuser guard"
```

---

## Task 4: 前端齒輪按鈕 + `/settings` route + 頁面骨架（含研究者 gate）

前端無測試 runner，用 `npm run lint` + `npm run build` 驗證（能編譯、無 lint error）。

**Files:**
- Create: `frontend/src/pages/SettingsPage.jsx`
- Create: `frontend/src/pages/SettingsPage.css`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/Sidebar.jsx`

- [ ] **Step 1: 建立 SettingsPage 骨架**

Create `frontend/src/pages/SettingsPage.jsx`：

```jsx
import './SettingsPage.css';

// 設定頁。目前只放研究者帳號管理；一般個人設定之後再加。齒輪對所有人可見，
// 非研究者進來看到佔位。實際存取控制在後端 IsResearcher，這裡只決定顯示。
function SettingsPage({ user }) {
  const isResearcher = Boolean(user?.isResearcher);

  if (!isResearcher) {
    return (
      <div className="settings-page">
        <div className="settings-heading">
          <h1>設定</h1>
        </div>
        <div className="settings-empty-card">尚無設定項。</div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-heading">
        <span className="settings-kicker">研究者</span>
        <h1>帳號管理</h1>
        <p>新增、停用／啟用帳號，設定研究者身分，或重設密碼。</p>
      </div>
      <div className="settings-empty-card">帳號管理面板即將載入…</div>
    </div>
  );
}

export default SettingsPage;
```

Create `frontend/src/pages/SettingsPage.css`：

```css
.settings-page {
  padding: 24px;
  max-width: 960px;
  margin: 0 auto;
}

.settings-heading h1 {
  margin: 4px 0 8px;
}

.settings-kicker {
  font-size: 12px;
  letter-spacing: 0.08em;
  color: #6b7280;
  text-transform: uppercase;
}

.settings-empty-card {
  margin-top: 16px;
  padding: 32px;
  text-align: center;
  color: #6b7280;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
}
```

- [ ] **Step 2: 加 route**

在 `frontend/src/App.jsx`：import 區塊（其他 page import 附近）加：

```jsx
import SettingsPage from './pages/SettingsPage';
```

在 `<Routes>` 內、`/viewpoint-review` route 附近加（`user` 已在 App 作用域內）：

```jsx
            <Route path="/settings" element={<SettingsPage user={user} />} />
```

- [ ] **Step 3: 齒輪改成按鈕**

在 `frontend/src/components/Sidebar.jsx`，把這行（約 line 55）：

```jsx
          <img src="/settings.png" alt="Settings" className="utility-icon" />
```

換成（比照相鄰的成就/歷史按鈕）：

```jsx
          <button
            className="sidebar-icon-btn"
            type="button"
            onClick={() => handleNavigate('/settings')}
            aria-label="設定"
            title="設定"
          >
            <img src="/settings.png" alt="" className="utility-icon" />
          </button>
```

- [ ] **Step 4: 驗證編譯**

Run（在 `frontend/`）：`npm run lint && npm run build`
Expected: lint 無 error、build 成功。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/SettingsPage.jsx frontend/src/pages/SettingsPage.css frontend/src/App.jsx frontend/src/components/Sidebar.jsx
git commit -m "feat(frontend): add settings route, gear button, researcher gate"
```

---

## Task 5: 帳號管理面板（清單 + 新增 + 操作）

把 Task 4 的佔位換成完整面板，串接 Task 1-3 的 API。

**Files:**
- Modify: `frontend/src/pages/SettingsPage.jsx`
- Modify: `frontend/src/pages/SettingsPage.css`

- [ ] **Step 1: 實作面板**

把 `frontend/src/pages/SettingsPage.jsx` 整份改成：

```jsx
import { useCallback, useEffect, useState } from 'react';
import api from '../api/client';
import './SettingsPage.css';

// 設定頁。研究者帳號管理面板；一般個人設定之後再加。實際存取控制在後端
// IsResearcher，這裡只決定顯示，非研究者看到佔位。

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-TW', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function SettingsPage({ user }) {
  const isResearcher = Boolean(user?.isResearcher);

  const [accounts, setAccounts] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');

  // 新增帳號表單
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newIsResearcher, setNewIsResearcher] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  const loadAccounts = useCallback(async () => {
    setIsLoading(true);
    setError('');
    try {
      const response = await api.get('/api/accounts/');
      setAccounts(Array.isArray(response.data) ? response.data : []);
    } catch (requestError) {
      setAccounts([]);
      setError(
        requestError?.response?.status === 403
          ? '這個頁面只開放給研究者帳號使用。'
          : requestError?.response?.data?.detail || '目前無法讀取帳號清單。'
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isResearcher) void loadAccounts();
  }, [isResearcher, loadAccounts]);

  const extractError = (requestError, fallback) => {
    const data = requestError?.response?.data;
    if (data?.detail) return data.detail;
    if (data && typeof data === 'object') {
      const firstKey = Object.keys(data)[0];
      const firstVal = firstKey ? data[firstKey] : null;
      if (Array.isArray(firstVal)) return firstVal[0];
      if (typeof firstVal === 'string') return firstVal;
    }
    return fallback;
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    if (isCreating) return;
    setIsCreating(true);
    setActionError('');
    try {
      await api.post('/api/accounts/', {
        username: newUsername,
        password: newPassword,
        is_researcher: newIsResearcher,
      });
      setNewUsername('');
      setNewPassword('');
      setNewIsResearcher(false);
      await loadAccounts();
    } catch (requestError) {
      setActionError(extractError(requestError, '新增帳號失敗，請再試一次。'));
    } finally {
      setIsCreating(false);
    }
  };

  const patchAccount = async (account, payload, fallbackMsg) => {
    setActionError('');
    try {
      await api.patch(`/api/accounts/${account.id}/`, payload);
      await loadAccounts();
    } catch (requestError) {
      setActionError(extractError(requestError, fallbackMsg));
    }
  };

  const handleToggleActive = (account) =>
    patchAccount(account, { is_active: !account.is_active }, '更新狀態失敗。');

  const handleToggleResearcher = (account) =>
    patchAccount(account, { is_researcher: !account.is_researcher }, '更新研究者身分失敗。');

  const handleResetPassword = async (account) => {
    const next = window.prompt(`為「${account.username}」設定新密碼：`);
    if (!next) return;
    setActionError('');
    try {
      await api.post(`/api/accounts/${account.id}/reset-password/`, { password: next });
      window.alert('密碼已重設。');
    } catch (requestError) {
      setActionError(extractError(requestError, '重設密碼失敗。'));
    }
  };

  if (!isResearcher) {
    return (
      <div className="settings-page">
        <div className="settings-heading"><h1>設定</h1></div>
        <div className="settings-empty-card">尚無設定項。</div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-heading">
        <span className="settings-kicker">研究者</span>
        <h1>帳號管理</h1>
        <p>新增、停用／啟用帳號，設定研究者身分，或重設密碼。</p>
      </div>

      <form className="settings-create-form" onSubmit={handleCreate}>
        <h2>新增帳號</h2>
        <div className="settings-create-row">
          <input
            type="text"
            placeholder="帳號名稱"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            required
          />
          <input
            type="text"
            placeholder="初始密碼"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
          />
          <label className="settings-checkbox">
            <input
              type="checkbox"
              checked={newIsResearcher}
              onChange={(e) => setNewIsResearcher(e.target.checked)}
            />
            設為研究者
          </label>
          <button type="submit" disabled={isCreating}>建立</button>
        </div>
      </form>

      {actionError && <div className="settings-action-error">{actionError}</div>}

      <div className="settings-list">
        {isLoading && <div className="settings-empty-card">正在讀取…</div>}
        {!isLoading && error && <div className="settings-empty-card error">{error}</div>}
        {!isLoading && !error && accounts.length === 0 && (
          <div className="settings-empty-card">目前沒有帳號。</div>
        )}
        {!isLoading && !error && accounts.length > 0 && (
          <table className="settings-table">
            <thead>
              <tr>
                <th>帳號</th>
                <th>狀態</th>
                <th>研究者</th>
                <th>最後登入</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id}>
                  <td>{account.username}{account.is_superuser ? '（管理員）' : ''}</td>
                  <td>{account.is_active ? '啟用' : '停用'}</td>
                  <td>{account.is_researcher ? '是' : '否'}</td>
                  <td>{formatTime(account.last_login)}</td>
                  <td className="settings-actions">
                    <button
                      type="button"
                      disabled={account.is_superuser}
                      onClick={() => handleToggleActive(account)}
                    >
                      {account.is_active ? '停用' : '啟用'}
                    </button>
                    <button
                      type="button"
                      disabled={account.is_superuser}
                      onClick={() => handleToggleResearcher(account)}
                    >
                      {account.is_researcher ? '取消研究者' : '設為研究者'}
                    </button>
                    <button
                      type="button"
                      disabled={account.is_superuser}
                      onClick={() => handleResetPassword(account)}
                    >
                      重設密碼
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default SettingsPage;
```

- [ ] **Step 2: 補面板樣式**

在 `frontend/src/pages/SettingsPage.css` 末端追加：

```css
.settings-create-form {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
}

.settings-create-form h2 {
  margin: 0 0 12px;
  font-size: 16px;
}

.settings-create-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.settings-create-row input[type="text"] {
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
}

.settings-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
}

.settings-create-row button,
.settings-actions button {
  padding: 6px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}

.settings-create-row button:disabled,
.settings-actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.settings-action-error {
  margin-top: 12px;
  color: #b91c1c;
}

.settings-list {
  margin-top: 16px;
}

.settings-table {
  width: 100%;
  border-collapse: collapse;
}

.settings-table th,
.settings-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
  font-size: 14px;
}

.settings-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.settings-empty-card.error {
  color: #b91c1c;
}
```

- [ ] **Step 3: 驗證編譯**

Run（在 `frontend/`）：`npm run lint && npm run build`
Expected: lint 無 error、build 成功。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SettingsPage.jsx frontend/src/pages/SettingsPage.css
git commit -m "feat(frontend): implement researcher account management panel"
```

---

## Task 6: 收尾（回歸 + docs）

**Files:**
- Modify: `CONTEXT.md`

- [ ] **Step 1: 後端相關回歸**

Run（在 `backend/`）：`uv run pytest api/tests_account_management.py api/tests_supervisor_admin.py api/tests_viewpoint_review.py api/tests_token_is_researcher_claim.py -v`
Expected: 全 PASS，無 error/failure。

- [ ] **Step 2: 更新 CONTEXT.md**

在 `CONTEXT.md`「模組現況」表 M1 認證 那列的重點，或「待辦」附近，記一筆：前端設定頁（齒輪 → `/settings`）研究者帳號管理面板已完成（清單／新增／停用啟用／升降研究者／重設密碼；`/api/accounts/*` 由 IsResearcher 把關；護欄：不能動 superuser、不能停用/取消自己）。措辭與周邊條目一致。

- [ ] **Step 3: Commit**

```bash
git add CONTEXT.md
git commit -m "docs: record frontend settings account management as done"
```

---

## Self-Review Notes

- **Spec 覆蓋**：§2 後端 API → Task 1（list/create）、Task 2（PATCH）、Task 3（reset-password）；§3 安全護欄 → Task 2/3 的 superuser + self guard；§前端 Sidebar/Route/SettingsPage → Task 4（骨架+gate+齒輪）、Task 5（面板）；§不做的事（個人設定/刪除/profile 欄位）→ 計畫未觸及。
- **無 placeholder**：所有 code step 為完整可貼上內容。
- **型別/名稱一致**：serializer 名稱（`AccountListSerializer`/`AccountCreateSerializer`/`AccountUpdateSerializer`/`PasswordResetSerializer`）在 Task 1-3 定義與 import 一致；view 名稱（`AccountListCreateView`/`AccountDetailView`/`AccountPasswordResetView`）與 urls.py route 對應；helper `_account_target_or_response` 於 Task 2 定義、Task 3 沿用；前端 API 路徑 `/api/accounts/`、`/api/accounts/<id>/`、`/api/accounts/<id>/reset-password/` 與後端 urls 一致；`RESEARCHER_GROUP_NAME` 一律由 `api.permissions` 來。
- **依賴順序**：Task 3 用到 Task 2 定義的 `_account_target_or_response`，需照序執行。
