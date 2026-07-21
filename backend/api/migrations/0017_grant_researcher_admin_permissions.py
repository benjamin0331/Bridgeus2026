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


def _ensure_auth_permissions(apps, using):
    # 全新的 migrate：auth/User 的預設權限由 post_migrate 建立，而 post_migrate
    # 要等所有 migration 跑完才觸發——此刻權限可能還不存在。手動先補建，確保
    # 下面 filter 抓得到。既有 DB 已有這些權限時，create_permissions 是 no-op。
    from django.apps import apps as global_apps

    auth_config = global_apps.get_app_config("auth")
    create_permissions(auth_config, apps=apps, using=using, verbosity=0)


def grant_permissions(apps, schema_editor):
    _ensure_auth_permissions(apps, schema_editor.connection.alias)

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
