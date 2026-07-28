"""Tests for the M6 觀點知識庫公開瀏覽 API：
KnowledgeBaseHighlightsView / KnowledgeBaseConversationDetailView /
KnowledgeBaseViewpointBrowseView / VideoRecommendationListView.

跟 tests_viewpoint_review.py（研究者專用審核 API）分開：這裡驗證的是任何
登入使用者都能看的公開端點，只看得到 review_status=approved 的節點。
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import AIConversation
from apps.summary.models import DialogueSummary, VideoRecommendation, ViewpointNode

User = get_user_model()


def _make_viewpoint(
    *,
    topic_id=102,
    dimension="anchor_safety",
    speaker_side="a",
    composite_score=0.5,
    citation_count=0,
    review_status=ViewpointNode.ReviewStatus.APPROVED,
    dialogue_id="9001",
):
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

    def test_does_not_expose_raw_transcript_fields(self):
        _make_viewpoint()

        response = self.client.get(
            "/api/summary/viewpoints/highlights/", {"topic_id": 102}
        )

        row = response.data[0]
        self.assertNotIn("user_input_text", row)
        self.assertNotIn("ai_response_text", row)
        self.assertNotIn("score_detail", row)
        self.assertIn("dimension_name", row)
        self.assertIn("speaker_side", row)

    def test_ordered_by_citation_count_then_score(self):
        low = _make_viewpoint(dialogue_id="a", citation_count=1, composite_score=0.9)
        high = _make_viewpoint(dialogue_id="b", citation_count=5, composite_score=0.1)

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
        self.assertNotIn("user_input_text", response.data["viewpoints"][0])

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
        _make_viewpoint(dialogue_id="pending", review_status=ViewpointNode.ReviewStatus.PENDING)

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
