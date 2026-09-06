"""研究者上傳知識庫影片檔（/api/summary/videos/admin/）的檔案處理。

跟 tests_video_recommendation.py 分開：那邊測的是「已經有影片之後怎麼排序推薦」，
這裡測的是「檔案怎麼進來、存去哪、換掉或刪掉時怎麼收拾」。每個測試都把
MEDIA_ROOT 換成暫存目錄，避免把測試檔案寫進開發者的 backend/media/。
"""

import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from api.permissions import RESEARCHER_GROUP_NAME
from apps.summary.models import VideoRecommendation

User = get_user_model()

ADMIN_URL = "/api/summary/videos/admin/"


def _mp4(name="clip.mp4", size=1024):
    """最小可用的 mp4：開頭給真的 ftyp box，後面用零填到指定大小。

    內容驗證只看得到前幾個 byte，size 用來測大小上限，兩者互不干擾。
    """
    header = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41"
    body = header + b"\x00" * max(0, size - len(header))
    return SimpleUploadedFile(name, body, content_type="video/mp4")


class VideoUploadTestCase(APITestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="bridgeus-media-")
        self.addCleanup(shutil.rmtree, self.media_root, True)

        self.researcher = User.objects.create_user(username="researcher", password="x")
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher.groups.add(group)
        self.client.force_authenticate(self.researcher)


class VideoUploadStorageTests(VideoUploadTestCase):
    def test_upload_stores_file_under_kb_videos_and_saves_relative_url(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                ADMIN_URL,
                {"title": "測試影片", "video_file": _mp4()},
                format="multipart",
            )

        self.assertEqual(response.status_code, 201, response.data)
        video = VideoRecommendation.objects.get()
        self.assertTrue(video.video_file.name.startswith("kb_videos/"))
        self.assertTrue(Path(self.media_root, video.video_file.name).exists())
        # 根相對路徑，不能是 http(s):// 開頭的絕對網址（見 _fill_video_url_from_file）。
        self.assertTrue(video.url.startswith("/media/kb_videos/"), video.url)

    def test_replacing_the_file_points_the_url_at_the_new_file(self):
        """重新上傳換檔之後 url 還指著舊檔，等於前台永遠播不到新影片。"""
        with override_settings(MEDIA_ROOT=self.media_root):
            created = self.client.post(
                ADMIN_URL,
                {"title": "測試影片", "video_file": _mp4("first.mp4")},
                format="multipart",
            )
            self.assertEqual(created.status_code, 201, created.data)
            video_id = created.data["id"]
            old_url = VideoRecommendation.objects.get(pk=video_id).url

            updated = self.client.patch(
                f"{ADMIN_URL}{video_id}/",
                {"video_file": _mp4("second.mp4")},
                format="multipart",
            )

        self.assertEqual(updated.status_code, 200, updated.data)
        video = VideoRecommendation.objects.get(pk=video_id)
        self.assertNotEqual(video.url, old_url)
        self.assertIn("second", video.url)
        self.assertEqual(video.url, f"/media/{video.video_file.name}")

    def test_replacing_the_file_removes_the_previous_one_from_disk(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            created = self.client.post(
                ADMIN_URL,
                {"title": "測試影片", "video_file": _mp4("first.mp4")},
                format="multipart",
            )
            video_id = created.data["id"]
            old_name = VideoRecommendation.objects.get(pk=video_id).video_file.name

            self.client.patch(
                f"{ADMIN_URL}{video_id}/",
                {"video_file": _mp4("second.mp4")},
                format="multipart",
            )

        self.assertFalse(Path(self.media_root, old_name).exists())

    def test_deleting_the_record_removes_the_file_from_disk(self):
        """DB 列刪掉、檔案留在磁碟 = 沒人知道還在佔空間的孤兒檔。"""
        with override_settings(MEDIA_ROOT=self.media_root):
            created = self.client.post(
                ADMIN_URL,
                {"title": "測試影片", "video_file": _mp4()},
                format="multipart",
            )
            video_id = created.data["id"]
            name = VideoRecommendation.objects.get(pk=video_id).video_file.name
            self.assertTrue(Path(self.media_root, name).exists())

            deleted = self.client.delete(f"{ADMIN_URL}{video_id}/")

        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Path(self.media_root, name).exists())


class VideoUploadValidationTests(VideoUploadTestCase):
    def test_file_over_the_size_limit_is_rejected_with_a_readable_message(self):
        with override_settings(MEDIA_ROOT=self.media_root, KB_VIDEO_MAX_BYTES=2048):
            response = self.client.post(
                ADMIN_URL,
                {"title": "太大", "video_file": _mp4(size=4096)},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("video_file", response.data)
        # 訊息要講得出上限是多少，研究者才知道該壓到多小。
        self.assertRegex(str(response.data["video_file"][0]), r"\d")
        self.assertFalse(VideoRecommendation.objects.exists())

    def test_file_within_the_size_limit_is_accepted(self):
        with override_settings(MEDIA_ROOT=self.media_root, KB_VIDEO_MAX_BYTES=8192):
            response = self.client.post(
                ADMIN_URL,
                {"title": "剛好", "video_file": _mp4(size=4096)},
                format="multipart",
            )

        self.assertEqual(response.status_code, 201, response.data)

    def test_non_video_extension_is_rejected(self):
        payload = SimpleUploadedFile(
            "payload.html", b"<script>alert(1)</script>", content_type="text/html"
        )
        with override_settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                ADMIN_URL,
                {"title": "不是影片", "video_file": payload},
                format="multipart",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("video_file", response.data)
        self.assertFalse(VideoRecommendation.objects.exists())

    def test_extension_check_is_case_insensitive(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            response = self.client.post(
                ADMIN_URL,
                {"title": "大寫副檔名", "video_file": _mp4("CLIP.MP4")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 201, response.data)


class VideoUrlFieldTests(VideoUploadTestCase):
    def test_admin_form_accepts_a_record_whose_url_is_a_relative_path(self):
        """url 從 build_absolute_uri 改存根相對路徑後，欄位型別必須跟著改。

        URLField 的 URLValidator 會擋掉 /media/... ——研究者從 Django admin
        編輯任何一筆已上傳的影片都會被擋，即使他根本沒動 url 欄位。
        """
        from django.contrib.admin.sites import AdminSite

        from apps.summary.admin import VideoRecommendationAdmin

        video = VideoRecommendation.objects.create(
            title="已上傳", url="/media/kb_videos/2026/09/clip.mp4"
        )
        model_admin = VideoRecommendationAdmin(VideoRecommendation, AdminSite())
        form_class = model_admin.get_form(None, obj=video)
        form = form_class(
            instance=video,
            data={
                "title": video.title,
                "url": video.url,
                "thumbnail_url": "",
                "description": "",
                "stance_direction": VideoRecommendation.StanceDirection.NEUTRAL,
                "display_order": 0,
                "is_published": "on",
            },
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
