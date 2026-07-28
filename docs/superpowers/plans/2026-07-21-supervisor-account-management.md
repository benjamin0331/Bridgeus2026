# Supervisor 帳號管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓「研究者」Django Group 成員（= Supervisor）能透過 Django Admin 新增／編輯／停用帳號，並把其他人加入該 Group 使其也成為 Supervisor。

**Architecture:** 不新增角色層級。用一支 data migration 把 `add_user`/`change_user`/`view_user`/`view_group` 權限授給既有的「研究者」Group（刻意不含 `delete_user`）；用一個 `m2m_changed` signal 讓「加入研究者 Group」自動連動 `is_staff=True`（移出則設回 `False`，superuser 除外），使 Group 成員一步到位能登入 `/admin/`；並自訂 `UserAdmin.list_display` 讓帳號列表顯示 `last_login`／`is_active`。

**Tech Stack:** Django 6.0.4、Django Admin、`django.contrib.auth`、pytest-django（`DJANGO_SETTINGS_MODULE=BridgeUs_Django.settings`）。

參考 spec：[docs/superpowers/specs/2026-07-21-supervisor-account-management-design.md](../specs/2026-07-21-supervisor-account-management-design.md)

---

## File Structure

- **Create** `backend/api/signals.py` — `m2m_changed` handler，唯一職責：同步「研究者」Group 成員的 `is_staff` 旗標。
- **Modify** `backend/api/apps.py` — 在 `ApiConfig.ready()` import signals 完成註冊。
- **Create** `backend/api/migrations/0017_grant_researcher_admin_permissions.py` — 把 user 管理權限授給「研究者」Group（可逆）。
- **Modify** `backend/api/admin.py` — unregister 預設 `UserAdmin`，改註冊一個 `list_display` 加上 `last_login`／`is_active` 的版本。
- **Create** `backend/api/tests_supervisor_admin.py` — 涵蓋 signal 行為與 migration 授權結果。

所有指令都在 `backend/` 目錄下執行（`cd /Users/light/code/backend`），測試用 `uv run pytest`。

---

## Task 1: `is_staff` 自動同步 signal

讓「加入研究者 Group ⇒ `is_staff=True`」「移出研究者 Group ⇒ `is_staff=False`（superuser 除外）」自動發生，涵蓋 M2M 的 forward（`user.groups.add`）與 reverse（`group.user_set.add`）兩個方向。

**Files:**
- Create: `backend/api/signals.py`
- Modify: `backend/api/apps.py`
- Test: `backend/api/tests_supervisor_admin.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/api/tests_supervisor_admin.py`：

```python
"""Supervisor 帳號管理：is_staff 同步 signal 與 migration 授權結果。

「研究者」Django Group（RESEARCHER_GROUP_NAME）成員即 Supervisor。加入該 Group
會自動連動 is_staff=True（否則有 admin 權限也登不進 /admin/）；移出則設回 False，
但 is_superuser 帳號不受影響。見
docs/superpowers/specs/2026-07-21-supervisor-account-management-design.md
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from api.permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


def _researcher_group():
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    return group


class ResearcherGroupStaffSyncTests(TestCase):
    def test_adding_user_to_researcher_group_sets_is_staff(self):
        user = User.objects.create_user(username="new_supervisor", password="pw")
        self.assertFalse(user.is_staff)

        user.groups.add(_researcher_group())

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_reverse_add_via_group_user_set_sets_is_staff(self):
        user = User.objects.create_user(username="reverse_add", password="pw")
        group = _researcher_group()

        group.user_set.add(user)  # reverse direction of the M2M

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_removing_user_from_researcher_group_clears_is_staff(self):
        user = User.objects.create_user(username="demoted", password="pw")
        group = _researcher_group()
        user.groups.add(group)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

        user.groups.remove(group)

        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_superuser_removed_from_group_keeps_is_staff(self):
        user = User.objects.create_superuser(username="root", password="pw")
        group = _researcher_group()
        user.groups.add(group)

        user.groups.remove(group)

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_adding_to_non_researcher_group_does_not_change_is_staff(self):
        user = User.objects.create_user(username="plain", password="pw")
        other, _ = Group.objects.get_or_create(name="其他組")

        user.groups.add(other)

        user.refresh_from_db()
        self.assertFalse(user.is_staff)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest api/tests_supervisor_admin.py::ResearcherGroupStaffSyncTests -v`
Expected: FAIL — signal 尚未存在，`test_adding_user_to_researcher_group_sets_is_staff` 等會因 `is_staff` 仍為 `False` 而 AssertionError。

- [ ] **Step 3: 建立 signal**

Create `backend/api/signals.py`：

```python
"""把「研究者」Group 成員身分與 is_staff 旗標保持同步。

研究者身分（Group）與 is_staff（能否登入 /admin/）在本專案刻意解耦
（見 api.permissions），但 Supervisor 需要兩者一致：加入「研究者」Group 就該
能登入 admin 管理帳號。這個 signal 讓「加入 Group」一步到位設好 is_staff，
不用在 admin 另外勾一次 Staff status。移出時設回 False，但 superuser 不動。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


@receiver(m2m_changed, sender=User.groups.through)
def sync_is_staff_with_researcher_group(
    sender, instance, action, pk_set, reverse, **kwargs
):
    if action not in ("post_add", "post_remove"):
        return

    researcher_group = Group.objects.filter(name=RESEARCHER_GROUP_NAME).first()
    if researcher_group is None:
        return

    # M2M 有兩個方向：
    #   forward (reverse=False)：instance 是 User，pk_set 是一組 Group id
    #   reverse (reverse=True) ：instance 是 Group，pk_set 是一組 User id
    if reverse:
        if instance.pk != researcher_group.pk:
            return
        users = list(User.objects.filter(pk__in=(pk_set or set())))
    else:
        if researcher_group.pk not in (pk_set or set()):
            return
        users = [instance]

    for user in users:
        _apply_staff_flag(user, action, researcher_group)


def _apply_staff_flag(user, action, researcher_group):
    if action == "post_add":
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
        return

    # post_remove：superuser 不動；若仍屬於研究者 Group 也不動；否則收回 is_staff。
    if user.is_superuser:
        return
    if user.groups.filter(pk=researcher_group.pk).exists():
        return
    if user.is_staff:
        user.is_staff = False
        user.save(update_fields=["is_staff"])
```

- [ ] **Step 4: 在 app ready() 註冊 signal**

Modify `backend/api/apps.py`：

```python
from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        from . import signals  # noqa: F401  # 註冊 m2m_changed handler
```

- [ ] **Step 5: 執行測試確認通過**

Run: `uv run pytest api/tests_supervisor_admin.py::ResearcherGroupStaffSyncTests -v`
Expected: PASS（5 個測試全過）。

- [ ] **Step 6: Commit**

```bash
git add api/signals.py api/apps.py api/tests_supervisor_admin.py
git commit -m "feat(auth): sync is_staff with 研究者 group membership"
```

---

## Task 2: 授予「研究者」Group 帳號管理權限（migration）

把 `add_user`/`change_user`/`view_user`/`view_group` 授給「研究者」Group，讓成員登入 admin 後能新增／編輯／查看帳號，但**不含** `delete_user`。

**Files:**
- Create: `backend/api/migrations/0017_grant_researcher_admin_permissions.py`
- Test: `backend/api/tests_supervisor_admin.py`（新增一個 TestCase）

- [ ] **Step 1: 寫失敗測試**

在 `backend/api/tests_supervisor_admin.py` 末端追加：

```python
class ResearcherGroupPermissionTests(TestCase):
    """0017 migration 應把 user 管理權限授給「研究者」Group（不含 delete）。"""

    def setUp(self):
        self.group = Group.objects.get(name=RESEARCHER_GROUP_NAME)

    def _codenames(self):
        return set(
            self.group.permissions.values_list("codename", flat=True)
        )

    def test_group_can_add_change_view_user(self):
        codenames = self._codenames()
        self.assertIn("add_user", codenames)
        self.assertIn("change_user", codenames)
        self.assertIn("view_user", codenames)

    def test_group_can_view_group(self):
        self.assertIn("view_group", self._codenames())

    def test_group_cannot_delete_user(self):
        self.assertNotIn("delete_user", self._codenames())
```

注意：這裡用 `Group.objects.get(...)`（非 `get_or_create`）——「研究者」Group 由 0013 migration 建立，測試 DB 套用 migration 後就存在；若 get 不到代表 migration 沒跑，本來就該失敗。

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest api/tests_supervisor_admin.py::ResearcherGroupPermissionTests -v`
Expected: FAIL — 0017 尚未建立，Group 沒有這些權限，`test_group_can_add_change_view_user` 因 `add_user` 不在集合中而 AssertionError。

- [ ] **Step 3: 建立 migration**

Create `backend/api/migrations/0017_grant_researcher_admin_permissions.py`：

```python
# Hand-written (no live Django env to run makemigrations), matching the style of
# 0013_create_researcher_group.py. Grants the 研究者 Group the Django Admin
# permissions needed to manage accounts (add/change/view user + view group),
# deliberately WITHOUT delete_user — account deletion cascades experiment data
# and is intentionally not exposed to Supervisors. See
# docs/superpowers/specs/2026-07-21-supervisor-account-management-design.md

from django.contrib.auth.management import create_permissions
from django.db import migrations

RESEARCHER_GROUP_NAME = "研究者"

USER_CODENAMES = ["add_user", "change_user", "view_user"]
GROUP_CODENAMES = ["view_group"]


def _ensure_auth_permissions(apps):
    # 全新的 migrate：auth/User 的預設權限由 post_migrate 建立，而 post_migrate
    # 要等所有 migration 跑完才觸發——此刻權限可能還不存在。手動先補建，確保
    # 下面 filter 抓得到。既有 DB 已有這些權限時，create_permissions 是 no-op。
    from django.apps import apps as global_apps

    auth_config = global_apps.get_app_config("auth")
    create_permissions(auth_config, apps=apps, verbosity=0)


def grant_permissions(apps, schema_editor):
    _ensure_auth_permissions(apps)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user_ct = ContentType.objects.get(app_label="auth", model="user")
    group_ct = ContentType.objects.get(app_label="auth", model="group")

    perms = list(
        Permission.objects.filter(content_type=user_ct, codename__in=USER_CODENAMES)
    ) + list(
        Permission.objects.filter(content_type=group_ct, codename__in=GROUP_CODENAMES)
    )
    group.permissions.add(*perms)


def revoke_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        group = Group.objects.get(name=RESEARCHER_GROUP_NAME)
    except Group.DoesNotExist:
        return

    user_ct = ContentType.objects.filter(app_label="auth", model="user").first()
    group_ct = ContentType.objects.filter(app_label="auth", model="group").first()
    perms = []
    if user_ct:
        perms += list(
            Permission.objects.filter(content_type=user_ct, codename__in=USER_CODENAMES)
        )
    if group_ct:
        perms += list(
            Permission.objects.filter(content_type=group_ct, codename__in=GROUP_CODENAMES)
        )
    group.permissions.remove(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0016_issuereaction"),
        ("auth", "__first__"),
        ("contenttypes", "__first__"),
    ]

    operations = [
        migrations.RunPython(grant_permissions, revoke_permissions),
    ]
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest api/tests_supervisor_admin.py::ResearcherGroupPermissionTests -v`
Expected: PASS（3 個測試全過）。

- [ ] **Step 5: Commit**

```bash
git add api/migrations/0017_grant_researcher_admin_permissions.py api/tests_supervisor_admin.py
git commit -m "feat(auth): grant 研究者 group user-management admin permissions"
```

---

## Task 3: 帳號列表顯示 last_login／is_active

自訂 `UserAdmin`，讓 Supervisor 在使用者列表能看到最後登入時間與啟用狀態（停用＝在編輯頁把 `is_active` 取消勾選，Django 內建欄位，無需額外程式）。

**Files:**
- Modify: `backend/api/admin.py`
- Test: `backend/api/tests_supervisor_admin.py`（新增一個 TestCase）

- [ ] **Step 1: 寫失敗測試**

在 `backend/api/tests_supervisor_admin.py` 末端追加：

```python
class UserAdminListDisplayTests(TestCase):
    """自訂 UserAdmin 應在列表顯示 last_login 與 is_active。"""

    def test_user_admin_list_display_includes_last_login_and_is_active(self):
        from django.contrib import admin as django_admin

        user_admin = django_admin.site._registry[User]
        self.assertIn("last_login", user_admin.list_display)
        self.assertIn("is_active", user_admin.list_display)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest api/tests_supervisor_admin.py::UserAdminListDisplayTests -v`
Expected: FAIL — 目前註冊的是 Django 預設 `UserAdmin`，`list_display` 為 `('username', 'email', 'first_name', 'last_name', 'is_staff')`，不含 `last_login`／`is_active`。

- [ ] **Step 3: 自訂 UserAdmin**

在 `backend/api/admin.py` 檔案**頂端 import 區塊之後、既有 `@admin.register(...)` 之前**插入：

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

現有 `api/admin.py` 第一行是 `from django.contrib import admin`，維持不動；把上面這段接在它與 `from .models import (...)` 之後即可。

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest api/tests_supervisor_admin.py::UserAdminListDisplayTests -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add api/admin.py api/tests_supervisor_admin.py
git commit -m "feat(admin): show last_login and is_active in user list"
```

---

## Task 4: 全套件回歸與收尾

確認新增內容沒有破壞既有測試，並更新開發狀態文件。

**Files:**
- Modify: `CONTEXT.md`

- [ ] **Step 1: 跑本檔全部測試**

Run: `uv run pytest api/tests_supervisor_admin.py -v`
Expected: PASS（Task 1–3 共 10 個測試全過）。

- [ ] **Step 2: 跑 api app 主要套件確認無回歸**

Run: `uv run pytest api/tests_supervisor_admin.py api/tests_viewpoint_review.py api/tests_token_is_researcher_claim.py -v`
Expected: PASS，無 error/failure。（`api/tests.py` 全套跑一次約 10 分鐘，如需完整回歸再單獨執行 `uv run pytest api/tests.py -q`。）

- [ ] **Step 3: 更新 CONTEXT.md 待辦**

在 `CONTEXT.md` 的「模組現況」表 M1 一列，或「待辦」區塊，記一筆：Supervisor 帳號管理已完成（研究者 Group 具 Django Admin 帳號管理權限、加入 Group 自動連動 is_staff、帳號列表顯示 last_login／is_active；刪除帳號刻意不開放）。實際措辭依現況微調，保持與周邊條目一致。

- [ ] **Step 4: Commit**

```bash
git add CONTEXT.md
git commit -m "docs: record supervisor account management as done"
```

---

## Self-Review Notes

- **Spec 覆蓋**：§設計 1（migration 授權）→ Task 2；§設計 2（is_staff signal）→ Task 1；§設計 3（list_display last_login）→ Task 3；§設計 4（新帳號預設值）→ 沿用 Django 預設，無需程式碼，測試不需覆蓋；§不做的事（不刪除）→ Task 2 明確排除 `delete_user`；（不動前端／`IsResearcher`／自助註冊）→ 計畫未觸及這些檔案。
- **無 placeholder**：所有 code step 均為完整可貼上的內容。
- **型別一致**：`RESEARCHER_GROUP_NAME` 一律由 `api.permissions` import；signal 的 `_apply_staff_flag(user, action, researcher_group)` 簽名在定義與呼叫端一致；migration 常數 `USER_CODENAMES`／`GROUP_CODENAMES` 定義後於 grant/revoke 兩處共用。
