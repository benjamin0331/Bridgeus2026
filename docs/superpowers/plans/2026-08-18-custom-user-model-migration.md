# Custom User Model 遷移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Django 內建的 `auth.User` 換成專案自有的 `accounts.User`，接管既有 `auth_user` 資料表、不遺失任何資料，並加入自助註冊所需的四個欄位。

**Architecture:** 新增 `accounts` app，其 `User(AbstractUser)` 以 `db_table = "auth_user"` 指回既有資料表，因此 20 個既有外鍵在 DB 層完全不動。遷移分三支 migration：`0001` 是 `auth.User` 的精確複製品（prod 上以手動寫入 `django_migrations` 的方式視為已套用）、`0002` 把 `django_content_type` 的 `app_label` 由 `auth` 原地改為 `accounts`（保住研究者的 admin 權限）、`0003` 才真正加欄位。

**Tech Stack:** Django 6.0.4、djangorestframework-simplejwt 5.5.1、uv（Python 3.13）、SQLite（dev/CI）＋ PostgreSQL（prod）

**Spec:** [../specs/2026-08-18-custom-user-model-design.md](../specs/2026-08-18-custom-user-model-design.md)

---

## 執行方式（subagent 必讀）

**不要執行任何 `git commit`、`git add`、`git push`。** 本計畫刻意不逐 task 提交，因為 `feat/Light` 已領先 `dev` 約 173 個 commit，再加 11 個只會讓後續合併更難。提交由 orchestrator 在三個檢查點統一處理。

**你（subagent）的交接責任**：完成分配到的 task 之後，把改動留在工作區，並在回報中明確列出

1. 你建立或修改的每一個檔案路徑
2. 你實際執行過的指令與其真實輸出（不要複述計畫裡的「預期」當成結果）
3. 任何與計畫預期不符之處 —— 這比「全部順利」有價值得多

所有 subagent 共用同一個工作區，因此上一個 task 未提交的改動會直接被你看到，不需要額外交接動作。

### 三個檢查點（由 orchestrator 執行）

| 檢查點 | 位置 | 為什麼在這裡 |
|---|---|---|
| 1 | Task 2 **與 Task 7** 完成後 | Task 1／1b 結束時 repo 都是壞的。Task 2 讓 `migrate` 通過，但 `api/views.py` 與 `api/serializers.py` 仍綁著 swapped-out 的 `auth.User`，`api.tests_account_management` 的 23 個測試會全數錯誤。**必須連 Task 7 一起做完，這個檢查點才是測試全綠的狀態。** 因此執行順序是 1 → 1b → 2 → 7 → 檢查點 1 → 3 → 4 → 5 → 6 → 8 → 9 → 10。 |
| 2 | Task 10 完成後 | 功能完整且全量回歸通過。 |
| 3 | Task 11 完成後 | prod runbook 產出。 |

**Task 1 → 1b → 2 → 7 之間不可提交** —— 那三個 task 中間的每一個狀態都是壞的（Task 1 後 `django.setup()` 失敗，Task 1b 後 `migrate` 失敗），提交只會在分支上留下跑不起來的樹。

---

## 前置知識（實作前必讀）

執行本計畫的人可能不熟本專案，以下五點是這次遷移的全部難點所在：

1. **不是新資料庫、不是新資料表。** 既有 Postgres 與 `auth_user` 表原地保留，只是 model 定義改由本 repo 提供。`db_table = "auth_user"` 是整個方案的核心。

2. **`accounts.0001_initial` 必須與 `auth.User` schema 完全相同，不可包含新欄位。** 因為 prod 要把它當成「已套用」跳過。新欄位一律留到 `0003`。這是最容易做錯的一步。

3. **`default_auto_field` 必須明確設成 `AutoField`。** Django 6 全域預設是 `BigAutoField`（`django/conf/global_settings.py:440`），而 `django.contrib.auth` 自己設了 `AutoField`（`django/contrib/auth/apps.py:14`）。本專案未覆寫 `DEFAULT_AUTO_FIELD`，若不明確指定，`auth_user.id` 會從 `integer` 變成 `bigint`。

4. **有兩支既有 migration 會在全新 DB 上炸掉，都必須改成 swappable-aware**，否則每一次跑測試都會失敗：

   - **`api/0013_create_researcher_group.py:19`** —— 它做 `apps.get_model("auth", "User").objects.filter(is_staff=True)`。swapped-out 的歷史 model **沒有 manager**，實測錯誤：`AttributeError: Manager isn't available; 'auth.User' has been swapped for 'accounts.User'`。這支比 0017 更早執行，是第一個撞到的。
   - **`api/0017_grant_researcher_admin_permissions.py`** —— 它做 `ContentType.objects.get(app_label="auth", model="user")`，但 `AppConfig.get_models()` 預設排除 swapped model（`django/apps/config.py:258`），換掉之後 `auth/user` 這個 ContentType 根本不會被建立。

5. **`admin.site.unregister(User)` 必須刪除。** `admin.site.register` 對 swapped model 會靜默忽略（`django/contrib/admin/sites.py:135`），所以換掉之後 `django.contrib.auth.admin` 不會註冊任何 User，unregister 會拋 `NotRegistered`。

### 測試指令

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts --noinput
```

全量回歸（碰 `VectorField` 的測試需要 Postgres）：

```bash
uv run python manage.py test --noinput
```

**重要：後端測試共用同一個 Postgres test DB，絕對不可同時執行兩個 `manage.py test`。** 被中斷的測試會留下鎖，造成不相干的假失敗。

所有指令都在 `backend/` 目錄下執行。

### 測試資料量原則

新增測試的 fixture 一律維持最小規模（每個案例 1–3 個 User）。這些 migration 的行為與資料筆數無關，灌大量測資只會拖慢 iteration。

---

## File Structure

**新建**

| 檔案 | 責任 |
|---|---|
| `backend/accounts/__init__.py` | app 套件 |
| `backend/accounts/apps.py` | AppConfig，釘住 `default_auto_field = AutoField` |
| `backend/accounts/models.py` | `User(AbstractUser)`，`db_table = "auth_user"` |
| `backend/accounts/admin.py` | `BridgeUsUserAdmin`（自 `api/admin.py` 搬來） |
| `backend/accounts/migrations/0001_initial.py` | auth.User 的精確複製品（makemigrations 產生） |
| `backend/accounts/migrations/0002_repoint_user_content_type.py` | ContentType `auth` → `accounts` |
| `backend/accounts/migrations/0003_user_registration_fields.py` | email 回填＋唯一約束＋3 個新欄位 |
| `backend/accounts/management/commands/bootstrap_user_swap.py` | prod 用：寫入 `django_migrations` 紀錄列 |
| `backend/accounts/tests_user_model.py` | model 層測試 |
| `backend/accounts/tests_migration_guards.py` | migration 與權限轉移測試 |
| `backend/accounts/tests_display_name_boundary.py` | `display_name` 不得外洩的邊界測試 |

**修改**

| 檔案 | 改動 |
|---|---|
| `backend/BridgeUs_Django/settings.py` | 加 `accounts` 到 INSTALLED_APPS、加 `AUTH_USER_MODEL` |
| `backend/api/models.py:2, 754, 786, 811` | 移除 `User` import，三處 FK 改 `settings.AUTH_USER_MODEL` |
| `backend/api/views.py:10` | `User` 改 `get_user_model()` |
| `backend/api/serializers.py:4` | 同上，`AccountListSerializer.Meta.model` 一併換 |
| `backend/api/admin.py:24-36` | 刪除 User 相關區塊（搬到 `accounts/admin.py`） |
| `backend/api/migrations/0017_grant_researcher_admin_permissions.py` | 改為 swappable-aware |
| 4 個測試檔 | `from django.contrib.auth.models import User` → `get_user_model()` |

---

### Task 1: 建立 accounts app 與 User model，切換 AUTH_USER_MODEL

**Files:**
- Create: `backend/accounts/__init__.py`
- Create: `backend/accounts/apps.py`
- Create: `backend/accounts/models.py`
- Create: `backend/accounts/migrations/__init__.py`
- Modify: `backend/BridgeUs_Django/settings.py:84-99`（INSTALLED_APPS）

本 task 刻意**不加任何新欄位** —— `0001_initial` 必須是 `auth.User` 的精確複製品。

**注意**：本 task 結束時 repo 仍是壞的（`migrate` 會失敗），這是預期的。Task 1b 會讓它重新可以載入，Task 2 才讓 `migrate` 通過。

**已知**：Step 5–7 的指令在本 task 單獨執行時會被兩件事擋住（`api/admin.py` 的 `NotRegistered`、`api/models.py` 三個 FK 的 `fields.E301`），這是計畫的順序問題，由 Task 1b 解決。Task 1 只要產出 `0001_initial.py` 即可，Step 6、7 的最終驗證移到 Task 1b。

- [ ] **Step 1: 建立 app 目錄與空檔案**

```bash
cd backend
mkdir -p accounts/migrations
touch accounts/__init__.py accounts/migrations/__init__.py
```

- [ ] **Step 2: 寫 AppConfig**

建立 `backend/accounts/apps.py`：

```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    # 必須明確指定 AutoField。Django 6 全域預設是 BigAutoField，而既有
    # auth_user.id 是 django.contrib.auth 用 AutoField 建的 integer 欄位。
    # 不指定會讓 model 狀態變成 bigint、與實際資料表不一致。
    default_auto_field = "django.db.models.AutoField"
    name = "accounts"
```

- [ ] **Step 3: 寫 User model（尚無新欄位）**

建立 `backend/accounts/models.py`：

```python
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """接管既有 auth_user 資料表的專案自有 User。

    db_table 指回 "auth_user" 是整個遷移方案的核心：資料表名稱與既有表相同，
    因此 20 個既有外鍵在 DB 層一個位元組都沒變，M2M 中介表也自動沿用
    auth_user_groups / auth_user_user_permissions 這兩個既有名稱。

    註冊用的新欄位刻意不放在這裡——0001_initial 必須是 auth.User 的精確
    複製品才能在 prod 上被安全地視為已套用。新欄位見 migration 0003。
    """

    class Meta(AbstractUser.Meta):
        db_table = "auth_user"
```

- [ ] **Step 4: 註冊 app 並切換 AUTH_USER_MODEL**

修改 `backend/BridgeUs_Django/settings.py`，在 INSTALLED_APPS 中 `'api',` 之前插入 `'accounts',`：

```python
    'rest_framework',
    'rest_framework_simplejwt',
    'accounts',
    'api',
    'chat',
    'apps.summary',
]

# User model 由 accounts app 提供，接管既有 auth_user 資料表。
# 見 docs/superpowers/specs/2026-08-18-custom-user-model-design.md
AUTH_USER_MODEL = "accounts.User"
```

- [ ] **Step 5: 產生 0001_initial**

```bash
DB_ENGINE=sqlite uv run python manage.py makemigrations accounts
```

預期輸出包含 `accounts/migrations/0001_initial.py` 與 `+ Create model User`。

- [ ] **Step 6: 驗證 0001 是精確複製品且 id 為 AutoField**

```bash
grep -n "AutoField\|BigAutoField\|db_table\|email_verified_at" accounts/migrations/0001_initial.py
```

預期：出現 `AutoField`、`'db_table': 'auth_user'`；**不得**出現 `BigAutoField` 或 `email_verified_at`。
若出現 `BigAutoField`，表示 Step 2 的 `default_auto_field` 沒生效 —— 刪掉 migration 修好再重跑。

- [ ] **Step 7: 驗證三個既有 app 不需要任何 migration**

```bash
DB_ENGINE=sqlite uv run python manage.py makemigrations --check --dry-run
```

預期：`No changes detected`。
若偵測到 `api`／`chat`／`apps.summary` 的變更，**停下來**——這表示 spec 第 3 節的前提不成立，需重新評估。

- [ ] **Step 8: 回報，不要 commit**

把改動留在工作區。回報你建立／修改的檔案清單，以及 Step 5–7 三個指令的**實際輸出**。

---

### Task 1b: 解除模組載入阻礙（admin 註冊 + 三個 FK）

**Files:**
- Create: `backend/accounts/admin.py`
- Modify: `backend/api/admin.py`（刪除 User 區塊）
- Modify: `backend/api/models.py`（第 2 行 import、三處 FK）

**為什麼有這個 task**：切換 `AUTH_USER_MODEL` 之後，有兩件事讓 Django 連
`django.setup()` 都跑不完，任何指令（含 `makemigrations`）都會失敗。原計畫把
它們排在 Task 2 與 Task 7，順序錯了 —— 這裡把最小解除集合提前。

實測到的兩個阻礙：

```
django.contrib.admin.exceptions.NotRegistered: The model User is not registered
  ← api/admin.py:31 的 admin.site.unregister(User)

api.Issue.author: (fields.E301) Field defines a relation with the model
  'auth.User', which has been swapped out.
  ← api/models.py 三處寫成 ForeignKey(User) 的外鍵
```

第二點也修正了 spec 第 3 節的說法：**已經寫成 `settings.AUTH_USER_MODEL` 的
20 個外鍵確實不需要任何 migration，但 `api/models.py` 裡 3 個寫成
`ForeignKey(User)` 的會產生 `AlterField`** —— 因為切換後 `auth.User` 成了
swapped-out model，deconstruct 不再輸出設定參照。改成 `settings.AUTH_USER_MODEL`
之後 `AlterField` 就會消失（既有 migration 記錄的正是這個形式，見
`api/migrations/0009_issue.py:25`）。

- [ ] **Step 1: 把 User admin 搬到 accounts app**

建立 `backend/accounts/admin.py`：

```python
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

User = get_user_model()


# 不需要（也不可以）先 unregister：django.contrib.auth.admin 對 swapped model
# 的註冊會被 admin.site.register 靜默忽略，因此這裡是 User 的唯一註冊點。
@admin.register(User)
class BridgeUsUserAdmin(UserAdmin):
    """加了 is_active／last_login 到列表，方便 Supervisor 一眼看帳號狀態。"""

    list_display = UserAdmin.list_display + ("is_active", "last_login")
```

- [ ] **Step 2: 從 api/admin.py 移除 User 區塊**

修改 `backend/api/admin.py`，刪除以下整段（約在第 24-36 行）：

```python
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

User = get_user_model()

# auth 在 INSTALLED_APPS 早於 api，預設 UserAdmin 先註冊了 User，這裡換成
# 加了 last_login／is_active 到列表的版本，方便 Supervisor 一眼看帳號狀態。
admin.site.unregister(User)


@admin.register(User)
class BridgeUsUserAdmin(UserAdmin):
    list_display = UserAdmin.list_display + ("is_active", "last_login")
```

改為只留一行註解指路：

```python
# User 的 admin 註冊已搬到 accounts/admin.py，與 model 定義放在一起。
```

- [ ] **Step 3: 修 api/models.py 的三處 FK**

刪除第 2 行 `from django.contrib.auth.models import User`，並把三處 FK 改成設定參照：

```python
# 原第 754 行
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="issues"
    )

# 原第 786 行
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_titles",
    )

# 原第 811 行
    reactor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_reactions",
    )
```

`from django.conf import settings` 已存在於第 1 行，不需新增。

- [ ] **Step 4: 確認 system check 通過**

```bash
DB_ENGINE=sqlite uv run python manage.py check
```

預期：`System check identified no issues`。不應再出現 `NotRegistered` 或 `fields.E301`。

- [ ] **Step 5: 確認沒有待產生的 migration**

`makemigrations --check` 會連資料庫，而既有的 `backend/db.sqlite3` 會因為
`accounts.0001_initial` 未套用而拋 `InconsistentMigrationHistory`（那是 Task 6 的
`bootstrap_user_swap` 要解的問題）。這裡對一個全新的空資料庫檔跑，只比對 model 狀態：

```bash
rm -f /tmp/bridgeus_task1b.sqlite3
DB_ENGINE=sqlite SQLITE_PATH=/tmp/bridgeus_task1b.sqlite3 \
  uv run python manage.py makemigrations --check --dry-run
rm -f /tmp/bridgeus_task1b.sqlite3
```

預期：`No changes detected`。

若仍偵測到 `api` 的 `Alter field`，表示 Step 3 沒改乾淨。
若偵測到 `chat` 或 `apps.summary` 的變更，**停下來回報** —— 那才是 spec 第 3 節的前提不成立。

- [ ] **Step 6: 回報，不要 commit**

附上 Step 4、5 的實際輸出。

---

### Task 2: 修復 `api/0013` 與 `api/0017`，讓全新 migrate 能跑通

**Files:**
- Modify: `backend/api/migrations/0013_create_researcher_group.py`
- Modify: `backend/api/migrations/0017_grant_researcher_admin_permissions.py`
- Test: `backend/accounts/tests_migration_guards.py`

Task 1b 之後 `manage.py check` 已通過，但 `migrate` 在全新 DB 上仍是壞的。本 task 修好它。

**要修兩支 migration，順序上 0013 先撞到。** 詳見前置知識第 4 點。

- [ ] **Step 1: 寫失敗測試 —— 全新 DB 能 migrate 且研究者權限仍在**

建立 `backend/accounts/tests_migration_guards.py`：

```python
"""遷移護欄：確認換掉 AUTH_USER_MODEL 之後，全新 DB 仍能 migrate，
且「研究者」Group 的 Django Admin 帳號管理權限沒有在換 app 的過程中掉光。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase

from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


class UserContentTypeTests(TestCase):
    def test_user_content_type_lives_in_accounts_app(self):
        """django_content_type 的 user 那一列已改由 accounts 持有。"""
        self.assertTrue(
            ContentType.objects.filter(app_label="accounts", model="user").exists()
        )
        self.assertFalse(
            ContentType.objects.filter(app_label="auth", model="user").exists()
        )

    def test_researcher_group_keeps_account_management_permissions(self):
        """換 app 之後研究者仍能在 Django Admin 管理帳號。

        這三個權限由 api/migrations/0017 指派。它們綁在 user 的 ContentType 上，
        若 ContentType 沒有原地轉移而是任其新建，Group 手上這組就會變成孤兒，
        研究者的帳號管理面板會整個失效。
        """
        group = Group.objects.get(name=RESEARCHER_GROUP_NAME)
        codenames = set(
            group.permissions.filter(
                content_type__app_label="accounts", content_type__model="user"
            ).values_list("codename", flat=True)
        )
        self.assertEqual(codenames, {"add_user", "change_user", "view_user"})

    def test_delete_user_permission_still_not_granted(self):
        """刻意不開放刪除帳號的既有決定沒有被這次遷移推翻。"""
        group = Group.objects.get(name=RESEARCHER_GROUP_NAME)
        self.assertFalse(group.permissions.filter(codename="delete_user").exists())

    def test_researcher_user_can_use_admin_user_change_permission(self):
        """從使用者端驗證權限字串是 accounts.change_user 而非 auth.change_user。"""
        group = Group.objects.get(name=RESEARCHER_GROUP_NAME)
        user = User.objects.create_user(username="r1", password="Xk9$mVpq2Lz")
        user.groups.add(group)
        user = User.objects.get(pk=user.pk)  # 清掉權限快取
        self.assertTrue(user.has_perm("accounts.change_user"))


class AdminRegistrationTests(TestCase):
    def test_user_model_registered_exactly_once_in_admin(self):
        """swapped model 的 admin 註冊會被靜默忽略，因此 auth 那邊不會註冊，
        只有 accounts/admin.py 這一份。重複 unregister 會拋 NotRegistered。
        """
        from django.contrib import admin

        self.assertIn(User, admin.site._registry)
        self.assertEqual(
            admin.site._registry[User].__class__.__name__, "BridgeUsUserAdmin"
        )
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards --noinput
```

預期：整個測試 **在建立測試資料庫階段就失敗**，錯誤訊息為

```
AttributeError: Manager isn't available; 'auth.User' has been swapped for 'accounts.User'
  File "backend/api/migrations/0013_create_researcher_group.py", line 19, in
  create_researcher_group_and_migrate_staff
```

修好 0013（Step 2b）之後再跑一次，會換成第二個錯誤：
`ContentType.DoesNotExist`，來自 `api/migrations/0017_grant_researcher_admin_permissions.py`。
兩個都看到才代表你確實驗證過這兩個 blocker 存在。

- [ ] **Step 2b: 把 0013 改成 swappable-aware**

修改 `backend/api/migrations/0013_create_researcher_group.py`。
在檔案頂端加入 `from django.conf import settings`，並新增一個小 helper：

```python
def _user_model(apps):
    """從 AUTH_USER_MODEL 取歷史 model。

    寫死 apps.get_model("auth", "User") 在切換之後會拿到 swapped-out 的
    歷史 model，它沒有 manager：
        AttributeError: Manager isn't available; 'auth.User' has been
        swapped for 'accounts.User'
    """
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    return apps.get_model(app_label, model_name)
```

把 `create_researcher_group_and_migrate_staff` 裡的

```python
    User = apps.get_model("auth", "User")
```

改成

```python
    User = _user_model(apps)
```

並在 `Migration.dependencies` 加入 swappable 依賴：

```python
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
```

`Group` 維持 `apps.get_model("auth", "Group")` 不變 —— Group 不是 swappable。

再跑一次 Step 2 的指令，確認錯誤已經換成 `ContentType.DoesNotExist`（來自 0017）。

- [ ] **Step 3: 把 0017 改成 swappable-aware**

修改 `backend/api/migrations/0017_grant_researcher_admin_permissions.py`。
把 `_ensure_auth_permissions` 整個函式替換為：

```python
def _user_content_type_key():
    """從 AUTH_USER_MODEL 推出 user 的 (app_label, model)。

    寫死 "auth"/"user" 在 AUTH_USER_MODEL 換成 accounts.User 之後會失效：
    AppConfig.get_models() 預設排除 swapped model，因此 auth/user 這個
    ContentType 在全新資料庫上根本不會被建立。
    """
    from django.conf import settings

    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    return app_label.lower(), model_name.lower()


def _ensure_permissions(apps, using):
    # 全新的 migrate：預設權限由 post_migrate 建立，而 post_migrate 要等所有
    # migration 跑完才觸發——此刻權限可能還不存在。手動先補建，確保下面
    # filter 抓得到。既有 DB 已有這些權限時，create_permissions 是 no-op。
    #
    # 除了 auth（Group 的權限來源）之外，還要補 user model 所在的 app，
    # 因為 User 已經不屬於 auth 了。
    from django.apps import apps as global_apps

    user_app_label, _ = _user_content_type_key()
    for label in dict.fromkeys(["auth", user_app_label]):
        create_permissions(
            global_apps.get_app_config(label), apps=apps, using=using, verbosity=0
        )
```

接著把 `grant_permissions` 整個函式替換為：

```python
def grant_permissions(apps, schema_editor):
    _ensure_permissions(apps, schema_editor.connection.alias)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user_app_label, user_model_name = _user_content_type_key()
    user_ct = ContentType.objects.get(app_label=user_app_label, model=user_model_name)
    group_ct = ContentType.objects.get(app_label="auth", model="group")

    perms = list(
        Permission.objects.filter(content_type=user_ct, codename__in=USER_CODENAMES)
    ) + list(
        Permission.objects.filter(content_type=group_ct, codename__in=GROUP_CODENAMES)
    )
    group.permissions.add(*perms)
```

在 `revoke_permissions` 中，把取得 `user_ct` 的那一行：

```python
    user_ct = ContentType.objects.filter(app_label="auth", model="user").first()
```

替換為：

```python
    user_app_label, user_model_name = _user_content_type_key()
    user_ct = ContentType.objects.filter(
        app_label=user_app_label, model=user_model_name
    ).first()
```

最後在檔案頂端補 `from django.conf import settings`，並在 `Migration.dependencies` 加入 swappable 依賴，確保 user model 的 app 先就位：

```python
class Migration(migrations.Migration):

    dependencies = [
        ("api", "0016_issuereaction"),
        ("auth", "__first__"),
        ("contenttypes", "__first__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
```

（原 Step 4、5 的 admin 搬移已移至 Task 1b，本 task 不再重複。）

- [ ] **Step 6: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards --noinput
```

預期：`Ran 5 tests` 全部 OK。

說明：全新測試資料庫上，`accounts/user` 這個 ContentType 是由 Step 3 改好的 `api/0017`（透過 `create_permissions`）建立的，`auth/user` 則從未存在，因此 `test_user_content_type_lives_in_accounts_app` 此時就會通過。Task 4 的 repoint migration 是給 prod 那種「已有 auth/user 列」的資料庫用的。

- [ ] **Step 7: 確認既有測試沒被打壞**

```bash
DB_ENGINE=sqlite uv run python manage.py test api.tests_supervisor_admin --noinput
```

預期：全部 OK。

- [ ] **Step 8: 回報，不要 commit**

回報改動檔案清單與 Step 2、6、7 的實際輸出。特別註明 Step 2 是否真的看到預期的 `ContentType.DoesNotExist`——那是驗證「這個 blocker 真實存在」的唯一機會。

> **接下來直接做 Task 7**（不是 Task 3）。Task 2 之後 `migrate` 雖然通過，但 `api.tests_account_management` 仍會全數錯誤於
> `AttributeError: Manager isn't available; 'auth.User' has been swapped`（`api/views.py:2811,2820`、`api/serializers.py:700`）。
> 檢查點 1 在 Task 7 之後。

---

### Task 3: model 層測試

**Files:**
- Create: `backend/accounts/tests_user_model.py`

- [ ] **Step 1: 寫測試**

建立 `backend/accounts/tests_user_model.py`：

```python
"""accounts.User 的結構性測試。

重點不在「欄位能不能存」，而在「有沒有真的接管既有的 auth_user 資料表」——
db_table 或 M2M 中介表名稱一旦跑掉，20 個既有外鍵就會指向錯的地方。
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class UserModelIdentityTests(TestCase):
    def test_auth_user_model_setting(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_user_model_is_from_accounts_app(self):
        self.assertEqual(User._meta.app_label, "accounts")

    def test_takes_over_existing_auth_user_table(self):
        """db_table 是整個遷移方案的核心，改掉就全盤皆錯。"""
        self.assertEqual(User._meta.db_table, "auth_user")

    def test_m2m_tables_reuse_existing_names(self):
        """M2M 中介表由 Django 以 <db_table>_<field_name> 推導。
        db_table 是 auth_user，所以自動沿用 Django 內建的既有表名。
        """
        self.assertEqual(User.groups.field.m2m_db_table(), "auth_user_groups")
        self.assertEqual(
            User.user_permissions.field.m2m_db_table(),
            "auth_user_user_permissions",
        )

    def test_primary_key_stays_integer_not_bigint(self):
        """既有 auth_user.id 是 AutoField（integer）。Django 6 全域預設是
        BigAutoField，accounts/apps.py 必須明確指定 AutoField 壓過它。
        """
        self.assertEqual(User._meta.pk.get_internal_type(), "AutoField")


class ResearcherGroupSyncTests(TestCase):
    """api/signals.py 的 Group ↔ is_staff 同步在換 model 之後仍要正常。"""

    def test_joining_researcher_group_sets_is_staff(self):
        from django.contrib.auth.models import Group

        from api.permissions import RESEARCHER_GROUP_NAME

        user = User.objects.create_user(username="p1", password="Xk9$mVpq2Lz")
        self.assertFalse(user.is_staff)

        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        user.groups.add(group)

        user.refresh_from_db()
        self.assertTrue(user.is_staff)
```

- [ ] **Step 2: 執行測試**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_user_model --noinput
```

預期：`Ran 6 tests` 全部 OK。這些測試驗證的是 Task 1 已完成的設計，應直接通過；任何一項失敗都代表 Task 1 做錯了，回報時務必指出是哪一項。

- [ ] **Step 3: 回報，不要 commit**

---

### Task 4: ContentType 原地轉移 migration

**Files:**
- Create: `backend/accounts/migrations/0002_repoint_user_content_type.py`
- Modify: `backend/accounts/tests_migration_guards.py`

這支 migration 在**全新 DB 上是 no-op**，只有在 prod 那種「既有 auth/user 列」的 DB 上才有作用。因此 Task 2 的測試已經通過不代表這支不需要 —— 它是 prod 專用的。

- [ ] **Step 1: 寫 migration**

建立 `backend/accounts/migrations/0002_repoint_user_content_type.py`：

```python
"""把 django_content_type 裡 user 那一列的 app_label 由 auth 原地改成 accounts。

不這樣做的話，post_migrate 會另外建立一組 accounts/user 的權限，而
api/migrations/0017 指派給「研究者」Group 的那三個權限仍綁在舊的 auth/user
ContentType 上，隨即變成孤兒——研究者的 Django Admin 帳號管理會整個失效。

原地改 app_label 讓 Permission 列、Group 的權限指派、以及 django_admin_log
的既有紀錄全部跟著轉移，不需搬動任何資料。
"""

from django.db import migrations


def repoint_forward(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")

    # 冪等：全新資料庫上 accounts/user 會由 post_migrate 或 api/0017 建立，
    # 而 auth/user 從來不存在。(app_label, model) 有 unique 約束，兩列同時
    # 存在時 update 會撞約束，所以這個檢查是必要的而非防禦性冗餘。
    if ContentType.objects.filter(app_label="accounts", model="user").exists():
        return
    ContentType.objects.filter(app_label="auth", model="user").update(
        app_label="accounts"
    )


def repoint_backward(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    if ContentType.objects.filter(app_label="auth", model="user").exists():
        return
    ContentType.objects.filter(app_label="accounts", model="user").update(
        app_label="auth"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("contenttypes", "__first__"),
    ]

    operations = [
        migrations.RunPython(repoint_forward, repoint_backward),
    ]
```

- [ ] **Step 2: 加冪等性測試**

在 `backend/accounts/tests_migration_guards.py` 末尾追加。
注意 migration 模組名以數字開頭、不是合法識別字，必須用 `importlib` 載入，
不能寫 `from accounts.migrations.0002_... import ...`：

```python
class RepointIdempotencyTests(TestCase):
    """repoint 在「accounts/user 已存在」時必須是 no-op。

    全新資料庫上 migration 順序不保證 accounts.0002 早於 api.0017，兩種順序
    都必須安全：先跑 0002 時 auth/user 不存在（update 命中 0 列），先跑 0017
    時 accounts/user 已存在（提早 return）。
    """

    def test_repoint_forward_is_noop_when_accounts_ct_exists(self):
        import importlib

        from django.apps import apps as global_apps

        module = importlib.import_module(
            "accounts.migrations.0002_repoint_user_content_type"
        )

        before = ContentType.objects.get(app_label="accounts", model="user").pk
        module.repoint_forward(global_apps, None)
        after = ContentType.objects.get(app_label="accounts", model="user").pk

        self.assertEqual(before, after)
        self.assertFalse(
            ContentType.objects.filter(app_label="auth", model="user").exists()
        )
```

- [ ] **Step 3: 執行測試**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards --noinput
```

預期：`Ran 6 tests` 全部 OK。

- [ ] **Step 4: 回報，不要 commit**

---

### Task 5: 加入註冊所需的四個欄位

**Files:**
- Modify: `backend/accounts/models.py`
- Create: `backend/accounts/migrations/0003_user_registration_fields.py`
- Modify: `backend/accounts/tests_user_model.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/accounts/tests_user_model.py` 末尾追加，並在檔案頂端 import 區補
`from django.db import IntegrityError, transaction`：

```python
class RegistrationFieldTests(TestCase):
    def test_email_can_be_null_for_multiple_users(self):
        """既有帳號都是研究者代開、沒有 email。DB 層必須允許多筆 NULL，
        UNIQUE 才不會擋住它們。（Postgres 與 SQLite 的 UNIQUE 都不將多個
        NULL 視為相撞。）
        """
        User.objects.create_user(username="n1", password="Xk9$mVpq2Lz")
        User.objects.create_user(username="n2", password="Xk9$mVpq2Lz")
        self.assertEqual(User.objects.filter(email__isnull=True).count(), 2)

    def test_duplicate_non_null_email_is_rejected(self):
        User.objects.create_user(
            username="e1", password="Xk9$mVpq2Lz", email="a@example.com"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="e2", password="Xk9$mVpq2Lz", email="a@example.com"
                )

    def test_new_fields_have_expected_defaults(self):
        user = User.objects.create_user(username="d1", password="Xk9$mVpq2Lz")
        self.assertIsNone(user.email_verified_at)
        self.assertFalse(user.is_research_subject)
        self.assertEqual(user.display_name, "")
```

**注意**：`create_user` 未指定 email 時，Django 的 `UserManager.normalize_email` 會回傳 `""`。因此 model 需要一個把空字串正規化成 `None` 的 `save()`，否則第一個測試會失敗於「兩筆空字串相撞」。下一步會處理。

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_user_model.RegistrationFieldTests --noinput
```

預期：三個測試都失敗，錯誤為 `AttributeError: 'User' object has no attribute 'email_verified_at'`。

- [ ] **Step 3: 加欄位到 model**

把 `backend/accounts/models.py` 整個替換為：

```python
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """接管既有 auth_user 資料表的專案自有 User。

    db_table 指回 "auth_user" 是整個遷移方案的核心：資料表名稱與既有表相同，
    因此 20 個既有外鍵在 DB 層一個位元組都沒變，M2M 中介表也自動沿用
    auth_user_groups / auth_user_user_permissions 這兩個既有名稱。
    """

    # DB 層允許 NULL：既有帳號都是研究者代開、沒有 email，不能強制 NOT NULL。
    # 「註冊必填」只在註冊 serializer 強制（下一份 spec）。
    email = models.EmailField("email address", unique=True, null=True, blank=True)

    # 先永遠是 None。之後要做驗證信時不用再動 migration。
    email_verified_at = models.DateTimeField(null=True, blank=True)

    # 區分「正式受試者」與「公開註冊的一般使用者」，供研究分析篩選。
    is_research_subject = models.BooleanField(default=False)

    # 只在設定頁與知識庫署名可見，不進對話室、不進 Godot。
    # 見 spec 第 7 節與 tests_display_name_boundary.py。
    display_name = models.CharField(max_length=50, blank=True)

    class Meta(AbstractUser.Meta):
        db_table = "auth_user"

    def save(self, *args, **kwargs):
        # UserManager.create_user 在沒給 email 時會塞空字串，而空字串在
        # UNIQUE 之下會互相碰撞（NULL 才不會）。在這裡統一正規化成 None，
        # 讓「沒有 email」只有一種表示法。
        if not self.email:
            self.email = None
        super().save(*args, **kwargs)
```

- [ ] **Step 4: 產生 migration**

`makemigrations` 也會做 `check_consistent_history`，用既有的 `backend/db.sqlite3`
會炸在 `InconsistentMigrationHistory`（那是 Task 6 的 `bootstrap_user_swap` 要解的
問題）。跟 Step 7 一樣，對一個暫存的空資料庫跑：

```bash
DB_ENGINE=sqlite SQLITE_PATH=/tmp/task5_mm.sqlite3 \
  uv run python manage.py makemigrations accounts --name user_registration_fields
```

預期產生 `accounts/migrations/0003_user_registration_fields.py`，含 1 個 `AlterField`（email）與 3 個 `AddField`。

**注意 `makemigrations` 產生的順序是字母序**（`display_name` → `email_verified_at` → `is_research_subject` → `AlterField email`），與下一步要求的順序不同，Step 5 會整個重排。

- [ ] **Step 5: 在 migration 最前面插入 email 回填**

編輯 `backend/accounts/migrations/0003_user_registration_fields.py`，在檔案頂端
（`class Migration` 之前）加入兩個函式：

```python
def blank_email_to_null(apps, schema_editor):
    """既有帳號的 email 全是空字串（研究者代開時沒填）。
    必須先回填成 NULL，之後加 UNIQUE 才不會撞重複值。
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(email="").update(email=None)


def null_email_to_blank(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(email__isnull=True).update(email="")
```

並把 `operations` 改成以下順序。必須是三步，不能簡化成兩步：

1. `AlterField`：`email` 改為 `null=True, blank=True`（**此時還不能加 `unique`**）
2. `RunPython`：把既有 `email = ''` 改成 `NULL`（含反向：`NULL` → `''`）
3. `AlterField`：`email` 加上 `unique=True`
4. `AddField` × 3：`email_verified_at`、`is_research_subject`、`display_name`

兩個約束互相牽制：**回填要寫 NULL，欄位得先允許 NULL**；而 **UNIQUE 必須等回填完才能加**，否則既有帳號的空字串 email 會互相相撞。

⚠️ **這個順序錯了在任何測試資料庫上都測不出來** —— 全新資料庫零筆帳號，`filter(email="")` 匹配到零列、靜默成功。只有在既有資料庫上才會炸：實測對 `backend/db.sqlite3` 的副本（4 個帳號、email 全為空字串）執行錯誤順序，得到 `IntegrityError: NOT NULL constraint failed: auth_user.email`。順序不變式已由 `accounts/tests_migration_guards.py` 的 `EmailBackfillOperationOrderTests` 釘住。

完整的 `operations`：

```python
    operations = [
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(
                blank=True,
                max_length=254,
                null=True,
                verbose_name="email address",
            ),
        ),
        migrations.RunPython(blank_email_to_null, null_email_to_blank),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(
                blank=True,
                max_length=254,
                null=True,
                unique=True,
                verbose_name="email address",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="is_research_subject",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="display_name",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
```

省略第一個 `AlterField` 會失敗於 `NOT NULL constraint failed: auth_user.email`；把 UNIQUE 併進第一個 `AlterField` 則會撞既有的重複空字串。

- [ ] **Step 6: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_user_model --noinput
```

預期：`Ran 9 tests` 全部 OK。

- [ ] **Step 7: 驗證沒有殘留未產生的 migration**

```bash
DB_ENGINE=sqlite uv run python manage.py makemigrations --check --dry-run
```

預期：`No changes detected`。

- [ ] **Step 8: 回報，不要 commit**

---

### Task 6: prod 用的 bootstrap 管理指令

**Files:**
- Create: `backend/accounts/management/__init__.py`
- Create: `backend/accounts/management/commands/__init__.py`
- Create: `backend/accounts/management/commands/bootstrap_user_swap.py`
- Modify: `backend/accounts/tests_migration_guards.py`

prod 上 `api.0001_initial` 已套用、`accounts.0001_initial` 未套用，`check_consistent_history` 會拒絕啟動；而該檢查在 `migrate.py:118` 是無條件執行、早於 `--fake`，所以 `--fake` 救不了。只能直接寫入紀錄列。

- [ ] **Step 1: 建目錄**

```bash
cd backend
mkdir -p accounts/management/commands
touch accounts/management/__init__.py accounts/management/commands/__init__.py
```

- [ ] **Step 2: 寫失敗測試**

在 `backend/accounts/tests_migration_guards.py` 末尾追加（`connection` 已在
Task 2 的 import 區匯入）：

```python
class BootstrapUserSwapCommandTests(TestCase):
    """prod 專用的 bootstrap 指令。測試資料庫上 accounts.0001 是真的跑過的，
    因此指令必須偵測到「已存在」而不做事。
    """

    def _run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("bootstrap_user_swap", stdout=out)
        return out.getvalue()

    def test_is_noop_when_record_already_exists(self):
        from django.db.migrations.recorder import MigrationRecorder

        recorder = MigrationRecorder(connection)
        before = recorder.migration_qs.filter(
            app="accounts", name="0001_initial"
        ).count()
        self.assertEqual(before, 1)

        output = self._run()

        after = recorder.migration_qs.filter(
            app="accounts", name="0001_initial"
        ).count()
        self.assertEqual(after, 1)
        self.assertIn("已存在", output)

    def test_inserts_record_when_missing(self):
        from django.db.migrations.recorder import MigrationRecorder

        recorder = MigrationRecorder(connection)
        recorder.migration_qs.filter(app="accounts", name="0001_initial").delete()

        output = self._run()

        self.assertEqual(
            recorder.migration_qs.filter(app="accounts", name="0001_initial").count(),
            1,
        )
        self.assertIn("已寫入", output)
```

- [ ] **Step 3: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards.BootstrapUserSwapCommandTests --noinput
```

預期：`CommandError: Unknown command: 'bootstrap_user_swap'`。

- [ ] **Step 4: 寫指令**

建立 `backend/accounts/management/commands/bootstrap_user_swap.py`：

```python
"""prod 專用：在切換 AUTH_USER_MODEL 之後、執行 migrate 之前跑一次。

為什麼需要這支指令：api/migrations/0001_initial.py 透過 swappable_dependency
宣告依賴，切換設定後會解析成 accounts.0001_initial。prod 上前者已套用、
後者未套用，check_consistent_history 會拋 InconsistentMigrationHistory。

為什麼不能用 migrate --fake：check_consistent_history 在
django/core/management/commands/migrate.py:118 是無條件執行的，早於 --fake
的任何處理，所以 --fake 自己也跑不起來。這是 chicken-and-egg，只能繞過
migrate 直接寫紀錄列。

全新資料庫（dev / CI / 測試）不需要這一步——accounts.0001_initial 會照正常
順序真正執行。本指令偵測到紀錄已存在時不做任何事，可安全重複執行。
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

APP_LABEL = "accounts"
MIGRATION_NAME = "0001_initial"


class Command(BaseCommand):
    help = (
        "把 accounts.0001_initial 標記為已套用，讓 migrate 能在既有資料庫上啟動。"
        "只在既有資料庫需要；全新資料庫上為 no-op。"
    )

    def handle(self, *args, **options):
        recorder = MigrationRecorder(connection)
        recorder.ensure_schema()

        exists = recorder.migration_qs.filter(
            app=APP_LABEL, name=MIGRATION_NAME
        ).exists()

        if exists:
            self.stdout.write(
                f"{APP_LABEL}.{MIGRATION_NAME} 的紀錄已存在，不做任何事。"
            )
            return

        recorder.record_applied(APP_LABEL, MIGRATION_NAME)
        self.stdout.write(
            self.style.SUCCESS(
                f"已寫入 {APP_LABEL}.{MIGRATION_NAME} 的套用紀錄，現在可以執行 migrate。"
            )
        )
```

- [ ] **Step 5: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards --noinput
```

預期：`Ran 8 tests` 全部 OK。

- [ ] **Step 6: 回報，不要 commit**

---

### Task 7: 換掉所有直接 import 內建 User 的地方

**Files:**
- Modify: `backend/api/models.py:2, 754, 786, 811`
- Modify: `backend/api/views.py:10`
- Modify: `backend/api/serializers.py:4`
- Modify: `backend/apps/matching/tests_semantic_tree_lit_node_count.py:8`
- Modify: `backend/apps/matching/tests_m6_trigger_on_close.py:8`
- Modify: `backend/apps/matching/tests_hh_analysis_drift_value.py:6`
- Modify: `backend/apps/summary/tests_assemble.py:7`

這些檔案 import 的是 `django.contrib.auth.models.User`，換掉 `AUTH_USER_MODEL` 之後那已經是**錯的 model**（swapped out）。`Group` 的 import 不受影響，不要動。

- [ ] **Step 1: 確認待改清單**

```bash
cd backend
grep -rn "from django.contrib.auth.models import" --include="*.py" . | grep -v "/.venv/\|/migrations/\|__pycache__" | grep "User"
```

預期 7 個命中（`api/models.py`、`api/serializers.py`、`api/views.py`、4 個測試檔）。
`api/tests_display_settings.py:117` 的 `AnonymousUser` 不在此列，不要動。

（原 Step 2、3 的 `api/models.py` 三處 FK 已於 Task 1b 完成，本 task 不再重複。）

- [ ] **Step 4: 改 api/views.py 與 api/serializers.py**

兩個檔案的 import 都是 `from django.contrib.auth.models import Group, User`。各自改為：

```python
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
```

`Group` 不是 swappable，維持原本的 import。`api/serializers.py` 的
`AccountListSerializer.Meta.model = User` 因此自動指向新 model，不需另外改。

**注意**：`User = get_user_model()` 必須放在模組層級的 import 區之後，且不能放在
其他 `from .models import ...` 之前造成循環匯入——照原本 `User` 出現的位置替換即可。

- [ ] **Step 5: 改四個測試檔**

四個檔案都把 `from django.contrib.auth.models import User` 換成：

```python
from django.contrib.auth import get_user_model

User = get_user_model()
```

檔案清單：
- `backend/apps/matching/tests_semantic_tree_lit_node_count.py:8`
- `backend/apps/matching/tests_m6_trigger_on_close.py:8`
- `backend/apps/matching/tests_hh_analysis_drift_value.py:6`
- `backend/apps/summary/tests_assemble.py:7`

- [ ] **Step 6: 確認沒有殘留**

```bash
grep -rn "from django.contrib.auth.models import" --include="*.py" . | grep -v "/.venv/\|/migrations/\|__pycache__" | grep "User"
```

預期：只剩 `api/tests_display_settings.py:117` 的 `AnonymousUser`。

- [ ] **Step 7: 執行帳號相關測試**

```bash
DB_ENGINE=sqlite uv run python manage.py test api.tests_account_management api.tests_supervisor_admin api.tests_token_is_researcher_claim --noinput
```

預期：全部 OK（`tests_account_management` 有 23 個測試）。

- [ ] **Step 8: 回報，不要 commit**

> **檢查點 1（orchestrator 執行）**：此時 `migrate` 通過且既有測試全綠，提交第一個 commit。
> 之後回到 Task 3 依序往下。

---

### Task 8: Admin 顯示新欄位

**Files:**
- Modify: `backend/accounts/admin.py`
- Modify: `backend/accounts/tests_migration_guards.py`

不把新欄位加進 `fieldsets`，研究者在 Django Admin 就看不到也改不了 `is_research_subject`。

- [ ] **Step 1: 寫失敗測試**

在 `backend/accounts/tests_migration_guards.py` 的 `AdminRegistrationTests` 類別內追加：

```python
    def test_admin_exposes_registration_fields(self):
        """研究者要能在 admin 勾選 is_research_subject，欄位必須進 fieldsets。"""
        from django.contrib import admin

        model_admin = admin.site._registry[User]
        exposed = set()
        for _, options in model_admin.fieldsets:
            exposed.update(options["fields"])

        self.assertIn("email_verified_at", exposed)
        self.assertIn("is_research_subject", exposed)
        self.assertIn("display_name", exposed)

    def test_admin_list_shows_account_status(self):
        from django.contrib import admin

        model_admin = admin.site._registry[User]
        self.assertIn("is_active", model_admin.list_display)
        self.assertIn("last_login", model_admin.list_display)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards.AdminRegistrationTests --noinput
```

預期：`test_admin_exposes_registration_fields` 失敗（`'email_verified_at' not found`）。

- [ ] **Step 3: 實作**

把 `backend/accounts/admin.py` 整個替換為：

```python
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

User = get_user_model()


# 不需要（也不可以）先 unregister：django.contrib.auth.admin 對 swapped model
# 的註冊會被 admin.site.register 靜默忽略，因此這裡是 User 的唯一註冊點。
@admin.register(User)
class BridgeUsUserAdmin(UserAdmin):
    """加了 is_active／last_login 到列表，方便 Supervisor 一眼看帳號狀態，
    並把註冊相關的新欄位放進編輯表單。
    """

    list_display = UserAdmin.list_display + ("is_active", "last_login")
    list_filter = UserAdmin.list_filter + ("is_research_subject",)
    fieldsets = UserAdmin.fieldsets + (
        (
            "BridgeUs",
            {
                "fields": (
                    "display_name",
                    "is_research_subject",
                    "email_verified_at",
                )
            },
        ),
    )
```

- [ ] **Step 4: 執行測試確認通過**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_migration_guards --noinput
```

預期：`Ran 10 tests` 全部 OK。

- [ ] **Step 5: 回報，不要 commit**

---

### Task 9: `display_name` 外洩邊界測試

**Files:**
- Create: `backend/accounts/tests_display_name_boundary.py`

現有設計刻意隱藏對話身份（`ANONYMOUS_MATCH_USER_NAME`、`GodotTicketRedeemView` 只回 `user_id`）。新增 `display_name` 之後必須釘住它不會從這些出口漏出去。

- [ ] **Step 1: 寫測試**

建立 `backend/accounts/tests_display_name_boundary.py`：

```python
"""display_name 的可見範圍邊界。

對話室與 Godot 大廳刻意不顯示真實身份（見 api/views.py 的
ANONYMOUS_MATCH_USER_NAME 與 GodotTicketRedeemView 的 docstring）。
display_name 是新欄位，很容易被順手序列化出去，這裡把邊界釘死。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.godot_tickets import issue_ticket

User = get_user_model()

GODOT_TOKEN = "test-godot-service-token-value"


class GodotBoundaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="subject1",
            password="Xk9$mVpq2Lz",
            display_name="王小明",
        )

    @override_settings(GODOT_SERVICE_TOKEN=GODOT_TOKEN)
    def test_ticket_redeem_returns_only_user_id(self):
        ticket = issue_ticket(user=self.user)

        response = self.client.post(
            "/api/godot/tickets/redeem/",
            {"ticket": ticket.token},
            format="json",
            HTTP_X_GODOT_SERVICE_TOKEN=GODOT_TOKEN,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data.keys()), {"user_id"})
        self.assertNotIn("王小明", str(response.data))


class SerializerBoundaryTests(TestCase):
    def test_match_message_serializer_has_no_display_name(self):
        """對話訊息的送出者名稱一律匿名化，不得帶出 display_name。"""
        from api.serializers import MatchMessageSerializer

        self.assertNotIn("display_name", MatchMessageSerializer().fields)

    def test_account_list_serializer_has_no_display_name(self):
        """帳號管理清單目前不顯示 display_name。若日後要加，請一併更新本測試
        與前端設定頁，而不是預設讓它跟著跑出去。
        """
        from api.serializers import AccountListSerializer

        self.assertNotIn("display_name", AccountListSerializer().fields)
```

- [ ] **Step 2: 執行測試**

```bash
DB_ENGINE=sqlite uv run python manage.py test accounts.tests_display_name_boundary --noinput
```

預期：`Ran 3 tests` 全部 OK。

若 `test_ticket_redeem_returns_only_user_id` 因為路徑或 header 名稱失敗，對照
`backend/api/tests_godot_service_token.py` 的既有寫法調整，並在回報中說明改了什麼。

- [ ] **Step 3: 回報，不要 commit**

---

### Task 10: 全新資料庫端對端驗證

**Files:** 無（純驗證）

- [ ] **Step 1: 用全新的 SQLite 跑完整 migrate**

```bash
cd backend
rm -f /tmp/bridgeus_fresh_check.sqlite3
DB_ENGINE=sqlite SQLITE_PATH=/tmp/bridgeus_fresh_check.sqlite3 \
  uv run python manage.py migrate --noinput
```

預期：所有 migration 套用成功，無 `ContentType.DoesNotExist`、無 `InconsistentMigrationHistory`。

- [ ] **Step 2: 驗證資料表與 ContentType**

```bash
sqlite3 /tmp/bridgeus_fresh_check.sqlite3 \
  "select name from sqlite_master where name in ('auth_user','auth_user_groups','auth_user_user_permissions');"
sqlite3 /tmp/bridgeus_fresh_check.sqlite3 \
  "select app_label, model from django_content_type where model='user';"
```

預期：三張表都在；ContentType 輸出 `accounts|user`。

- [ ] **Step 3: 驗證新欄位存在且 id 仍是 integer**

```bash
sqlite3 /tmp/bridgeus_fresh_check.sqlite3 "pragma table_info(auth_user);"
```

預期：包含 `email_verified_at`、`is_research_subject`、`display_name`；`id` 的型別為 `integer`（**不是** `bigint`）。

- [ ] **Step 4: 驗證研究者權限**

```bash
sqlite3 /tmp/bridgeus_fresh_check.sqlite3 \
  "select p.codename, ct.app_label from auth_permission p
   join django_content_type ct on p.content_type_id=ct.id
   join auth_group_permissions gp on gp.permission_id=p.id
   join auth_group g on g.id=gp.group_id
   where g.name='研究者' and ct.model='user';"
```

預期三列，`app_label` 皆為 `accounts`：`add_user`、`change_user`、`view_user`。

- [ ] **Step 5: 清理**

```bash
rm -f /tmp/bridgeus_fresh_check.sqlite3
```

- [ ] **Step 6: 全量回歸（Postgres）**

碰 `VectorField` 的測試在 SQLite 下會整檔炸在 setup，全量回歸必須用 Postgres。

```bash
uv run python manage.py test --noinput
```

預期：全部通過。**執行期間不可同時啟動另一個 `manage.py test`。**

- [ ] **Step 7: 回報，不要 commit**

逐項附上 Step 1–4、6 的實際輸出。這是整個計畫唯一一次完整驗證，回報請詳細。

> **檢查點 2（orchestrator 執行）**：功能完整且全量回歸通過，提交第二個 commit。

---

### Task 11: prod 遷移演練與正式程序文件

**Files:**
- Create: `docs/superpowers/plans/2026-08-18-custom-user-model-prod-runbook.md`

**這個 task 不碰 prod。** 只產出演練結果與正式程序文件，實際上 prod 由人工在確認演練通過後執行。

- [ ] **Step 1: 取得 prod 快照並在本機還原**

```bash
pg_dump "$PROD_DATABASE_URL" -Fc -f /tmp/bridgeus_prod_snapshot.dump
createdb bridgeus_rehearsal
pg_restore -d bridgeus_rehearsal /tmp/bridgeus_prod_snapshot.dump
```

現用的正式資料庫是 `bridgeus_test`（2026-08-18 查證：497 筆 AI 對話、19 個帳號、
`api` migration 停在 `0027_favorite`）。`bridgeus` 是較舊的副本，不要拿它演練。

- [ ] **Step 2: 記錄遷移前的基準值**

```bash
psql bridgeus_rehearsal -c "select count(*) as users from auth_user;"
psql bridgeus_rehearsal -c "select count(*) from auth_user where email <> '';"
psql bridgeus_rehearsal -c "select email, count(*) from auth_user where email <> '' group by email having count(*) > 1;"
psql bridgeus_rehearsal -c "select app_label, model from django_content_type where model='user';"
```

把數字抄進 runbook。
**若第三個查詢有輸出（重複的非空 email），停下來**——必須先人工處理重複值，`0003` 的 UNIQUE 才加得上去。

- [ ] **Step 3: 在演練庫上執行遷移**

```bash
cd backend
export DB_ENGINE=postgres DB_NAME=bridgeus_rehearsal
uv run python manage.py bootstrap_user_swap
uv run python manage.py migrate --noinput
```

預期：`bootstrap_user_swap` 輸出「已寫入」；`migrate` 套用 `accounts.0002`、`accounts.0003`，無錯誤。

- [ ] **Step 4: 驗證資料完整性**

```bash
psql bridgeus_rehearsal -c "select count(*) as users from auth_user;"
psql bridgeus_rehearsal -c "select app_label, model from django_content_type where model='user';"
psql bridgeus_rehearsal -c "select count(*) from auth_user where email is not null;"
psql bridgeus_rehearsal -c "select p.codename, ct.app_label from auth_permission p
  join django_content_type ct on p.content_type_id=ct.id
  join auth_group_permissions gp on gp.permission_id=p.id
  join auth_group g on g.id=gp.group_id
  where g.name='研究者' and ct.model='user';"
```

預期：使用者數量與 Step 2 完全相同；ContentType 為 `accounts|user`；三個權限的 `app_label` 皆為 `accounts`。

- [ ] **Step 5: 功能驗證**

啟動伺服器連演練庫：

```bash
DB_ENGINE=postgres DB_NAME=bridgeus_rehearsal \
  uv run python manage.py runserver 0.0.0.0:8005 --noreload
```

逐項確認：
- 既有帳號能以原密碼登入（`POST /api/token/`）
- `GET /api/me/` 回傳正確的 `is_researcher`
- 研究者能開啟 `/admin/accounts/user/` 並編輯帳號
- 前端設定頁的 `GET /api/accounts/` 清單正常
- 抽查一筆既有對話：`GET /api/history/conversations/` 有資料
- `/admin/` 的「最近的動作」仍顯示既有紀錄（驗證 `django_admin_log` 沒斷）

- [ ] **Step 6: 寫 runbook**

建立 `docs/superpowers/plans/2026-08-18-custom-user-model-prod-runbook.md`，內容包含：

- 演練日期與所用 dump 的時間戳
- Step 2 與 Step 4 的實際數字對照表
- Step 5 每一項的通過與否
- 正式 prod 程序（依序）：
  1. 公告停機視窗
  2. `pg_dump` 完整備份，記下檔案路徑
  3. 部署含 `accounts` app 與新 `AUTH_USER_MODEL` 的程式碼，**先不執行 migrate**
  4. `uv run python manage.py bootstrap_user_swap`
  5. `uv run python manage.py migrate --noinput`
  6. 重跑 Step 4 與 Step 5 的驗證清單
  7. 恢復服務
- 回滾程序：還原步驟 2 的 dump 並退回前一版程式碼。`accounts.0001` 與 `0002` 沒有 schema 變更，實際產生 DDL 的只有 `0003`。

- [ ] **Step 7: 清理演練庫**

```bash
dropdb bridgeus_rehearsal
rm -f /tmp/bridgeus_prod_snapshot.dump
```

- [ ] **Step 8: 回報，不要 commit**

> **檢查點 3（orchestrator 執行）**：提交 runbook，這是最後一個 commit。

---

## 完成標準

- [ ] `DB_ENGINE=sqlite uv run python manage.py makemigrations --check --dry-run` 回報 `No changes detected`
- [ ] 全新 SQLite 資料庫可完整 migrate，`django_content_type` 的 user 列為 `accounts`
- [ ] `auth_user.id` 仍為 `integer`（非 `bigint`）
- [ ] 全量測試在 Postgres 上通過
- [ ] 「研究者」Group 持有 `accounts.add_user` / `change_user` / `view_user`，且**不**持有 `delete_user`
- [ ] prod 演練通過並產出 runbook
- [ ] 整個遷移只落成 3 個 commit

## 本計畫刻意不做

以下各自獨立，見前置報告：

- 註冊 endpoint、email 驗證信、忘記密碼流程（下一份 spec）
- 登入限流、`DEFAULT_PERMISSION_CLASSES`、WebSocket 的 `is_active` 檢查
- 密碼 validator 少傳 `user` 的修正（報告第 2.3 節）
- username 唯一性 race（報告第 4.1 節）
- `UPDATE_LAST_LOGIN` 設定（報告第 3.5 節）
