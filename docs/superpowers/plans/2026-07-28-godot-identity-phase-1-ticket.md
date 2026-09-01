# Godot 身份層階段一：一次性入場券 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 後端提供一次性、60 秒到期的 Godot 大廳入場券（主功能發券、Godot server 用服務金鑰兌換成 user_id），並移除會產生無主帳號的訪客登入端點。

**Architecture:** 新增 `GodotEntryTicket` model 與 `api/godot_tickets.py` 服務層（發券／兌換的邏輯都在這裡，view 只做 HTTP 轉接）。兌換用 `select_for_update` 行鎖保證一次性。兌換端點沿用既有 `GodotMatchRoomView` 的 `authentication_classes = [] + IsGodotServiceToken` 寫法。

**Tech Stack:** Django 6 + DRF、pytest + pytest-django、PostgreSQL。

**依據 spec:** `docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md`（§5 全部、§13 的 ticket 與 guest 測試項）

**範圍界線：** 本計畫**只動後端**。Godot 端（`Backend.gd` / `game.gd` 的 ticket 交接與 spawn 授權）是階段二，不在這裡做——本階段結束時 Godot 仍在呼叫已被移除的 `/api/guest/`，這是預期的中間狀態，階段二會接上。

---

## 執行前必讀

- 後端指令一律在 `backend/` 目錄下用 `uv run` 執行。
- **測試必須序列執行**：後端測試共用同一個 Postgres test database，同時跑兩個測試程序會失敗。
- 測試指令一律帶絕對路徑 `cd`，否則 `uv run` 找不到虛擬環境並以 `Failed to spawn: pytest` 靜默失敗：
  `cd /Users/light/code/backend && uv run pytest <目標>`
- 每個 Task 只跑該 Task 的測試檔，最後一個 Task 再跑迴歸。
- 目前分支：`feat/Light`。不要切分支、不要開 worktree（`backend/.venv` 沒進版控，新 worktree 會沒有虛擬環境）。

### ⚠️ `force_authenticate` 會跳過 `authentication_classes`

DRF 的 `force_authenticate` 直接塞 `request.user`，**完全不走 authentication 流程**，所以任何只用它的測試都驗證不到「這個 view 用哪個 authentication class」。本計畫 Task 3 的發券端點**必須**有一個用真 JWT 的測試：

```python
from rest_framework_simplejwt.tokens import AccessToken

token = str(AccessToken.for_user(user))
client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
```

專案設定用的是 `JWTAuthentication`（非 stateless），所以 `request.user` 是真的 DB User，可以直接當 FK 指派。

---

## 檔案結構

| 檔案 | 責任 |
|---|---|
| `backend/api/models.py`（修改） | 新增 `GodotEntryTicket` model |
| `backend/api/migrations/0022_godotentryticket.py`（新增，由 makemigrations 產生） | schema |
| `backend/api/godot_tickets.py`（新增） | 發券／兌換的全部邏輯；TTL 常數；一次性的行鎖保證 |
| `backend/api/views.py`（修改） | 新增兩個 view；刪除 `GuestLoginView` |
| `backend/api/urls.py`（修改） | 新增兩條路由；刪除 `guest/` |
| `backend/api/tests_godot_tickets.py`（新增） | 服務層 + 兩支端點的測試 |

---

## Task 1: `GodotEntryTicket` model

**Files:**
- Modify: `backend/api/models.py`（接在 `DialogueEntryAssignment` 之後，檔案尾端）
- Create: `backend/api/migrations/0022_godotentryticket.py`（由 makemigrations 產生，不要手寫）

- [ ] **Step 1: 加入 model**

在 `backend/api/models.py` 最後面加上：

```python
class GodotEntryTicket(models.Model):
    """一次性的 Godot 大廳入場券。主功能發，Godot server 用服務金鑰兌換。

    存在的理由：Godot server 只需要知道「這個 peer 是哪個 user」，不需要、也不該
    持有主功能的長效 access token——長效憑證一旦進了遊戲 server 的記憶體與 log
    就很難收回。券短效、一次性、只有持服務金鑰的一方能兌換，洩漏後果有界。
    見 docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md §5。
    """

    token = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="godot_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"ticket user={self.user_id} redeemed={self.redeemed_at is not None}"
```

- [ ] **Step 2: 產生 migration**

Run: `cd /Users/light/code/backend && uv run python manage.py makemigrations api`
Expected: 產生 `api/migrations/0022_godotentryticket.py`，輸出含 `+ Create model GodotEntryTicket`

- [ ] **Step 3: 套用 migration 確認無誤**

Run: `cd /Users/light/code/backend && uv run python manage.py migrate api`
Expected: `Applying api.0022_godotentryticket... OK`

- [ ] **Step 4: Commit**

```bash
git add backend/api/models.py backend/api/migrations/0022_godotentryticket.py
git commit -m "feat(m1): add GodotEntryTicket model for godot lobby entry"
```

---

## Task 2: 發券與兌換的服務層

**Files:**
- Create: `backend/api/godot_tickets.py`
- Test: `backend/api/tests_godot_tickets.py`

- [ ] **Step 1: 寫失敗的測試**

建立 `backend/api/tests_godot_tickets.py`：

```python
"""
pytest tests for Godot 一次性入場券（服務層 + 端點）。

Run from backend/:
    pytest api/tests_godot_tickets.py -v
"""
import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from api.godot_tickets import TICKET_TTL_SECONDS, issue_ticket, redeem_ticket
from api.models import GodotEntryTicket

User = get_user_model()


@pytest.mark.django_db
def test_issue_ticket_creates_unredeemed_ticket_with_ttl():
    user = User.objects.create_user(username="u1", password="pw")

    ticket = issue_ticket(user=user)

    assert ticket.user_id == user.id
    assert ticket.redeemed_at is None
    assert len(ticket.token) >= 32
    delta = ticket.expires_at - timezone.now()
    assert timedelta(seconds=TICKET_TTL_SECONDS - 5) < delta <= timedelta(
        seconds=TICKET_TTL_SECONDS
    )


@pytest.mark.django_db
def test_issue_ticket_tokens_are_unique():
    user = User.objects.create_user(username="u1", password="pw")

    tokens = {issue_ticket(user=user).token for _ in range(5)}

    assert len(tokens) == 5


@pytest.mark.django_db
def test_redeem_returns_user_and_marks_redeemed():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    redeemed = redeem_ticket(token=ticket.token)

    assert redeemed is not None
    assert redeemed.id == user.id
    ticket.refresh_from_db()
    assert ticket.redeemed_at is not None


@pytest.mark.django_db
def test_redeem_twice_fails_the_second_time():
    """一次性：第二次兌換必須失敗，否則同一張券可以讓兩個 peer 冒充同一人。"""
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    assert redeem_ticket(token=ticket.token) is not None
    assert redeem_ticket(token=ticket.token) is None


@pytest.mark.django_db
def test_redeem_expired_ticket_fails():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    ticket.expires_at = timezone.now() - timedelta(seconds=1)
    ticket.save(update_fields=["expires_at"])

    assert redeem_ticket(token=ticket.token) is None


@pytest.mark.django_db
def test_redeem_unknown_token_fails():
    assert redeem_ticket(token="does-not-exist") is None


@pytest.mark.django_db
def test_redeem_empty_token_fails():
    assert redeem_ticket(token="") is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_redeem_only_one_succeeds():
    """兩個 peer 同時送同一張券，只能有一個拿到身份。

    一次性語意靠 select_for_update 的行鎖保證；沒有鎖的話兩邊都會讀到
    redeemed_at is None 而同時成功。需要 transaction=True 才有真實的 commit 邊界。
    """
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    results = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            results.append(redeem_ticket(token=ticket.token))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert sum(1 for result in results if result is not None) == 1
    assert GodotEntryTicket.objects.get(pk=ticket.pk).redeemed_at is not None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py -v`
Expected: FAIL，收集階段就錯 `ModuleNotFoundError: No module named 'api.godot_tickets'`

- [ ] **Step 3: 寫實作**

建立 `backend/api/godot_tickets.py`：

```python
"""Godot 大廳一次性入場券的發放與兌換。

為什麼不是直接把主功能的 access token 交給 Godot：見
docs/superpowers/specs/2026-07-28-godot-identity-and-match-binding-design.md §D1。
"""
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from api.models import GodotEntryTicket

TICKET_TTL_SECONDS = 60


def issue_ticket(*, user, now=None) -> GodotEntryTicket:
    """發一張新券給 user。券是短效的，過期就重發，不做回收。"""
    current_time = now or timezone.now()
    return GodotEntryTicket.objects.create(
        token=secrets.token_urlsafe(32),
        user=user,
        expires_at=current_time + timedelta(seconds=TICKET_TTL_SECONDS),
    )


def redeem_ticket(*, token: str, now=None):
    """兌換一張券，回傳對應的 User；無效（不存在／已用過／逾期）一律回 None。

    不區分無效的原因——呼叫端是 Godot server，知道細節沒有用處，而區分了就等於
    給探測者一個 oracle。

    一次性必須靠行鎖：兩個 peer 同時送同一張券時，沒有 select_for_update 的話
    兩邊都會讀到 redeemed_at is None 而同時成功。of=("self",) 只鎖 ticket 這一列，
    不去鎖 join 進來的 user 列（沒必要，也避免拖慢其他碰 user 的交易）。
    """
    if not token:
        return None

    current_time = now or timezone.now()
    with transaction.atomic():
        ticket = (
            GodotEntryTicket.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(
                token=token,
                redeemed_at__isnull=True,
                expires_at__gt=current_time,
            )
            .first()
        )
        if ticket is None:
            return None
        ticket.redeemed_at = current_time
        ticket.save(update_fields=["redeemed_at"])
        return ticket.user
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py -v`
Expected: PASS，8 passed

- [ ] **Step 5: 確認併發測試真的在驗行鎖（變異測試）**

暫時把 `select_for_update(of=("self",))` 改成 `all()`，重跑併發測試：

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py::test_concurrent_redeem_only_one_succeeds -v`
Expected: FAIL（兩個都成功）。確認會失敗後**把 `select_for_update(of=("self",))` 改回去**再重跑一次確認 PASS。

若拿掉鎖之後測試仍然 PASS，代表這個測試沒有驗到東西，要修測試（例如檢查 barrier 有沒有真的讓兩條執行緒同時進入）。

- [ ] **Step 6: Commit**

```bash
git add backend/api/godot_tickets.py backend/api/tests_godot_tickets.py
git commit -m "feat(m1): add godot entry ticket issue/redeem service with single-use lock"
```

---

## Task 3: 兩支端點

**Files:**
- Modify: `backend/api/views.py`（新增兩個 view，放在 `GodotMatchRoomView` 之前）
- Modify: `backend/api/urls.py`（在 `godot/match-rooms/` 那條附近加兩條）
- Test: `backend/api/tests_godot_tickets.py`（追加）

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_godot_tickets.py` 檔案**最後**追加（檔頭的 import 也要補上
`from django.test import override_settings`、`from rest_framework.test import APIClient`、
`from rest_framework_simplejwt.tokens import AccessToken`）：

```python
def _service_client(token="svc-token"):
    client = APIClient()
    client.credentials(HTTP_X_GODOT_SERVICE_TOKEN=token)
    return client


@pytest.mark.django_db
def test_issue_endpoint_requires_authentication():
    response = APIClient().post("/api/godot/tickets/", {}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_issue_endpoint_returns_ticket_with_real_jwt():
    """用真 JWT 而非 force_authenticate——後者不走 authentication 流程，
    驗證不到這個 view 實際上用哪個 authentication class。"""
    user = User.objects.create_user(username="u1", password="pw")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")

    response = client.post("/api/godot/tickets/", {}, format="json")

    assert response.status_code == 201
    assert response.data["expires_in"] == TICKET_TTL_SECONDS
    assert GodotEntryTicket.objects.get(token=response.data["ticket"]).user_id == user.id


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeem_endpoint_returns_user_id():
    user = User.objects.create_user(username="u1", password="pw")
    user.first_name = "彩希"
    user.save(update_fields=["first_name"])
    ticket = issue_ticket(user=user)

    response = _service_client().post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 200
    assert response.data == {"user_id": user.id, "nickname": "彩希"}


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeem_endpoint_falls_back_to_username_when_no_first_name():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    response = _service_client().post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.data["nickname"] == "u1"


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeem_endpoint_rejects_wrong_service_token():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)

    response = _service_client("wrong").post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 403
    assert GodotEntryTicket.objects.get(pk=ticket.pk).redeemed_at is None


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeem_endpoint_ignores_stale_authorization_header():
    """authentication_classes 必須清空：預設的 JWTAuthentication 遇到壞掉的
    Authorization header 會先丟 401，根本輪不到服務金鑰驗證。"""
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    client = APIClient()
    client.credentials(
        HTTP_X_GODOT_SERVICE_TOKEN="svc-token",
        HTTP_AUTHORIZATION="Bearer garbage.token.value",
    )

    response = client.post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 200
    assert response.data["user_id"] == user.id


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeem_endpoint_rejects_used_ticket():
    user = User.objects.create_user(username="u1", password="pw")
    ticket = issue_ticket(user=user)
    redeem_ticket(token=ticket.token)

    response = _service_client().post(
        "/api/godot/tickets/redeem/", {"ticket": ticket.token}, format="json"
    )

    assert response.status_code == 400
    assert response.data == {"detail": "入場券無效。"}


@override_settings(GODOT_SERVICE_TOKEN="svc-token")
@pytest.mark.django_db
def test_redeem_endpoint_rejects_missing_and_non_string_ticket():
    for payload in ({}, {"ticket": ""}, {"ticket": 123}, {"ticket": None}):
        response = _service_client().post(
            "/api/godot/tickets/redeem/", payload, format="json"
        )
        assert response.status_code == 400, payload
        assert response.data == {"detail": "入場券無效。"}, payload
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py -v -k endpoint`
Expected: FAIL，路由不存在（404，斷言 201/200 失敗）

- [ ] **Step 3: 寫實作**

在 `backend/api/views.py` 的 `class GodotMatchRoomView(APIView):` **正上方**插入：

```python
class GodotTicketIssueView(APIView):
    """POST /api/godot/tickets/ — 主功能頁面替目前登入者換一張 Godot 大廳入場券。

    回傳的 ticket 由 GodotLobby.jsx 塞進 iframe 的 window.bridgeus_ticket，
    Godot client 再交給 headless server 兌換（見 integration spec §5）。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ticket = issue_ticket(user=request.user)
        return Response(
            {"ticket": ticket.token, "expires_in": TICKET_TTL_SECONDS},
            status=status.HTTP_201_CREATED,
        )


class GodotTicketRedeemView(APIView):
    """POST /api/godot/tickets/redeem/ — 常駐 headless Godot server 用服務金鑰
    把入場券換成 user_id，藉此確認「這個 peer 是哪個使用者」。

    呼叫者是 Godot server、不是使用者，沒有也不該有 JWT。清空 authentication_classes
    是必要的：預設的 JWTAuthentication 遇到過期/損壞的 Authorization header 會
    直接丟 401，根本輪不到底下的服務金鑰驗證跑（同 GodotMatchRoomView）。
    """

    authentication_classes = []
    permission_classes = [IsGodotServiceToken]

    def post(self, request):
        token = request.data.get("ticket")
        user = redeem_ticket(token=token) if isinstance(token, str) else None
        if user is None:
            # 不區分「不存在／已用過／逾期」——呼叫端用不到，區分了等於給探測者 oracle。
            return Response(
                {"detail": "入場券無效。"}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            {"user_id": user.id, "nickname": user.first_name or user.username}
        )
```

在 `backend/api/views.py` 的 import 區加上：

```python
from api.godot_tickets import TICKET_TTL_SECONDS, issue_ticket, redeem_ticket
```

在 `backend/api/urls.py` 的 `path('godot/match-rooms/', ...)` **上方**加兩條：

```python
    path('godot/tickets/', views.GodotTicketIssueView.as_view()),
    path('godot/tickets/redeem/', views.GodotTicketRedeemView.as_view()),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py -v`
Expected: PASS，16 passed

- [ ] **Step 5: Commit**

```bash
git add backend/api/views.py backend/api/urls.py backend/api/tests_godot_tickets.py
git commit -m "feat(m1): add godot ticket issue and redeem endpoints"
```

---

## Task 4: 移除訪客登入

**Files:**
- Modify: `backend/api/views.py`（刪除 `GuestLoginView`）
- Modify: `backend/api/urls.py`（刪除 `guest/` 路由）
- Test: `backend/api/tests_godot_tickets.py`（追加）

- [ ] **Step 1: 寫失敗的測試**

在 `backend/api/tests_godot_tickets.py` 最後追加：

```python
@pytest.mark.django_db
def test_guest_login_endpoint_is_gone():
    """訪客登入會產生無主帳號（user_id 對不到真實受試者），已移除。
    見 integration spec §5.4。"""
    response = APIClient().post("/api/guest/", {"nickname": "訪客"}, format="json")

    assert response.status_code == 404
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py::test_guest_login_endpoint_is_gone -v`
Expected: FAIL，`assert 201 == 404`

- [ ] **Step 3: 刪除實作**

- 從 `backend/api/views.py` 刪掉整個 `class GuestLoginView(APIView):` 區塊（含 `permission_classes`、`post`）。
- 從 `backend/api/urls.py` 刪掉 `path('guest/', views.GuestLoginView.as_view()),` 這一行。
- 檢查 `RefreshToken` 與 `_uuid_mod` 這兩個 import 在 `views.py` 是否還有其他使用者；沒有的話一併刪掉 import。用 `grep -n "RefreshToken\|_uuid_mod" backend/api/views.py` 確認。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd /Users/light/code/backend && uv run pytest api/tests_godot_tickets.py -v`
Expected: PASS，17 passed

- [ ] **Step 5: 跑全套迴歸**

Run: `cd /Users/light/code/backend && uv run pytest api/tests*.py -q`

⚠️ **不要用 `pytest api`**——它會收集到 0 個測試然後以 exit code 5 結束，看起來像「跑完沒事」。
專案的測試檔命名是 `tests_*.py`，而 pytest 預設只收 `test_*.py` / `*_test.py`，
`pyproject.toml` 也沒有設 `python_files`。要嘛用上面的 glob，要嘛指定個別檔案。

Expected: 全部 PASS，而且測試數是三位數（若看到「no tests ran」就是收集條件寫錯了，不是真的沒事）。
若有測試因為 `/api/guest/` 消失而失敗，那個測試也要一併移除（它在驗證一個已經被刻意刪掉的功能）。
**不要**為了讓它過而把 `GuestLoginView` 加回來。

- [ ] **Step 6: Commit**

```bash
git add backend/api/views.py backend/api/urls.py backend/api/tests_godot_tickets.py
git commit -m "feat(m1): remove guest login endpoint"
```

---

## 完成後

階段一結束時的已知中間狀態（不是 bug，階段二會接上）：

- `godot/Globals/Backend.gd` 仍在呼叫已移除的 `/api/guest/`，桌面開發模式下會拿到 404。
- `frontend/src/pages/GodotLobby.jsx` 仍在塞 `bridgeus_token`，還沒改用 ticket。

下一步：依 spec §14 撰寫階段二計畫（Godot 身份表 + spawn 授權 + 移除 `backend_user_id` 同步）。
