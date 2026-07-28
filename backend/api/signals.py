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
    # 只處理 post_add / post_remove；post_clear（groups.clear()）刻意不納入——
    # admin 的 group 元件用 .set()，發出的是 add/remove diff 而非 clear。
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
