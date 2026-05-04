from django.conf import settings
from django.db import models
from django.db.models import F, Q


class AIConversation(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_conversations",
    )
    session_id = models.CharField(max_length=64, blank=True, db_index=True)
    topic_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    user_prompt = models.TextField(help_text="使用者的提問")
    ai_response = models.TextField(blank=True, null=True, help_text="AI 的回覆")
    dialogue_phase = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, help_text="建立時間")

    class Meta:
        indexes = [
            models.Index(fields=["user", "session_id", "created_at"]),
            models.Index(fields=["topic_id", "created_at"]),
        ]

    def __str__(self):
        return f"session={self.session_id or '-'} prompt={self.user_prompt[:20]}..."


class UserStanceProfile(models.Model):
    class StanceCategory(models.TextChoices):
        SUPPORT = "support", "支持"
        NEUTRAL = "neutral", "中立"
        OPPOSE = "oppose", "反對"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stance_profiles",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    stance_score = models.DecimalField(max_digits=4, decimal_places=2)
    stance_category = models.CharField(
        max_length=20,
        choices=StanceCategory.choices,
        db_index=True,
    )
    survey_answers = models.JSONField(default=dict)
    survey_open_answers = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "topic_id"],
                name="uniq_stance_profile_user_topic",
            ),
            models.CheckConstraint(
                condition=Q(stance_score__gte=1) & Q(stance_score__lte=7),
                name="stance_score_between_1_and_7",
            ),
        ]

    def __str__(self):
        return (
            f"user={self.user_id} topic={self.topic_id} "
            f"score={self.stance_score}"
        )


class DialogueMatch(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "進行中"
        CLOSED = "closed", "已結束"
        CANCELLED = "cancelled", "已取消"

    topic_id = models.PositiveIntegerField(db_index=True)
    user_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dialogue_matches_as_a",
    )
    user_b = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dialogue_matches_as_b",
    )
    user_a_score = models.DecimalField(max_digits=4, decimal_places=2)
    user_b_score = models.DecimalField(max_digits=4, decimal_places=2)
    room_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(user_a=F("user_b")),
                name="match_users_must_differ",
            ),
            models.CheckConstraint(
                condition=Q(user_a_score__gte=1) & Q(user_a_score__lte=7),
                name="match_user_a_score_between_1_and_7",
            ),
            models.CheckConstraint(
                condition=Q(user_b_score__gte=1) & Q(user_b_score__lte=7),
                name="match_user_b_score_between_1_and_7",
            ),
        ]

    def __str__(self):
        return (
            f"match={self.id} room={self.room_id} "
            f"status={self.status}"
        )


class MatchQueueEntry(models.Model):
    class Status(models.TextChoices):
        MATCHING = "matching", "匹配中"
        MATCHED = "matched", "匹配成功"
        CANCELLED = "cancelled", "取消匹配"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_queue_entries",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    profile = models.ForeignKey(
        UserStanceProfile,
        on_delete=models.PROTECT,
        related_name="queue_entries",
    )
    stance_score = models.DecimalField(max_digits=4, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.MATCHING,
        db_index=True,
    )
    match = models.ForeignKey(
        DialogueMatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="queue_entries",
    )
    waiting_started_at = models.DateTimeField(auto_now_add=True)
    matched_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "topic_id"],
                condition=Q(status="matching"),
                name="uniq_active_queue_user_topic",
            ),
            models.CheckConstraint(
                condition=Q(stance_score__gte=1) & Q(stance_score__lte=7),
                name="queue_stance_score_between_1_and_7",
            ),
        ]
        indexes = [
            models.Index(
                fields=["topic_id", "status", "stance_score"],
                name="match_queue_lookup_idx",
            ),
        ]

    def __str__(self):
        return (
            f"user={self.user_id} topic={self.topic_id} "
            f"status={self.status}"
        )


class MatchMessage(models.Model):
    match = models.ForeignKey(
        DialogueMatch,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_messages",
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["match", "created_at", "id"],
                name="match_message_order_idx",
            ),
        ]

    def __str__(self):
        return f"match={self.match_id} sender={self.sender_id}"
