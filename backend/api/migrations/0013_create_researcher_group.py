# Generated manually (no live Django env available to run makemigrations —
# hand-written to match Django 6.0's migration format).
#
# Creates the "研究者" Django Group used by api.permissions.IsResearcher, and
# migrates any existing is_staff=True user into it so nobody loses access to
# the M6 觀點知識庫審核 endpoints when the permission check switches from
# IsAdminUser (is_staff) to IsResearcher (group membership).

from django.conf import settings
from django.db import migrations

RESEARCHER_GROUP_NAME = "研究者"


def _user_model(apps):
    """從 AUTH_USER_MODEL 取歷史 model。

    寫死 apps.get_model("auth", "User") 在切換之後會拿到 swapped-out 的
    歷史 model，它沒有 manager：
        AttributeError: Manager isn't available; 'auth.User' has been
        swapped for 'accounts.User'
    """
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    return apps.get_model(app_label, model_name)


def create_researcher_group_and_migrate_staff(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = _user_model(apps)

    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    for user in User.objects.filter(is_staff=True):
        user.groups.add(group)


def remove_researcher_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=RESEARCHER_GROUP_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0012_ccndtimelineunlock"),
        ("auth", "__first__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            create_researcher_group_and_migrate_staff,
            remove_researcher_group,
        ),
    ]
