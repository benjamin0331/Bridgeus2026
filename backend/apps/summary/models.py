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
    網址——其餘讀取路徑（公開清單、前端 <a href>）全部只認 url，不需要
    知道背後究竟是本地上傳檔案還是外部連結。"""

    title = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    video_file = models.FileField(upload_to="kb_videos/%Y/%m/", blank=True, null=True)
    thumbnail_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    # 對應 api.dialogue_topics.TOPIC_CONFIGS 的 key；留空 = 不限議題的推薦。
    topic_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    is_published = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return self.title
