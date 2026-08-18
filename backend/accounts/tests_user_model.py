"""accounts.User 的結構性測試。

重點不在「欄位能不能存」，而在「有沒有真的接管既有的 auth_user 資料表」——
db_table 或 M2M 中介表名稱一旦跑掉，20 個既有外鍵就會指向錯的地方。
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
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
