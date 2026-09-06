"""自助註冊端點。

與 api/views.py 的 AccountListCreateView（研究者代開帳號）平行存在。
放在 accounts app 而不是 api：後者的 views.py 已經 4400 行，而 accounts
本來就擁有 User model。
"""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.serializers import BridgeUsTokenObtainPairSerializer

from .consent import CONSENT_DOCUMENT, CONSENT_VERSION
from .serializers import RegistrationSerializer


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
