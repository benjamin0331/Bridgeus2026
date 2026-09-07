from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import EmailVerificationCode, PasswordResetCode

User = get_user_model()


# 不需要（也不可以）先 unregister：django.contrib.auth.admin 對 swapped model
# 的註冊會被 admin.site.register 靜默忽略，因此這裡是 User 的唯一註冊點。
@admin.register(User)
class BridgeUsUserAdmin(UserAdmin):
    """加了 is_active／last_login 到列表，方便 Supervisor 一眼看帳號狀態，
    並把註冊相關的新欄位放進編輯表單。
    """

    list_display = UserAdmin.list_display + ("is_active", "last_login")
    list_filter = UserAdmin.list_filter + ("is_research_subject",)
    fieldsets = UserAdmin.fieldsets + (
        (
            "BridgeUs",
            {
                "fields": (
                    "display_name",
                    "is_research_subject",
                    "email_verified_at",
                )
            },
        ),
    )


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    """唯讀：驗證碼明碼從不落庫（只存 SHA-256），這裡看不到、也不該從後台
    重設別人的密碼——那條路是使用者列表的「重設密碼」動作。留這個註冊點是
    為了排查「使用者說沒收到信 / 一直說碼錯」時，能看到碼的建立時間、是否
    過期、驗錯幾次。
    """

    list_display = (
        "user",
        "created_at",
        "expires_at",
        "consumed_at",
        "attempt_count",
    )
    list_filter = ("created_at",)
    search_fields = ("user__username", "user__email")
    readonly_fields = (
        "user",
        "code_hash",
        "created_at",
        "expires_at",
        "consumed_at",
        "attempt_count",
    )

    def has_add_permission(self, request):
        return False


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    """唯讀，用途同 PasswordResetCodeAdmin：排查「使用者說沒收到驗證信 /
    一直說碼錯」。明碼從不落庫（只存 SHA-256）。
    """

    list_display = (
        "email",
        "user",
        "created_at",
        "expires_at",
        "verified_at",
        "consumed_at",
        "attempt_count",
    )
    list_filter = ("created_at", "verified_at")
    search_fields = ("email", "user__username")
    readonly_fields = (
        "email",
        "user",
        "code_hash",
        "created_at",
        "expires_at",
        "verified_at",
        "consumed_at",
        "attempt_count",
    )

    def has_add_permission(self, request):
        return False
