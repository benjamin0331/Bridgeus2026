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


class EmailBackfillOperationOrderTests(TestCase):
    """0003 的 operations 順序不變式。

    為什麼要用結構斷言而不是行為測試：全新的測試資料庫裡一個帳號都沒有，
    `filter(email="").update(email=None)` 匹配到零列、靜默成功，**這個 bug
    在任何測試資料庫上都測不出來**。它只在既有資料庫上顯現 —— 實測對
    backend/db.sqlite3 的副本（4 個帳號、email 全為空字串）執行會得到
    `IntegrityError: NOT NULL constraint failed: auth_user.email`。

    正確順序必須是三步：
      1. AlterField 讓 email 可為 NULL（此時不能加 UNIQUE，既有空字串會相撞）
      2. RunPython 把空字串回填成 NULL
      3. AlterField 加上 UNIQUE（此時已無重複值）

    真正的端對端驗證在 Task 11 的 production 快照演練。
    """

    def _operations(self):
        import importlib

        module = importlib.import_module(
            "accounts.migrations.0003_user_registration_fields"
        )
        return module.Migration.operations

    def _email_alter_indexes(self, ops):
        from django.db import migrations as m

        return [
            (i, op.field.unique)
            for i, op in enumerate(ops)
            if isinstance(op, m.AlterField) and op.name == "email"
        ]

    def _runpython_index(self, ops):
        from django.db import migrations as m

        hits = [i for i, op in enumerate(ops) if isinstance(op, m.RunPython)]
        self.assertEqual(len(hits), 1, "0003 應該只有一個 RunPython（email 回填）")
        return hits[0]

    def test_email_is_made_nullable_before_the_backfill_runs(self):
        """回填要寫 NULL，欄位必須先允許 NULL，否則 NOT NULL 約束會擋下來。"""
        ops = self._operations()
        alters = self._email_alter_indexes(ops)
        backfill = self._runpython_index(ops)

        nullable_first = [i for i, unique in alters if i < backfill]
        self.assertTrue(
            nullable_first,
            "RunPython 之前必須先有一個把 email 改成 null=True 的 AlterField",
        )
        for i, unique in alters:
            if i < backfill:
                self.assertFalse(
                    unique,
                    "回填之前不可加 UNIQUE：既有帳號的空字串 email 會互相相撞",
                )

    def test_unique_constraint_is_added_after_the_backfill(self):
        ops = self._operations()
        alters = self._email_alter_indexes(ops)
        backfill = self._runpython_index(ops)

        unique_after = [i for i, unique in alters if unique and i > backfill]
        self.assertTrue(
            unique_after,
            "UNIQUE 必須在回填之後才加上，否則會撞到既有的重複空字串",
        )
