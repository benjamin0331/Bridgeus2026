"""display_name 的可見範圍邊界。

對話室與 Godot 大廳刻意不顯示真實身份（見 api/views.py 的
ANONYMOUS_MATCH_USER_NAME 與 GodotTicketRedeemView 的 docstring）。
display_name 是新欄位，很容易被順手序列化出去，這裡把邊界釘死。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.godot_tickets import issue_ticket

User = get_user_model()

GODOT_TOKEN = "test-godot-service-token-value"


class GodotBoundaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="subject1",
            password="Xk9$mVpq2Lz",
            display_name="王小明",
        )

    @override_settings(GODOT_SERVICE_TOKEN=GODOT_TOKEN)
    def test_ticket_redeem_returns_only_user_id(self):
        ticket = issue_ticket(user=self.user)

        response = self.client.post(
            "/api/godot/tickets/redeem/",
            {"ticket": ticket.token},
            format="json",
            HTTP_X_GODOT_SERVICE_TOKEN=GODOT_TOKEN,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data.keys()), {"user_id"})
        self.assertNotIn("王小明", str(response.data))


class SerializerBoundaryTests(TestCase):
    def test_match_message_serializer_has_no_display_name(self):
        """對話訊息的送出者名稱一律匿名化，不得帶出 display_name。"""
        from api.serializers import MatchMessageSerializer

        self.assertNotIn("display_name", MatchMessageSerializer().fields)

    def test_account_list_serializer_has_no_display_name(self):
        """帳號管理清單目前不顯示 display_name。若日後要加，請一併更新本測試
        與前端設定頁，而不是預設讓它跟著跑出去。
        """
        from api.serializers import AccountListSerializer

        self.assertNotIn("display_name", AccountListSerializer().fields)
