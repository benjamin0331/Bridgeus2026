from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class Conversation(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "等待中"
        ACTIVE = "active", "進行中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    topic_id = models.PositiveIntegerField(db_index=True)
    user_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations_as_a",
    )
    user_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations_as_b",
    )
    session_number = models.PositiveSmallIntegerField(
        choices=[(1, "Session 1"), (2, "Session 2")],
        default=1,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.WAITING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Anchor embedding for topic-deviation detection; set once at conversation creation
    topic_anchor_embedding = VectorField(dimensions=384, null=True, blank=True)
    # Initial stance embeddings from pre-dialogue questionnaire open-ended answers
    user_a_initial_embedding = VectorField(dimensions=384, null=True, blank=True)
    user_b_initial_embedding = VectorField(dimensions=384, null=True, blank=True)
    # Populated by end_session()
    summary = models.TextField(null=True, blank=True)
    stats = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user_a", "topic_id"]),
            models.Index(fields=["user_b", "topic_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(session_number__in=[1, 2]),
                name="conv_session_number_1_or_2",
            ),
        ]

    def __str__(self):
        return (
            f"conv={self.id} topic={self.topic_id} "
            f"session={self.session_number} status={self.status}"
        )


class Message(models.Model):
    class DialoguePhase(models.TextChoices):
        EXPLORATION = "exploration", "探索期"
        CONFRONTATION = "confrontation", "交鋒期"
        CONVERGENCE = "convergence", "收斂期"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hh_messages",
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    emotion_score = models.FloatField(null=True, blank=True)
    dialogue_phase = models.CharField(
        max_length=20,
        choices=DialoguePhase.choices,
        null=True,
        blank=True,
    )
    # 384-dim embedding from paraphrase-multilingual-MiniLM-L12-v2; populated by M5 pipeline
    embedding = VectorField(dimensions=384, null=True, blank=True)

    class Meta:
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["conversation", "timestamp"]),
        ]

    def __str__(self):
        return (
            f"msg={self.id} conv={self.conversation_id} "
            f"sender={self.sender_id}"
        )


class AISuggestion(models.Model):
    class Category(models.TextChoices):
        REPHRASE = "rephrase", "改述"
        DIRECTION = "direction", "引導方向"
        REDIRECT = "redirect", "回到主題"

    class Action(models.TextChoices):
        ACCEPT = "accept", "接受"
        MODIFY = "modify", "修改"
        IGNORE = "ignore", "忽略"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="ai_suggestions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_suggestions",
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    original_content = models.TextField(null=True, blank=True)
    suggested_content = models.TextField()
    user_action = models.CharField(
        max_length=10, choices=Action.choices, null=True, blank=True
    )
    modified_content = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "user", "created_at"]),
        ]

    def __str__(self):
        return (
            f"suggestion conv={self.conversation_id} user={self.user_id} "
            f"cat={self.category} action={self.user_action}"
        )


class StanceDrift(models.Model):
    """Records a single stance-drift measurement for one user in one conversation."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="stance_drifts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stance_drifts",
    )
    drift_value = models.FloatField()
    measured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["measured_at"]
        indexes = [
            models.Index(fields=["conversation", "user", "measured_at"]),
        ]

    def __str__(self):
        return (
            f"drift conv={self.conversation_id} user={self.user_id} "
            f"val={self.drift_value:.4f}"
        )
