"""Tests for the 觀點知識庫收藏（星星）API：FavoriteView。

重點在兩件事：
  1. 切換語意——同一個目標再按一次是取消，不是疊加。
  2. 可見性 gate——只有 approved 的觀點與 is_published 的影片能被收藏、被列出。
     這道 gate 在寫入與讀取兩邊都要成立，見 FavoriteView 的 docstring。
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import DialogueMatch, Favorite
from apps.summary.models import DialogueSummary, VideoRecommendation, ViewpointNode

User = get_user_model()

FAVORITES_URL = "/api/favorites/"


def _make_viewpoint(*, topic_id=102, review_status=ViewpointNode.ReviewStatus.APPROVED):
    suffix = ViewpointNode.objects.count() + 1
    user_a = User.objects.create_user(username=f"fav_a_{suffix}", password="pw")
    user_b = User.objects.create_user(username=f"fav_b_{suffix}", password="pw")
    match = DialogueMatch.objects.create(
        topic_id=topic_id,
        user_a=user_a,
        user_b=user_b,
        room_id=f"fav-room-{suffix}",
    )
    summary = DialogueSummary.objects.create(
        dialogue_id=str(match.id), topic_id=topic_id
    )
    return ViewpointNode.objects.create(
        summary=summary,
        topic_id=topic_id,
        dimension="anchor_safety",
        speaker_side="a",
        stance_direction="pro",
        user_input_text="核能在低碳排放這個面向確實有其優勢",
        ai_response_text="您提到了核能的兩個核心矛盾",
        viewpoint_summary="核能安全、經濟成本",
        composite_score=0.5,
        citation_count=3,
        review_status=review_status,
    )


def _make_video(*, is_published=True, title="核能入門"):
    return VideoRecommendation.objects.create(
        title=title,
        url="https://example.com/v",
        thumbnail_url="https://example.com/t.jpg",
        description="說明",
        topic_id=102,
        is_published=is_published,
    )


class FavoriteToggleTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_anonymous_request_is_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(FAVORITES_URL)
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_toggle_adds_then_removes(self):
        viewpoint = _make_viewpoint()

        first = self.client.post(
            FAVORITES_URL, {"target_type": "viewpoint", "target_id": viewpoint.id}
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["favorited"])
        self.assertEqual(Favorite.objects.filter(user=self.user).count(), 1)

        second = self.client.post(
            FAVORITES_URL, {"target_type": "viewpoint", "target_id": viewpoint.id}
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(second.data["favorited"])
        self.assertEqual(Favorite.objects.filter(user=self.user).count(), 0)

    def test_cannot_favorite_unapproved_viewpoint(self):
        """靠猜 id 收藏還沒過審的候選觀點會變成一個「這筆存不存在」的探測器。"""
        pending = _make_viewpoint(review_status=ViewpointNode.ReviewStatus.PENDING)

        response = self.client.post(
            FAVORITES_URL, {"target_type": "viewpoint", "target_id": pending.id}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Favorite.objects.count(), 0)

    def test_cannot_favorite_unpublished_video(self):
        video = _make_video(is_published=False)

        response = self.client.post(
            FAVORITES_URL, {"target_type": "video", "target_id": video.id}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Favorite.objects.count(), 0)

    def test_missing_target_returns_404(self):
        response = self.client.post(
            FAVORITES_URL, {"target_type": "viewpoint", "target_id": 999999}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_target_type_is_rejected(self):
        response = self.client.post(
            FAVORITES_URL, {"target_type": "issue", "target_id": 1}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class FavoriteListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="participant", password="pw")
        self.other = User.objects.create_user(username="somebody_else", password="pw")
        self.client.force_authenticate(user=self.user)

    def _favorite(self, target_type, target_id, *, user=None):
        return Favorite.objects.create(
            user=user or self.user, target_type=target_type, target_id=target_id
        )

    def test_returns_full_card_payload_for_both_kinds(self):
        """收藏頁要能直接畫卡片，所以這裡回的欄位必須跟知識庫首頁一致，
        前端不用再逐筆打一次觀點/影片的細節 API。"""
        viewpoint = _make_viewpoint()
        video = _make_video()
        self._favorite(Favorite.TargetType.VIEWPOINT, viewpoint.id)
        self._favorite(Favorite.TargetType.VIDEO, video.id)

        response = self.client.get(FAVORITES_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["viewpoint"]), 1)
        self.assertEqual(len(response.data["video"]), 1)

        row = response.data["viewpoint"][0]
        self.assertEqual(row["id"], viewpoint.id)
        for field in (
            "topic_title",
            "dimension_name",
            "speaker_side",
            "stance_direction",
            "viewpoint_summary",
            "citation_count",
        ):
            self.assertIn(field, row)

        video_row = response.data["video"][0]
        self.assertEqual(video_row["id"], video.id)
        for field in ("title", "url", "thumbnail_url", "description"):
            self.assertIn(field, video_row)

    def test_only_lists_own_favorites(self):
        mine = _make_viewpoint()
        theirs = _make_viewpoint()
        self._favorite(Favorite.TargetType.VIEWPOINT, mine.id)
        self._favorite(Favorite.TargetType.VIEWPOINT, theirs.id, user=self.other)

        response = self.client.get(FAVORITES_URL)

        ids = [row["id"] for row in response.data["viewpoint"]]
        self.assertEqual(ids, [mine.id])

    def test_viewpoint_sent_back_to_review_disappears_from_favorites(self):
        """收藏之後才被退回審核的觀點不該再出現在任何人的收藏頁——收藏紀錄
        留著（研究者若再次核准就會自己回來），但讀取端要濾掉。"""
        viewpoint = _make_viewpoint()
        self._favorite(Favorite.TargetType.VIEWPOINT, viewpoint.id)

        viewpoint.review_status = ViewpointNode.ReviewStatus.REJECTED
        viewpoint.save(update_fields=["review_status"])

        response = self.client.get(FAVORITES_URL)

        self.assertEqual(response.data["viewpoint"], [])
        self.assertEqual(Favorite.objects.filter(user=self.user).count(), 1)

    def test_deleted_target_is_skipped_without_error(self):
        """target_type/target_id 是鬆散指向，沒有 FK 級聯，所以目標被刪掉之後
        會留下孤兒列。列表要安靜略過，不能 500。"""
        video = _make_video()
        self._favorite(Favorite.TargetType.VIDEO, video.id)
        video_id = video.id
        video.delete()

        response = self.client.get(FAVORITES_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["video"], [])
        self.assertTrue(
            Favorite.objects.filter(
                user=self.user, target_type="video", target_id=video_id
            ).exists()
        )

    def test_newest_favorite_comes_first(self):
        older = _make_viewpoint()
        newer = _make_viewpoint()
        self._favorite(Favorite.TargetType.VIEWPOINT, older.id)
        self._favorite(Favorite.TargetType.VIEWPOINT, newer.id)

        response = self.client.get(FAVORITES_URL)

        ids = [row["id"] for row in response.data["viewpoint"]]
        self.assertEqual(ids, [newer.id, older.id])
