from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

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
