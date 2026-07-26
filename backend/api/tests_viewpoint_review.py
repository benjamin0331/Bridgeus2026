"""Tests for the M6 觀點知識庫 Step 4 人工終審 API
(ViewpointReviewListView / ViewpointReviewDecisionView).

IsResearcher（「研究者」Django Group）on purpose, same reasoning as
CCNDSnapshotAnalysisView's IsAdminUser: these are candidate viewpoints that
haven't been vetted yet, not something a study participant should see or be
able to approve/reject themselves. Group membership (not is_staff) is the
permission source — see api.permissions.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from api.permissions import RESEARCHER_GROUP_NAME
from apps.summary.models import DialogueSummary, ViewpointNode

User = get_user_model()


def _make_researcher(username="researcher"):
    group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
    user = User.objects.create_user(username=username, password="pw")
    user.groups.add(group)
    return user


def _make_viewpoint(
    *,
    topic_id=102,
    dimension="anchor_safety",
    composite_score=0.5,
    review_status=ViewpointNode.ReviewStatus.PENDING,
):
    summary = DialogueSummary.objects.create(dialogue_id="9001", topic_id=topic_id)
    return ViewpointNode.objects.create(
        summary=summary,
        topic_id=topic_id,
        dimension=dimension,
        stance_direction="pro",
        user_input_text="核能在低碳排放這個面向確實有其優勢，但安全疑慮不能忽視",
        ai_response_text="您提到了核能的兩個核心矛盾",
        composite_score=composite_score,
        score_detail={"semantic_dist_raw": 0.4},
        review_status=review_status,
    )


class ViewpointReviewPermissionTests(APITestCase):
    def setUp(self):
        self.participant = User.objects.create_user(username="participant", password="pw")
        self.researcher = _make_researcher()
        self.viewpoint = _make_viewpoint()

    def test_participant_cannot_list_viewpoints(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.get("/api/summary/viewpoints/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_participant_cannot_submit_a_decision(self):
        self.client.force_authenticate(user=self.participant)
        response = self.client.post(
            f"/api/summary/viewpoints/{self.viewpoint.id}/review/", {"action": "approve"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_request_is_rejected(self):
        response = self.client.get("/api/summary/viewpoints/")
        self.assertIn(
            response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        )

    def test_researcher_can_list_viewpoints(self):
        self.client.force_authenticate(user=self.researcher)
        response = self.client.get("/api/summary/viewpoints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.viewpoint.id)
        self.assertEqual(response.data[0]["dialogue_id"], "9001")

    def test_is_staff_alone_without_group_membership_cannot_list(self):
        # is_staff (Django admin access) and 研究者 group membership are
        # deliberately independent now.
        admin_only = User.objects.create_user(username="admin_only", password="pw", is_staff=True)
        self.client.force_authenticate(user=admin_only)
        response = self.client.get("/api/summary/viewpoints/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ViewpointReviewListFilterTests(APITestCase):
    def setUp(self):
        self.researcher = _make_researcher()
        self.client.force_authenticate(user=self.researcher)
        self.pending = _make_viewpoint(composite_score=0.9, review_status=ViewpointNode.ReviewStatus.PENDING)
        self.approved = _make_viewpoint(composite_score=0.5, review_status=ViewpointNode.ReviewStatus.APPROVED)
        self.rejected = _make_viewpoint(composite_score=0.1, review_status=ViewpointNode.ReviewStatus.REJECTED)

    def test_defaults_to_pending_only(self):
        response = self.client.get("/api/summary/viewpoints/")
        ids = {item["id"] for item in response.data}
        self.assertEqual(ids, {self.pending.id})

    def test_status_all_returns_everything(self):
        response = self.client.get("/api/summary/viewpoints/", {"status": "all"})
        ids = {item["id"] for item in response.data}
        self.assertEqual(ids, {self.pending.id, self.approved.id, self.rejected.id})

    def test_status_approved_returns_only_approved(self):
        response = self.client.get("/api/summary/viewpoints/", {"status": "approved"})
        ids = {item["id"] for item in response.data}
        self.assertEqual(ids, {self.approved.id})

    def test_ordered_by_composite_score_descending(self):
        response = self.client.get("/api/summary/viewpoints/", {"status": "all"})
        scores = [item["composite_score"] for item in response.data]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_filters_by_topic_id(self):
        other_topic = _make_viewpoint(topic_id=103, dimension="anchor_equality")
        response = self.client.get("/api/summary/viewpoints/", {"status": "all", "topic_id": 103})
        ids = {item["id"] for item in response.data}
        self.assertEqual(ids, {other_topic.id})


class ViewpointReviewDecisionTests(APITestCase):
    def setUp(self):
        self.researcher = _make_researcher()
        self.client.force_authenticate(user=self.researcher)
        self.viewpoint = _make_viewpoint()

    def test_approve_sets_status_reviewer_and_timestamp(self):
        response = self.client.post(
            f"/api/summary/viewpoints/{self.viewpoint.id}/review/",
            {"action": "approve", "notes": "看起來沒問題"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.viewpoint.refresh_from_db()
        self.assertEqual(self.viewpoint.review_status, ViewpointNode.ReviewStatus.APPROVED)
        self.assertEqual(self.viewpoint.reviewed_by_id, self.researcher.id)
        self.assertIsNotNone(self.viewpoint.reviewed_at)
        self.assertEqual(self.viewpoint.review_notes, "看起來沒問題")

    def test_reject_sets_status(self):
        response = self.client.post(
            f"/api/summary/viewpoints/{self.viewpoint.id}/review/", {"action": "reject"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.viewpoint.refresh_from_db()
        self.assertEqual(self.viewpoint.review_status, ViewpointNode.ReviewStatus.REJECTED)

    def test_reset_sends_decided_node_back_to_pending(self):
        self.client.post(
            f"/api/summary/viewpoints/{self.viewpoint.id}/review/", {"action": "approve"}
        )

        response = self.client.post(
            f"/api/summary/viewpoints/{self.viewpoint.id}/review/",
            {"action": "reset", "notes": "需要再確認一次"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.viewpoint.refresh_from_db()
        self.assertEqual(self.viewpoint.review_status, ViewpointNode.ReviewStatus.PENDING)
        self.assertEqual(self.viewpoint.review_notes, "需要再確認一次")

    def test_invalid_action_is_rejected(self):
        response = self.client.post(
            f"/api/summary/viewpoints/{self.viewpoint.id}/review/", {"action": "delete"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_id_returns_404(self):
        response = self.client.post(
            "/api/summary/viewpoints/999999/review/", {"action": "approve"}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
