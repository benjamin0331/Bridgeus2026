"""Mark pre-existing duplicate PostDialogueResponse rows as superseded.

Before the post_response_user_session_unique / post_response_user_room_unique
constraints existed (see the next migration), nothing stopped a participant
from submitting the post-dialogue questionnaire more than once for the same
session_id or room_id. A handful of such duplicates already exist in some
databases. We don't delete them — the research data must not silently lose
rows — we keep the newest submission per (user, session_id)/(user, room_id)
as the live one and mark every earlier duplicate as is_superseded=True, which
the default manager (PostDialogueResponse.objects) now excludes. The next
migration's uniqueness constraints only apply to non-superseded rows, so this
must run first or that migration fails on any database that already has
duplicates.
"""
from django.db import migrations
from django.db.models import Count


def _supersede_older_duplicates(apps, schema_editor, *, field_name):
    PostDialogueResponse = apps.get_model("api", "PostDialogueResponse")
    duplicate_groups = (
        PostDialogueResponse.objects.exclude(**{f"{field_name}__isnull": True})
        .values("user_id", field_name)
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    for group in duplicate_groups:
        rows = list(
            PostDialogueResponse.objects.filter(
                user_id=group["user_id"], **{field_name: group[field_name]}
            ).order_by("-created_at", "-id")
        )
        # rows[0] is the newest — keep it live, supersede the rest.
        for row in rows[1:]:
            row.is_superseded = True
            row.save(update_fields=["is_superseded"])


def supersede_older_duplicates(apps, schema_editor):
    _supersede_older_duplicates(apps, schema_editor, field_name="session_id")
    _supersede_older_duplicates(apps, schema_editor, field_name="room_id")


def unsupersede_all(apps, schema_editor):
    PostDialogueResponse = apps.get_model("api", "PostDialogueResponse")
    PostDialogueResponse.objects.filter(is_superseded=True).update(
        is_superseded=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0030_post_response_is_superseded"),
    ]

    operations = [
        migrations.RunPython(supersede_older_duplicates, unsupersede_all),
    ]
