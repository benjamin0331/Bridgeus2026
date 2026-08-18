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
