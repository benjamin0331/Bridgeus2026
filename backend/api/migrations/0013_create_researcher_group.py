# Generated manually (no live Django env available to run makemigrations —
# hand-written to match Django 6.0's migration format).
#
# Creates the "研究者" Django Group used by api.permissions.IsResearcher, and
# migrates any existing is_staff=True user into it so nobody loses access to
# the M6 觀點知識庫審核 endpoints when the permission check switches from
# IsAdminUser (is_staff) to IsResearcher (group membership).

from django.db import migrations

RESEARCHER_GROUP_NAME = "研究者"


def create_researcher_group_and_migrate_staff(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

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
    ]

    operations = [
        migrations.RunPython(
            create_researcher_group_and_migrate_staff,
            remove_researcher_group,
        ),
    ]
