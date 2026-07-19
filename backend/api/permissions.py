"""共用權限類別。

RESEARCHER_GROUP_NAME 這個 Django Group 用來表達「有沒有研究者身分」，跟
Django 內建的 is_staff（能不能登入 /admin/）是兩件不一定相同的事——用獨立的
Group 表達更準確，之後研究團隊成員增加時，也不用連帶給對方 Django admin 的
存取權限。Group 由 api/migrations/0013_create_researcher_group.py 建立。
"""

import hmac

from django.conf import settings
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


class IsGodotServiceToken(BasePermission):
    """Godot 常駐 headless server 專用：驗證 X-Godot-Service-Token 標頭，不需要
    （也不接受）user JWT——呼叫者是 server，不代表任何一位玩家，所以不套
    「requester 必須是參與者」這類限制。見 godot-web-deployment-spec.md §4。
    """

    message = "缺少或錯誤的服務金鑰。"

    def has_permission(self, request, view):
        configured = getattr(settings, "GODOT_SERVICE_TOKEN", "") or ""
        if not configured:
            return False
        provided = request.META.get("HTTP_X_GODOT_SERVICE_TOKEN", "")
        # 比對 bytes 而非 str：Django 用 latin-1 解 header，任何 >127 的位元組都會
        # 變成非 ASCII str，而 compare_digest 對非 ASCII str 會丟 TypeError——
        # 那不會放行（仍是拒絕），但會變成 500，等於讓任何人都能灌錯誤日誌。
        # encode 後兩邊都是 bytes，compare_digest 照樣是常數時間比對。
        return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))
