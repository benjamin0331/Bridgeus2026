# 一次性資料清理：把「該關而沒關」的 AI 對話 session 標記為 closed。
#
# 兩個漏關的路徑（送出後測問卷、建立新對話）都已經在程式端修好，但修正只對
# 之後的新資料生效。既有的殘留仍會讓 /api/dialogue/sessions/latest/ 一直回報
# 「有對話可以繼續」，使用者因此結束不掉對話，也永遠看不到沿用上次立場的彈窗。
#
# 判定規則見 api/session_cleanup.py，與 manage.py close_stale_dialogue_sessions
# 指令共用同一份邏輯。
#
# 無法反向：舊的 status 沒有另外留存，而且把這些 session 改回 active 正是要
# 修掉的錯誤狀態，所以 reverse 是 no-op（migration 本身仍可回退）。

from django.db import migrations

from api.session_cleanup import find_closable_session_ids


def close_stale_sessions(apps, schema_editor):
    DialogueSessionRecord = apps.get_model("api", "DialogueSessionRecord")
    PostDialogueResponse = apps.get_model("api", "PostDialogueResponse")

    closable = find_closable_session_ids(
        DialogueSessionRecord=DialogueSessionRecord,
        PostDialogueResponse=PostDialogueResponse,
    )
    if not closable:
        return

    DialogueSessionRecord.objects.filter(
        session_id__in=closable, status="active"
    ).update(status="closed")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0017_grant_researcher_admin_permissions"),
    ]

    operations = [
        migrations.RunPython(close_stale_sessions, noop_reverse),
    ]
