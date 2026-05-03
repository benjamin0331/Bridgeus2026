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
