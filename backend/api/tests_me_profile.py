"""一般使用者的個人資料（GET/PATCH /api/me/）。

只能改自己的：pk 不從 URL 來，一律用 request.user。username 刻意不可改——
它是登入帳號，也是研究資料的對應鍵；要改名請用 display_name。

display_name 的可見範圍邊界（不進對話室、不進 Godot）由
accounts/tests_display_name_boundary.py 釘住，這裡不重複。
"""

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

PASSWORD = "Xk9$mVpq2Lz"
URL = "/api/me/"


class MeProfileReadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="participant",
            password=PASSWORD,
            email="me@example.com",
            display_name="小明",
        )
        self.client.force_authenticate(user=self.user)

    def test_get_returns_own_email_and_display_name(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "me@example.com")
        self.assertEqual(response.data["display_name"], "小明")

    def test_get_returns_empty_email_as_blank_not_null(self):
        # 研究者代開的帳號沒有 email（DB 存 NULL）。前端表單要拿它當
        # <input value>，回 null 會讓 React 把 input 變成 uncontrolled。
        self.user.email = None
        self.user.save(update_fields=["email"])
        response = self.client.get(URL)
        self.assertEqual(response.data["email"], "")


class MeProfileUpdateTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="participant",
            password=PASSWORD,
            email="me@example.com",
            display_name="小明",
        )
        self.client.force_authenticate(user=self.user)

    def test_updates_display_name(self):
        response = self.client.patch(URL, {"display_name": "小華"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "小華")
        self.assertEqual(response.data["display_name"], "小華")

    def test_display_name_can_be_cleared(self):
        # 顯示名稱是選填的，使用者要收回「讓別人看到我的名字」這件事應該做得到。
        response = self.client.patch(URL, {"display_name": ""}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "")

    def test_rejects_display_name_over_50_chars(self):
        response = self.client.patch(URL, {"display_name": "字" * 51}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("display_name", response.data)

    def test_updates_email(self):
        response = self.client.patch(URL, {"email": "new@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

    def test_researcher_created_account_can_add_missing_email(self):
        # 研究者代開的帳號一律沒有 email，補得上去才走得通未來的信件重設流程。
        self.user.email = None
        self.user.save(update_fields=["email"])
        response = self.client.patch(URL, {"email": "added@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "added@example.com")

    def test_rejects_email_taken_by_another_user_case_insensitively(self):
        User.objects.create_user(
            username="other", password=PASSWORD, email="taken@example.com"
        )
        response = self.client.patch(URL, {"email": "TAKEN@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "me@example.com")

    def test_resubmitting_own_email_is_not_a_duplicate(self):
        # 使用者只改了顯示名稱、email 欄位原樣送回來，不能被自己的 email 擋下。
        response = self.client.patch(
            URL, {"email": "me@example.com", "display_name": "小華"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_rejects_blank_email(self):
        # 清空 email 等於把自己未來的信件重設管道關掉，而且使用者多半是誤刪。
        response = self.client.patch(URL, {"email": ""}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "me@example.com")

    def test_changing_email_clears_verification_timestamp(self):
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email_verified_at"])
        self.client.patch(URL, {"email": "new@example.com"}, format="json")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)

    def test_ignores_attempts_to_change_username_or_role(self):
        response = self.client.patch(
            URL,
            {
                "username": "hacker",
                "is_researcher": True,
                "is_research_subject": False,
                "is_staff": True,
                "display_name": "小華",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "participant")
        self.assertFalse(self.user.is_staff)
        self.assertFalse(response.data["is_researcher"])
        # 有效欄位仍然照常套用。
        self.assertEqual(self.user.display_name, "小華")

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.patch(URL, {"display_name": "小華"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
