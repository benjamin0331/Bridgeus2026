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
from .emails import send_email_verification_code, send_password_reset_code
from .models import EmailVerificationCode, PasswordResetCode, User
from .serializers import (
    EmailVerificationConfirmSerializer,
    EmailVerificationRequestSerializer,
    MeEmailVerificationConfirmSerializer,
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

    信箱查無帳號時直接回 `404` 明講「這個信箱沒有註冊」，不做「不論存不存在
    都回同一句」的防帳號列舉處理——這是產品刻意的取捨（封閉的研究平台、註冊頁
    本來就會回報 email 已被使用），換取使用者打錯字時看得懂。

    寄出的條件是帳號存在、`is_active`、有 email；三者有一個不成立就當成
    「沒有註冊」。研究者代開的舊帳號多半沒有 email，走不到這條流程是預期
    行為——他們的密碼由研究者在後台重設。

    限流 scope password_reset_request 掛在這裡：未認證請求依 IP 計數，狂點
    只會灌爆某個信箱、燒掉 Gmail 每日寄信配額。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_request"

    NOT_REGISTERED = "這個信箱沒有註冊帳號。"
    MAIL_FAILED = "驗證碼寄送失敗，請稍後再試。"
    SENT = "驗證碼已寄出。"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None or not user.email:
            return Response(
                {"detail": self.NOT_REGISTERED},
                status=status.HTTP_404_NOT_FOUND,
            )

        record, code = PasswordResetCode.issue(user)
        try:
            send_password_reset_code(user.email, code)
        except Exception:
            # 寄信失敗就把剛產的碼作廢：不要在 DB 裡留一組寄不出去、卻仍然
            # 有效的憑證。
            record.consumed_at = timezone.now()
            record.save(update_fields=["consumed_at"])
            logger.exception(
                "password reset mail failed for user id=%s", user.id
            )
            return Response(
                {"detail": self.MAIL_FAILED},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"detail": self.SENT})


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


EMAIL_CODE_GENERIC_ERROR = "驗證碼不正確或已失效，請重新索取。"


def _verify_code(record, code, *, on_success):
    """驗證碼比對的共用尾段：查無可用紀錄或比錯 → 回 400 通用錯誤並累加
    attempt_count；比中 → 執行 on_success()（呼叫端負責落庫）並回它的 Response。
    """
    if record is None or not record.is_usable():
        return Response(
            {"detail": EMAIL_CODE_GENERIC_ERROR},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not secrets.compare_digest(
        record.code_hash, EmailVerificationCode.hash_code(code)
    ):
        record.attempt_count += 1
        record.save(update_fields=["attempt_count"])
        return Response(
            {"detail": EMAIL_CODE_GENERIC_ERROR},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return on_success()


class EmailVerificationRequestView(APIView):
    """POST /api/email-verification/request/ — 註冊前，寄信箱驗證碼。

    未登入端點：帳號還不存在，用 email 當鍵。這個 email 若已經有人註冊，回
    400（與註冊頁一致地回報 email 已被使用）。限流 scope
    email_verification_request 依 IP 計數，量級對齊 register（同場地集體註冊
    共用 NAT 出口 IP）。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_verification_request"

    def post(self, request):
        serializer = EmailVerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        if User.objects.filter(email__iexact=email).exists():
            return Response(
                {"email": ["這個 email 已經註冊過了。"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record, code = EmailVerificationCode.issue(email, user=None)
        try:
            send_email_verification_code(email, code)
        except Exception:
            record.consumed_at = timezone.now()
            record.save(update_fields=["consumed_at"])
            logger.exception("email verification mail failed for %s", email)
            return Response(
                {"detail": "驗證碼寄送失敗，請稍後再試。"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"detail": "驗證碼已寄出。"})


class EmailVerificationConfirmView(APIView):
    """POST /api/email-verification/confirm/ — 註冊前，驗信箱驗證碼。

    比中就把該紀錄標記 verified_at + consumed_at；註冊 serializer 之後會讀
    verified_at 判斷「這個 email 驗過了」，並在成功建帳號時把紀錄刪掉。
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_verification_confirm"

    def post(self, request):
        serializer = EmailVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        code = serializer.validated_data["code"]

        record = (
            EmailVerificationCode.objects.filter(
                email__iexact=email, user__isnull=True, consumed_at__isnull=True
            )
            .order_by("-created_at")
            .first()
        )

        def _ok():
            now = timezone.now()
            record.verified_at = now
            record.consumed_at = now
            record.save(update_fields=["verified_at", "consumed_at"])
            return Response({"detail": "信箱驗證成功。"})

        return _verify_code(record, code, on_success=_ok)


class MeEmailVerificationRequestView(APIView):
    """POST /api/me/email/verify/request/ — 登入後，寄驗證碼到自己目前的信箱。

    給「補上／換了信箱、還沒驗證」的使用者用。已認證請求，限流依 user id
    計數。
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_verification_request"

    def post(self, request):
        user = request.user
        if not user.email:
            return Response(
                {"detail": "尚未設定信箱。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.email_verified_at is not None:
            return Response(
                {"detail": "信箱已驗證。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record, code = EmailVerificationCode.issue(user.email, user=user)
        try:
            send_email_verification_code(user.email, code)
        except Exception:
            record.consumed_at = timezone.now()
            record.save(update_fields=["consumed_at"])
            logger.exception(
                "email verification mail failed for user id=%s", user.id
            )
            return Response(
                {"detail": "驗證碼寄送失敗，請稍後再試。"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"detail": "驗證碼已寄出。"})


class MeEmailVerificationConfirmView(APIView):
    """POST /api/me/email/verify/confirm/ — 登入後，用驗證碼確認自己的信箱。"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_verification_confirm"

    def post(self, request):
        serializer = MeEmailVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        user = request.user

        if not user.email or user.email_verified_at is not None:
            # 沒有信箱、或已經驗過——沒有東西可驗。
            return Response(
                {"detail": EMAIL_CODE_GENERIC_ERROR},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = (
            EmailVerificationCode.objects.filter(
                user=user, email__iexact=user.email, consumed_at__isnull=True
            )
            .order_by("-created_at")
            .first()
        )

        def _ok():
            now = timezone.now()
            record.verified_at = now
            record.consumed_at = now
            record.save(update_fields=["verified_at", "consumed_at"])
            user.email_verified_at = now
            user.save(update_fields=["email_verified_at"])
            return Response({"detail": "信箱驗證成功。"})

        return _verify_code(record, code, on_success=_ok)
