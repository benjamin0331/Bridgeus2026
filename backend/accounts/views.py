"""自助註冊端點。

與 api/views.py 的 AccountListCreateView（研究者代開帳號）平行存在。
放在 accounts app 而不是 api：後者的 views.py 已經 4400 行，而 accounts
本來就擁有 User model。
"""

import logging
import secrets

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.serializers import BridgeUsTokenObtainPairSerializer
from api.token_revocation import revoke_user_tokens

from .consent import CONSENT_DOCUMENT, CONSENT_VERSION
from .emails import send_password_reset_code
from .models import PasswordResetCode, User
from .serializers import (
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegistrationSerializer,
)

logger = logging.getLogger(__name__)


class ConsentDocumentView(APIView):
    """GET /api/consent/ — 研究參與說明全文 + 版本號。

    公開端點（免登入）：受試者必須在建立帳號前就看得到。內容是後端程式碼
    常數（見 accounts/consent.py），前端 ConsentPage 只負責渲染，不再自己
    抄一份。回傳的 version 與註冊時記進 User.consent_version 的是同一個值。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(
            {"version": CONSENT_VERSION, "document": CONSENT_DOCUMENT}
        )


class RegistrationView(APIView):
    """POST /api/register/ — 受試者自助註冊，成功後直接發 token。

    直接發 token（自動登入）而不是導回登入頁：受試者少一道手續，降低流失。

    限流的預設值刻意放寬（見 settings 的 register scope）：DRF 對未認證請求
    依 IP 計數，而受試者會在同一個場地集體註冊、共用 NAT 出口 IP。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # 用登入用的同一個 serializer 產生 token，確保 is_researcher claim 存在。
        # RefreshToken.for_user(user) 不會帶那個 claim，前端解 token 會讀不到。
        refresh = BridgeUsTokenObtainPairSerializer.get_token(user)

        return Response(
            {
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "is_researcher": False,
                },
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class PasswordResetRequestView(APIView):
    """POST /api/password-reset/request/ — 忘記密碼第一步：寄驗證碼到信箱。

    **一律回同一句話**，不論這個信箱有沒有註冊、帳號有沒有停用、寄信有沒有
    成功。這個端點免登入，回應只要因帳號存在與否而不同，就是一個帳號列舉
    工具。真的寄出去的條件（帳號存在、is_active、有 email）都在內部判斷，
    對外不可見。

    研究者代開的舊帳號多半沒有 email，走不到這條流程是預期行為——他們的
    密碼由研究者在後台重設。

    限流 scope password_reset_request 掛在這裡：未認證請求依 IP 計數，狂點
    只會灌爆某個信箱、燒掉 Gmail 每日寄信配額。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_request"

    GENERIC_OK = "如果這個信箱有註冊帳號，我們已經寄出一組驗證碼，請查看信箱。"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None and user.email:
            record, code = PasswordResetCode.issue(user)
            try:
                send_password_reset_code(user.email, code)
            except Exception:
                # 寄信失敗就把剛產的碼作廢：不要在 DB 裡留一組寄不出去、
                # 卻仍然有效的憑證。錯誤只進 log，不對外洩漏。
                record.consumed_at = timezone.now()
                record.save(update_fields=["consumed_at"])
                logger.exception(
                    "password reset mail failed for user id=%s", user.id
                )

        return Response({"detail": self.GENERIC_OK})


class PasswordResetConfirmView(APIView):
    """POST /api/password-reset/confirm/ — 忘記密碼第二步：驗碼 + 設新密碼。

    驗證碼正確才換密碼，接著：
    - 把該碼標記為已用（單次使用）；
    - 作廢該帳號所有既有 refresh token（密碼可能已外流，舊 session 不能留）；
    - 順手補上 email_verified_at——能收到寄到這個信箱的驗證碼，等於證明了
      信箱是本人的。

    驗證碼錯誤 / 過期 / 查無此信箱一律回同一句 GENERIC_ERROR，一樣是為了
    不讓端點變成帳號列舉或驗證碼 oracle。密碼強度不足（400，欄位 new_password）
    是唯一會逐項回報的錯誤——那是使用者需要知道怎麼改的。

    成功後不自動登入：使用者拿新密碼回登入頁即可，這裡不發 token。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    GENERIC_ERROR = "驗證碼不正確或已失效，請重新索取。"

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        user = (
            User.objects.filter(email__iexact=email, is_active=True).first()
            if email
            else None
        )

        serializer = PasswordResetConfirmSerializer(
            data=request.data, context={"target": user}
        )
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]

        record = None
        if user is not None:
            record = (
                PasswordResetCode.objects.filter(
                    user=user, consumed_at__isnull=True
                )
                .order_by("-created_at")
                .first()
            )

        if record is None or not record.is_usable():
            return Response(
                {"detail": self.GENERIC_ERROR},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not secrets.compare_digest(
            record.code_hash, PasswordResetCode.hash_code(code)
        ):
            record.attempt_count += 1
            record.save(update_fields=["attempt_count"])
            return Response(
                {"detail": self.GENERIC_ERROR},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        record.consumed_at = now
        record.save(update_fields=["consumed_at"])

        user.set_password(serializer.validated_data["new_password"])
        update_fields = ["password"]
        if user.email_verified_at is None:
            user.email_verified_at = now
            update_fields.append("email_verified_at")
        user.save(update_fields=update_fields)
        revoke_user_tokens(user)

        return Response({"detail": "密碼已更新，請用新密碼登入。"})
