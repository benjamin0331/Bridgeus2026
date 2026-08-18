from django.apps import AppConfig


class AccountsConfig(AppConfig):
    # 必須明確指定 AutoField。Django 6 全域預設是 BigAutoField，而既有
    # auth_user.id 是 django.contrib.auth 用 AutoField 建的 integer 欄位。
    # 不指定會讓 model 狀態變成 bigint、與實際資料表不一致。
    default_auto_field = "django.db.models.AutoField"
    name = "accounts"
