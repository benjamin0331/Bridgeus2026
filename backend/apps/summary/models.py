"""M6 觀點知識庫沉澱 — models for the post-dialogue summary + viewpoint pipeline.

Mirrors the schema previously prototyped as raw SQL in the standalone
`觀點知識庫/` scripts (品質篩選.py → 觀點寫入.py → 去重檢查.py). Embeddings use
the same model/dimension as the rest of the project (paraphrase-multilingual-
MiniLM-L12-v2, 384-dim; see chat/services/embedding.py) so ViewpointNode.embedding
is comparable against MatchMessage.embedding / AIConversation.embedding.
"""

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver
from pgvector.django import VectorField


class DialogueSummary(models.Model):
    # Either a DialogueMatch id (H-H) or an AI dialogue session_id — both are
    # stored as text since AI sessions use a hex string, not an integer PK.
    dialogue_id = models.CharField(max_length=64, db_index=True)
    topic_id = models.PositiveIntegerField(db_index=True)
    summary_text = models.TextField(blank=True)
    side_a_stance = models.CharField(max_length=32, blank=True)
    side_b_stance = models.CharField(max_length=32, blank=True)
    quality_score = models.FloatField(null=True, blank=True)
    stance_shift_magnitude = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["dialogue_id"]),
            models.Index(fields=["topic_id", "created_at"]),
        ]

    def __str__(self):
        return f"dialogue={self.dialogue_id} topic={self.topic_id}"


class ViewpointNode(models.Model):
    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "待審核"
        APPROVED = "approved", "已通過"
        REJECTED = "rejected", "未通過"

    class SpeakerSide(models.TextChoices):
        A = "a", "A"
        B = "b", "B"

    summary = models.ForeignKey(
        DialogueSummary,
        on_delete=models.CASCADE,
        related_name="viewpoints",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    dimension = models.CharField(max_length=64, db_index=True)
    # 這則觀點是 DialogueMatch.user_a 還是 user_b 說的（對應 DialogueSummary
    # 的 side_a_stance/side_b_stance 是哪一邊）。stance_direction 存的是「立場
    # 標籤」，不等於「是哪一方講的」——兩者未必一一對應（例如兩邊都可能被標成
    # 同一種立場），公開瀏覽要明確標示 A/B 方需要這個獨立欄位。blank=True 是
    # 為了相容這個欄位加入前就存在的舊資料。
    speaker_side = models.CharField(
        max_length=1, choices=SpeakerSide.choices, blank=True
    )
    stance_direction = models.CharField(max_length=32, blank=True)
    user_input_text = models.TextField()
    # ⚠️ 只能寫入 AIConversation.ai_response（已由 ReplyStreamGate 剝離
    # <judgment> 判定段的 <reply> 內容）。絕不可寫入 LLM 的原始串流輸出，
    # 否則模型的內部判定推理會沉澱進觀點知識庫並污染品質評分。
    # 對應的原始判定段另存於 AIConversation.internal_judgment，僅供研究分析。
    ai_response_text = models.TextField(blank=True)
    viewpoint_summary = models.TextField(blank=True)
    source_message_ids = ArrayField(
        models.IntegerField(),
        default=list,
        blank=True,
    )
    # 384-dim embedding of user_input_text (paraphrase-multilingual-MiniLM-L12-v2);
    # must stay in the same model/dimension as MatchMessage.embedding /
    # AIConversation.embedding so dedup + any future cross-table similarity
    # queries are comparable.
    embedding = VectorField(dimensions=384, null=True, blank=True)
    composite_score = models.FloatField(null=True, blank=True)
    score_detail = models.JSONField(default=dict, blank=True)
    citation_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Step 4 人工終審（見 apps/summary/pipeline/quality_filter.py 模組 docstring）。
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_viewpoints",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["topic_id", "dimension"]),
            models.Index(fields=["review_status"]),
        ]

    def __str__(self):
        return f"topic={self.topic_id} dim={self.dimension} score={self.composite_score}"


class VideoRecommendation(models.Model):
    """觀點知識庫首頁的影片推薦區塊。研究者從前端設定頁的「影片管理」面板
    本地上傳影片檔（video_file），上傳成功後 url 會自動填成該檔案的存取
    網址——其餘讀取路徑（公開清單、前端 <video src>）全部只認 url，不需要
    知道背後究竟是本地上傳檔案還是外部連結。"""

    class StanceDirection(models.TextChoices):
        SUPPORT = "support", "支持"
        NEUTRAL = "neutral", "中立"
        OPPOSE = "oppose", "反對"

    title = models.CharField(max_length=255)
    # 刻意用 CharField 而不是 URLField：本地上傳的影片存的是根相對路徑
    # （/media/kb_videos/…，見 api.views._fill_video_url_from_file），
    # URLField 的 URLValidator 會把它判成無效網址——研究者從 Django admin
    # 編輯任何一筆已上傳的影片都會被擋下來，即使他根本沒動到這一欄。
    # 外部連結（https://…）一樣存在這裡，兩種來源下游都只認這一欄。
    url = models.CharField(max_length=500, blank=True)
    video_file = models.FileField(upload_to="kb_videos/%Y/%m/", blank=True, null=True)
    thumbnail_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    # 對應 api.dialogue_topics.TOPIC_CONFIGS 的 key；留空 = 不限議題的推薦。
    topic_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    # 這部影片代表哪一種立場——後期影片推薦演算法要靠這個欄位判斷「跟使用者
    # 立場相反的影片」是哪些，只有研究者在影片管理面板能設定。預設 neutral
    # （不特別偏向任一方），對舊資料相容。
    stance_direction = models.CharField(
        max_length=16,
        choices=StanceDirection.choices,
        default=StanceDirection.NEUTRAL,
    )
    is_published = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return self.title


class VideoWatchEvent(models.Model):
    """使用者每次點開一部推薦影片就記一筆，供影片推薦演算法使用：

    - 初期（使用者在這個議題還沒有後測問卷）：依全部影片被記錄的次數
      （= 點擊率）排序，取熱門前 10 部。
    - 後期（已有後測問卷）：依使用者立場的「相反立場」影片優先推薦，並用
      這裡累積的 stance_direction 分布持續檢查是否已經達到 4:6～6:4 之間
      的均衡曝光比例（見 api.views.VideoRecommendationListView）。

    stance_direction 是「該次觀看當下」影片被標記的立場快照（不是外鍵指過
    去現查），研究者事後改動影片標記不會回頭改寫已經發生的觀看紀錄，
    ratio 判斷才會穩定。topic_id 存的是使用者觀看當下所在的議題（來自前端
    請求，不是 video.topic_id）——影片可能是「不限議題」（topic_id=None）的
    共用推薦，但看的人一定是在某個特定議題頁面底下看的，比例要算在那個
    議題上。
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_watch_events",
    )
    video = models.ForeignKey(
        VideoRecommendation,
        on_delete=models.CASCADE,
        related_name="watch_events",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    stance_direction = models.CharField(
        max_length=16, choices=VideoRecommendation.StanceDirection.choices
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "topic_id"]),
        ]

    def __str__(self):
        return f"user={self.user_id} video={self.video_id} stance={self.stance_direction}"


# ---------------------------------------------------------------------------
# 影片檔的磁碟清理
#
# Django 從 1.3 起就不會自動刪 FileField 的實體檔案——刪掉 DB 列或換上新檔
# 之後，舊檔會永遠留在 MEDIA_ROOT 底下，沒有任何一頁看得到它，但一直佔著
# 研究用 VPS 那顆不大的磁碟。影片是這個專案裡唯一的大型二進位檔，累積速度
# 遠比其他資料快，所以這裡補上。
#
# 用 signal 而不是覆寫 view：研究者也可能從 Django admin 換檔或刪除，
# 兩條路徑都要收拾乾淨。
# ---------------------------------------------------------------------------


def _discard_video_file(file_field) -> None:
    """安靜地把檔案從儲存後端移除。

    刪不掉不該讓使用者的操作失敗——列已經刪了／新檔已經存好了，這裡失敗
    最多是留下一個孤兒檔，比讓整個請求 500 好。
    """
    if not file_field:
        return
    try:
        file_field.storage.delete(file_field.name)
    except Exception:  # noqa: BLE001 — 清理失敗不該影響主流程
        pass


@receiver(post_delete, sender=VideoRecommendation)
def _delete_video_file_on_record_delete(sender, instance, **kwargs):
    _discard_video_file(instance.video_file)


@receiver(pre_save, sender=VideoRecommendation)
def _delete_replaced_video_file(sender, instance, **kwargs):
    """換上新檔時，把這一列原本指著的舊檔刪掉。"""
    if not instance.pk:
        return
    try:
        previous = VideoRecommendation.objects.get(pk=instance.pk)
    except VideoRecommendation.DoesNotExist:
        return
    old_file = previous.video_file
    if old_file and old_file.name != getattr(instance.video_file, "name", None):
        _discard_video_file(old_file)
