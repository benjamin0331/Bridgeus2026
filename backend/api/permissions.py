"""共用權限類別。

RESEARCHER_GROUP_NAME 這個 Django Group 用來表達「有沒有研究者身分」，跟
Django 內建的 is_staff（能不能登入 /admin/）是兩件不一定相同的事——用獨立的
Group 表達更準確，之後研究團隊成員增加時，也不用連帶給對方 Django admin 的
存取權限。Group 由 api/migrations/0013_create_researcher_group.py 建立。
"""

from rest_framework.permissions import BasePermission

RESEARCHER_GROUP_NAME = "研究者"


class IsResearcher(BasePermission):
    """使用者要屬於「研究者」Django Group 才有權限。"""

    message = "此功能僅限研究者使用。"

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.groups.filter(name=RESEARCHER_GROUP_NAME).exists()
        )
