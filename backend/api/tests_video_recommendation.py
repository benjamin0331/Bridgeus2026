"""M6 知識庫影片推薦演算法（api.views.VideoRecommendationListView）。

重點在「後期」階段：使用者填過後測問卷後，推薦要偏向他的相反立場，但不能
變成只看得到相反立場——那樣使用者永遠沒機會累積另一側的觀看紀錄，
_is_exposure_balanced() 的比例永遠是 0，就再也回不到熱門排序（死結）。
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from api.models import PostDialogueResponse
from apps.summary.models import VideoRecommendation, VideoWatchEvent

User = get_user_model()

TOPIC_ID = 102
Stance = VideoRecommendation.StanceDirection


class VideoRecommendationOrderingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viewer", password="x")
        self.client.force_authenticate(self.user)
        self.support = VideoRecommendation.objects.create(
            title="support video", url="https://example.com/s", topic_id=TOPIC_ID,
            stance_direction=Stance.SUPPORT,
        )
        self.oppose = VideoRecommendation.objects.create(
            title="oppose video", url="https://example.com/o", topic_id=TOPIC_ID,
            stance_direction=Stance.OPPOSE,
        )
        self.neutral = VideoRecommendation.objects.create(
            title="neutral video", url="https://example.com/n", topic_id=TOPIC_ID,
            stance_direction=Stance.NEUTRAL,
        )

    def _get(self):
        return self.client.get("/api/summary/videos/", {"topic_id": TOPIC_ID})

    def _titles(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        return [row["title"] for row in response.data]

    def _make_late_stage_supporter(self):
        """讓使用者進入「後期」且立場為支持 → 相反立場 = oppose。

        C1 的 8 題全填 7，正向題直接取值、反向題換算後仍偏高，s_post 因此落在
        支持端（見 PostDialogueResponse.s_post）。
        """
        PostDialogueResponse.objects.create(
            user=self.user,
            topic_id=TOPIC_ID,
            session_id="sess-video",
            experiment_condition=PostDialogueResponse.ExperimentCondition.AI,
            post_likert_1=7, post_likert_2=1, post_likert_3=7, post_likert_4=7,
            post_likert_5=7, post_likert_6=1, post_likert_7=1, post_likert_8=1,
            exp_stance_change_1=4, exp_stance_change_2=4,
            exp_quality_1=4, exp_quality_2=4,
            exp_reflection_1=4, exp_reflection_2=4,
            exp_comprehension_1=4,
            ccnd_attention=4, ccnd_awareness=4, ccnd_influence=4,
            opponent_judgment=2,
            post_open_comprehension="x" * 60,
        )

    def test_early_stage_returns_every_published_video(self):
        """還沒填後測 = 初期，純熱門排序，不做任何立場過濾。"""
        self.assertCountEqual(
            self._titles(), ["support video", "oppose video", "neutral video"]
        )

    def test_late_stage_puts_opposite_stance_first(self):
        self._make_late_stage_supporter()
        self.assertEqual(self._titles()[0], "oppose video")

    def test_late_stage_still_lists_the_other_stances(self):
        """死結的根源：只回傳相反立場的話，使用者永遠累積不到另一側的觀看
        紀錄，曝光比例就永遠達不到平衡。"""
        self._make_late_stage_supporter()
        self.assertCountEqual(
            self._titles(), ["support video", "oppose video", "neutral video"]
        )

    def test_late_stage_is_not_empty_when_no_opposite_stance_video_exists(self):
        VideoRecommendation.objects.filter(stance_direction=Stance.OPPOSE).delete()
        self._make_late_stage_supporter()
        self.assertCountEqual(self._titles(), ["support video", "neutral video"])

    def _watch(self, video, stance, times):
        for _ in range(times):
            VideoWatchEvent.objects.create(
                user=self.user, video=video, topic_id=TOPIC_ID,
                stance_direction=stance,
            )

    def test_unbalanced_exposure_keeps_the_stance_priority(self):
        """3:1 → 少數方只佔 25%，還沒到 40% 門檻，仍然把相反立場排前面。
        注意 support 的觀看次數比較高，所以純熱門排序會是 support 在前——
        這裡看到 oppose 在前，才證明優先排序真的有生效。"""
        self._make_late_stage_supporter()
        self._watch(self.support, Stance.SUPPORT, 3)
        self._watch(self.oppose, Stance.OPPOSE, 1)
        self.assertEqual(self._titles()[0], "oppose video")

    def test_balanced_exposure_drops_the_stance_priority(self):
        """3:2 → 少數方佔 40%，達到門檻視為已均衡 → 回到純熱門排序。
        support 觀看數較高，所以它排最前面，而不是相反立場的 oppose。"""
        self._make_late_stage_supporter()
        self._watch(self.support, Stance.SUPPORT, 3)
        self._watch(self.oppose, Stance.OPPOSE, 2)
        self.assertEqual(self._titles()[0], "support video")

    def test_neutral_watches_do_not_count_towards_balance(self):
        """中立影片不屬於任何一側，不該把比例算成已均衡。"""
        self._make_late_stage_supporter()
        self._watch(self.neutral, Stance.NEUTRAL, 10)
        self.assertEqual(self._titles()[0], "oppose video")

    def test_unpublished_videos_are_never_returned(self):
        self.oppose.is_published = False
        self.oppose.save(update_fields=["is_published"])
        self._make_late_stage_supporter()
        self.assertNotIn("oppose video", self._titles())
