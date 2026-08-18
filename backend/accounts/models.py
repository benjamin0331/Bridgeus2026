from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """接管既有 auth_user 資料表的專案自有 User。

    db_table 指回 "auth_user" 是整個遷移方案的核心：資料表名稱與既有表相同，
    因此 20 個既有外鍵在 DB 層一個位元組都沒變，M2M 中介表也自動沿用
    auth_user_groups / auth_user_user_permissions 這兩個既有名稱。

    註冊用的新欄位刻意不放在這裡——0001_initial 必須是 auth.User 的精確
    複製品才能在 prod 上被安全地視為已套用。新欄位見 migration 0003。
    """

    class Meta(AbstractUser.Meta):
        db_table = "auth_user"
