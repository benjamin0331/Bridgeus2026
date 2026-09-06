"""Tests for the M6 觀點知識庫公開瀏覽 API：
KnowledgeBaseHighlightsView / KnowledgeBaseConversationDetailView /
KnowledgeBaseViewpointBrowseView / VideoRecommendationListView.

跟 tests_viewpoint_review.py（研究者專用審核 API）分開：這裡驗證的是任何
登入使用者都能看的公開端點，只看得到 review_status=approved 的節點。
"""

import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import AIConversation, DialogueMatch, MatchMessage
from api.permissions import RESEARCHER_GROUP_NAME
from apps.summary.models import DialogueSummary, VideoRecommendation, ViewpointNode

User = get_user_model()


def _make_match(topic_id=102, room_id=None):
    """對話詳情頁會用 summary.dialogue_id 反查真正的 DialogueMatch（要拿逐字稿
    跟 CCND 樹），所以每個 summary 背後都要有一場實際存在的配對房。"""
    suffix = room_id or f"kb-{DialogueMatch.objects.count() + 1}"
    user_a = User.objects.create_user(username=f"kb_a_{suffix}", password="pw")
    user_b = User.objects.create_user(username=f"kb_b_{suffix}", password="pw")
    return DialogueMatch.objects.create(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        user_a_score=6.0,
        user_b_score=2.0,
        room_id=f"room-{suffix}",
    )


def _make_viewpoint(
    *,
    topic_id=102,
    dimension="anchor_safety",
    speaker_side="a",
    composite_score=0.5,
    citation_count=0,
    review_status=ViewpointNode.ReviewStatus.APPROVED,
    dialogue_id=None,
):
    if dialogue_id is None:
        dialogue_id = str(_make_match(topic_id=topic_id).id)
    summary = DialogueSummary.objects.create(dialogue_id=dialogue_id, topic_id=topic_id)
    return ViewpointNode.objects.create(
        summary=summary,
        topic_id=topic_id,
        dimension=dimension,
        speaker_side=speaker_side,
        stance_direction="pro",
        user_input_text="核能在低碳排放這個面向確實有其優勢，但安全疑慮不能忽視",
        ai_response_text="您提到了核能的兩個核心矛盾",
        viewpoint_summary="核能安全、經濟成本",
        composite_score=composite_score,
        citation_count=citation_count,
        review_status=review_status,
    )


class KnowledgeBaseHighlightsTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_anonymous_request_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/summary/viewpoints/highlights/")
        self.assertIn(
            response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        )

    def test_only_approved_viewpoints_are_returned(self):
        approved = _make_viewpoint(citation_count=3)
        _make_viewpoint(review_status=ViewpointNode.ReviewStatus.PENDING)
        _make_viewpoint(review_status=ViewpointNode.ReviewStatus.REJECTED)

        response = self.client.get(
            "/api/summary/viewpoints/highlights/", {"topic_id": 102}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], approved.id)

    def test_exposes_approved_transcript_but_not_internal_review_fields(self):
        """已核准節點的逐字稿（user_input_text/ai_response_text）是刻意公開的
        ——對話詳情頁本來就會顯示完整逐字稿，卡片只是延伸同一個範圍。但審核
        用的內部欄位（評分細項、審核狀態、審核者、審核備註）不能外流。"""
        _make_viewpoint()

        response = self.client.get(
            "/api/summary/viewpoints/highlights/", {"topic_id": 102}
        )

        row = response.data[0]
        self.assertIn("user_input_text", row)
        self.assertIn("ai_response_text", row)
        self.assertIn("dimension_name", row)
        self.assertIn("speaker_side", row)
        self.assertIn("dialogue_summary_id", row)
        for internal_field in (
            "score_detail",
            "review_status",
            "review_notes",
            "reviewed_by_username",
            "reviewed_at",
            "embedding",
        ):
            self.assertNotIn(internal_field, row)

    def test_ordered_by_citation_count_then_score(self):
        low = _make_viewpoint(citation_count=1, composite_score=0.9)
        high = _make_viewpoint(citation_count=5, composite_score=0.1)

        response = self.client.get(
            "/api/summary/viewpoints/highlights/", {"topic_id": 102}
        )

        ids = [row["id"] for row in response.data]
        self.assertEqual(ids, [high.id, low.id])

    def test_limit_is_clamped(self):
        for i in range(3):
            _make_viewpoint(dialogue_id=str(i))

        response = self.client.get(
            "/api/summary/viewpoints/highlights/",
            {"topic_id": 102, "limit": 999},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)

    def test_invalid_topic_id_is_a_400_not_a_500(self):
        response = self.client.get(
            "/api/summary/viewpoints/highlights/", {"topic_id": "not-a-number"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_topic_id_is_optional(self):
        _make_viewpoint(topic_id=102)
        _make_viewpoint(topic_id=103, dimension="anchor_equality")

        response = self.client.get("/api/summary/viewpoints/highlights/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)


class KnowledgeBaseConversationDetailTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant2", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_returns_summary_and_sibling_viewpoints(self):
        node = _make_viewpoint(citation_count=2)
        sibling = ViewpointNode.objects.create(
            summary=node.summary,
            topic_id=102,
            dimension="anchor_safety",
            speaker_side="b",
            user_input_text="另一則同場對話的發言",
            composite_score=0.3,
            review_status=ViewpointNode.ReviewStatus.APPROVED,
        )

        response = self.client.get(f"/api/summary/viewpoints/{node.id}/conversation/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["dialogue_summary_id"], node.summary_id)
        returned_ids = {row["id"] for row in response.data["viewpoints"]}
        self.assertEqual(returned_ids, {node.id, sibling.id})

    def test_returns_transcript_and_semantic_tree(self):
        node = _make_viewpoint()
        match = DialogueMatch.objects.get(pk=int(node.summary.dialogue_id))
        MatchMessage.objects.create(
            match=match, sender=match.user_a, content="A 方的發言內容"
        )
        MatchMessage.objects.create(
            match=match, sender=match.user_b, content="B 方的回應內容"
        )

        response = self.client.get(f"/api/summary/viewpoints/{node.id}/conversation/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        messages = response.data["messages"]
        self.assertEqual([m["side"] for m in messages], ["a", "b"])
        self.assertEqual([m["sender_label"] for m in messages], ["A方", "B方"])
        # 逐字稿不得帶出發言者身分，只有 A/B 方標示。
        for message in messages:
            self.assertNotIn("sender_id", message)
            self.assertNotIn("sender_name", message)
        # 雙方的 CCND 樹都要在，前端才能切換顯示。
        self.assertEqual(len(response.data["semantic_tree"]["trees"]), 2)
        self.assertEqual(
            [tree["label"] for tree in response.data["semantic_tree"]["trees"]],
            ["A方", "B方"],
        )

    def test_missing_match_record_is_a_404_not_a_500(self):
        # dialogue_id 指向一場不存在的配對房（例如手動塞的舊資料）。
        node = _make_viewpoint(dialogue_id="999999")

        response = self.client.get(f"/api/summary/viewpoints/{node.id}/conversation/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_pending_node_is_not_found(self):
        node = _make_viewpoint(review_status=ViewpointNode.ReviewStatus.PENDING)

        response = self.client.get(f"/api/summary/viewpoints/{node.id}/conversation/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_id_is_not_found(self):
        response = self.client.get("/api/summary/viewpoints/999999/conversation/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class KnowledgeBaseViewpointBrowseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant3", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_topic_id_is_required(self):
        response = self.client.get("/api/summary/viewpoints/browse/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_topic_id_is_a_400_not_a_500(self):
        response = self.client.get(
            "/api/summary/viewpoints/browse/", {"topic_id": "abc"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_paginated_approved_viewpoints(self):
        for i in range(3):
            _make_viewpoint(dialogue_id=str(i))
        _make_viewpoint(review_status=ViewpointNode.ReviewStatus.PENDING)

        response = self.client.get(
            "/api/summary/viewpoints/browse/", {"topic_id": 102}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(len(response.data["results"]), 3)

    def test_only_returns_requested_topic(self):
        _make_viewpoint(topic_id=102)
        _make_viewpoint(topic_id=103, dimension="anchor_equality")

        response = self.client.get(
            "/api/summary/viewpoints/browse/", {"topic_id": 103}
        )

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["topic_id"], 103)


class VideoRecommendationListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant4", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_only_published_videos_are_returned(self):
        VideoRecommendation.objects.create(
            title="已發布", url="https://example.com/a", is_published=True
        )
        VideoRecommendation.objects.create(
            title="未發布", url="https://example.com/b", is_published=False
        )

        response = self.client.get("/api/summary/videos/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "已發布")

    def test_filters_by_topic_id(self):
        VideoRecommendation.objects.create(
            title="核能影片", url="https://example.com/a", topic_id=102
        )
        VideoRecommendation.objects.create(
            title="兵役影片", url="https://example.com/b", topic_id=103
        )

        response = self.client.get("/api/summary/videos/", {"topic_id": 102})

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "核能影片")

    def test_invalid_topic_id_is_a_400_not_a_500(self):
        response = self.client.get("/api/summary/videos/", {"topic_id": "abc"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class DialogueTopicTrendingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant5", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_anonymous_request_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/dialogue/topics/trending/")
        self.assertIn(
            response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        )

    def test_sorted_by_hits_descending(self):
        for _ in range(3):
            AIConversation.objects.create(topic_id=103, user_prompt="x")
        AIConversation.objects.create(topic_id=102, user_prompt="x")

        response = self.client.get("/api/dialogue/topics/trending/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {row["id"]: row["hits"] for row in response.data}
        self.assertEqual(by_id[103], 3)
        self.assertEqual(by_id[102], 1)
        ids_in_order = [row["id"] for row in response.data]
        self.assertEqual(ids_in_order.index(103), 0)

    def test_includes_topics_with_zero_hits(self):
        response = self.client.get("/api/dialogue/topics/trending/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(row["hits"] == 0 for row in response.data))
        self.assertGreater(len(response.data), 0)


# 上傳測試會真的把檔案寫進 MEDIA_ROOT；導到暫存目錄，不要在 backend/media/
# 底下留下測試殘骸。
@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="bridgeus-test-media-"))
class VideoRecommendationAdminTests(APITestCase):
    """研究者專用的影片管理面板（前端設定頁「影片管理」分頁）。"""

    def setUp(self):
        group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
        self.researcher = User.objects.create_user(username="kb_researcher", password="pw")
        self.researcher.groups.add(group)
        self.participant = User.objects.create_user(username="kb_participant", password="pw")

    def _upload(self, name="clip.mp4", title="測試影片"):
        return self.client.post(
            "/api/summary/videos/admin/",
            {
                "title": title,
                "video_file": SimpleUploadedFile(name, b"fake-video-bytes", "video/mp4"),
            },
            format="multipart",
        )

    def test_participant_cannot_list_or_upload(self):
        self.client.force_authenticate(user=self.participant)
        self.assertEqual(
            self.client.get("/api/summary/videos/admin/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self._upload().status_code, status.HTTP_403_FORBIDDEN)

    def test_upload_fills_url_from_the_stored_file(self):
        self.client.force_authenticate(user=self.researcher)

        response = self._upload()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        video = VideoRecommendation.objects.get(pk=response.data["id"])
        # url 沒帶要自動補成該檔案的「根相對路徑」，下游只認 url。
        # 這裡刻意不是絕對網址：絕對網址會把上傳當下的 host/scheme 寫死進 DB
        # （見 api.views._fill_video_url_from_file 的說明）。
        self.assertTrue(video.url.startswith("/media/"), video.url)
        self.assertIn("kb_videos/", video.url)

    def test_upload_without_file_or_url_is_rejected(self):
        self.client.force_authenticate(user=self.researcher)

        response = self.client.post(
            "/api/summary/videos/admin/", {"title": "沒有來源"}, format="multipart"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_external_url_without_a_file_is_accepted(self):
        self.client.force_authenticate(user=self.researcher)

        response = self.client.post(
            "/api/summary/videos/admin/",
            {"title": "外部連結", "url": "https://example.com/v"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_list_includes_unpublished_but_public_list_does_not(self):
        VideoRecommendation.objects.create(
            title="未發布", url="https://example.com/hidden", is_published=False
        )

        self.client.force_authenticate(user=self.researcher)
        admin_titles = [row["title"] for row in self.client.get("/api/summary/videos/admin/").data]
        self.assertIn("未發布", admin_titles)

        self.client.force_authenticate(user=self.participant)
        public_titles = [row["title"] for row in self.client.get("/api/summary/videos/").data]
        self.assertNotIn("未發布", public_titles)

    def test_researcher_can_publish_and_delete(self):
        video = VideoRecommendation.objects.create(
            title="待發布", url="https://example.com/v", is_published=False
        )
        self.client.force_authenticate(user=self.researcher)

        patch = self.client.patch(
            f"/api/summary/videos/admin/{video.id}/", {"is_published": True}, format="json"
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        video.refresh_from_db()
        self.assertTrue(video.is_published)

        delete = self.client.delete(f"/api/summary/videos/admin/{video.id}/")
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(VideoRecommendation.objects.filter(pk=video.id).exists())
