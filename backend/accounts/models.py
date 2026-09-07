import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


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

    # 第一次看完新手導覽（OnboardingTour）的時間；NULL = 還沒看過，登入後
    # 會自動跳出來。放這裡而不是 localStorage：受試者可能換裝置或清瀏覽器
    # 資料，而「自動跳出來」只該發生一次。設定頁的「重看導覽」是手動開啟，
    # 不會動這個欄位，所以重看之後下次登入也不會又自動跳。
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(AbstractUser.Meta):
        db_table = "auth_user"

    def save(self, *args, **kwargs):
        # UserManager.create_user 在沒給 email 時會塞空字串，而空字串在
        # UNIQUE 之下會互相碰撞（NULL 才不會）。在這裡統一正規化成 None，
        # 讓「沒有 email」只有一種表示法。
        if not self.email:
            self.email = None
        super().save(*args, **kwargs)


class PasswordResetCode(models.Model):
    """忘記密碼時寄到信箱的一次性驗證碼。

    **只存雜湊不存明碼**：這張表（或它進到的 log／備份）外洩時，不能直接
    拿來重設任何人的密碼。比對時把使用者輸入的碼做同樣的 SHA-256 再比
    `code_hash`。

    一個帳號同時只該有一組有效的碼：`issue()` 在建新碼之前，會把該帳號所有
    尚未使用的舊碼標記成已用（`consumed_at`）。

    `attempt_count` 是暴力破解的主要防線——限流依 IP 計數，攻擊者換 IP 就能
    繞過，但錯 `MAX_ATTEMPTS` 次就作廢這組碼，六位數字在 10 分鐘內只有 5 次
    機會。
    """

    MAX_ATTEMPTS = 5

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_codes",
    )
    code_hash = models.CharField(max_length=64)  # SHA-256 hexdigest
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "consumed_at"])]

    def __str__(self):
        return f"PasswordResetCode(user={self.user_id}, created={self.created_at:%Y-%m-%d %H:%M})"

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    @classmethod
    def issue(cls, user) -> tuple["PasswordResetCode", str]:
        """作廢該帳號現有的碼，產一組新的六位數字碼。

        回傳 `(instance, 明碼)`——明碼只在這一刻存在於記憶體，寄完信就沒有
        任何地方留著它。
        """
        now = timezone.now()
        cls.objects.filter(user=user, consumed_at__isnull=True).update(
            consumed_at=now
        )
        code = f"{secrets.randbelow(1_000_000):06d}"
        ttl = getattr(settings, "PASSWORD_RESET_CODE_TTL_MINUTES", 10)
        instance = cls.objects.create(
            user=user,
            code_hash=cls.hash_code(code),
            expires_at=now + timedelta(minutes=ttl),
        )
        return instance, code

    def is_usable(self) -> bool:
        return (
            self.consumed_at is None
            and self.attempt_count < self.MAX_ATTEMPTS
            and timezone.now() < self.expires_at
        )
