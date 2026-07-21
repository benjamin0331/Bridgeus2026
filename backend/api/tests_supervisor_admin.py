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
