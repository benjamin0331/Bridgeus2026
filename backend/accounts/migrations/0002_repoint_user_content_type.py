"""把 django_content_type 裡 user 那一列的 app_label 由 auth 原地改成 accounts。

不這樣做的話，post_migrate 會另外建立一組 accounts/user 的權限，而
api/migrations/0017 指派給「研究者」Group 的那三個權限仍綁在舊的 auth/user
ContentType 上，隨即變成孤兒——研究者的 Django Admin 帳號管理會整個失效。

原地改 app_label 讓 Permission 列、Group 的權限指派、以及 django_admin_log
的既有紀錄全部跟著轉移，不需搬動任何資料。
"""

from django.db import migrations


def repoint_forward(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")

    # 冪等：全新資料庫上 accounts/user 會由 post_migrate 或 api/0017 建立，
    # 而 auth/user 從來不存在。(app_label, model) 有 unique 約束，兩列同時
    # 存在時 update 會撞約束，所以這個檢查是必要的而非防禦性冗餘。
    if ContentType.objects.filter(app_label="accounts", model="user").exists():
        return
    ContentType.objects.filter(app_label="auth", model="user").update(
        app_label="accounts"
    )


def repoint_backward(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    if ContentType.objects.filter(app_label="auth", model="user").exists():
        return
    ContentType.objects.filter(app_label="accounts", model="user").update(
        app_label="auth"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("contenttypes", "__first__"),
    ]

    operations = [
        migrations.RunPython(repoint_forward, repoint_backward),
    ]
