"""M6 觀點知識庫沉澱 — models for the post-dialogue summary + viewpoint pipeline.

Mirrors the schema previously prototyped as raw SQL in the standalone
`觀點知識庫/` scripts (品質篩選.py → 觀點寫入.py → 去重檢查.py). Embeddings use
the same model/dimension as the rest of the project (paraphrase-multilingual-
MiniLM-L12-v2, 384-dim; see chat/services/embedding.py) so ViewpointNode.embedding
is comparable against MatchMessage.embedding / AIConversation.embedding.
"""

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
    summary = models.ForeignKey(
        DialogueSummary,
        on_delete=models.CASCADE,
        related_name="viewpoints",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    dimension = models.CharField(max_length=64, db_index=True)
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

    class Meta:
        indexes = [
            models.Index(fields=["topic_id", "dimension"]),
        ]

    def __str__(self):
        return f"topic={self.topic_id} dim={self.dimension} score={self.composite_score}"
