from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """接管既有 auth_user 資料表的專案自有 User。

    db_table 指回 "auth_user" 是整個遷移方案的核心：資料表名稱與既有表相同，
    因此 20 個既有外鍵在 DB 層一個位元組都沒變，M2M 中介表也自動沿用
    auth_user_groups / auth_user_user_permissions 這兩個既有名稱。
    """

    # DB 層允許 NULL：既有帳號都是研究者代開、沒有 email，不能強制 NOT NULL。
    # 「註冊必填」只在註冊 serializer 強制（下一份 spec）。
    email = models.EmailField("email address", unique=True, null=True, blank=True)

    # 先永遠是 None。之後要做驗證信時不用再動 migration。
    email_verified_at = models.DateTimeField(null=True, blank=True)

    # 區分「正式受試者」與「公開註冊的一般使用者」，供研究分析篩選。
    is_research_subject = models.BooleanField(default=False)

    # 只在設定頁與知識庫署名可見，不進對話室、不進 Godot。
    # 見 spec 第 7 節與 tests_display_name_boundary.py。
    display_name = models.CharField(max_length=50, blank=True)

    # 使用者註冊時同意的研究說明版本（accounts.consent.CONSENT_VERSION）。
    # 空字串 = 沒有同意紀錄——研究者代開的帳號與遷移前的既有帳號都是這一類。
    # 存的是簽署當下的版本字串，日後改版不會動到既有紀錄。
    consent_version = models.CharField(max_length=32, blank=True)

    class Meta(AbstractUser.Meta):
        db_table = "auth_user"

    def save(self, *args, **kwargs):
        # UserManager.create_user 在沒給 email 時會塞空字串，而空字串在
        # UNIQUE 之下會互相碰撞（NULL 才不會）。在這裡統一正規化成 None，
        # 讓「沒有 email」只有一種表示法。
        if not self.email:
            self.email = None
        super().save(*args, **kwargs)
