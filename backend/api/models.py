from django.conf import settings
from django.db import models
from django.db.models import F, Q
from pgvector.django import VectorField


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


class DialogueSessionRecord(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "進行中"
        CLOSED = "closed", "已結束"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dialogue_session_records",
    )
    session_id = models.CharField(max_length=64, unique=True, db_index=True)
    topic_id = models.PositiveIntegerField(db_index=True)
    topic_title = models.CharField(max_length=255)
    collection_name = models.CharField(max_length=255)
    survey_context = models.JSONField(default=dict, blank=True)
    session_state = models.JSONField(default=dict, blank=True)
    semantic_tree_state = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    last_activity_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "topic_id", "status", "last_activity_at"],
                name="dialogue_session_restore_idx",
            ),
        ]

    def __str__(self):
        return f"session={self.session_id} topic={self.topic_id} status={self.status}"


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
    q9_embedding = VectorField(dimensions=384, null=True, blank=True)
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
    likert_distance = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    semantic_distance = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    match_score = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    matching_algorithm_version = models.CharField(max_length=64, default="legacy")
    room_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    topic_anchor_embedding = VectorField(dimensions=384, null=True, blank=True)
    summary = models.TextField(null=True, blank=True)
    stats = models.JSONField(null=True, blank=True)

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
    emotion_score = models.FloatField(null=True, blank=True)
    dialogue_phase = models.CharField(max_length=32, blank=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
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


class MatchAISuggestion(models.Model):
    class Category(models.TextChoices):
        REPHRASE = "rephrase", "改述"
        DIRECTION = "direction", "引導方向"
        REDIRECT = "redirect", "回到主題"

    class Action(models.TextChoices):
        ACCEPT = "accept", "接受"
        MODIFY = "modify", "修改"
        IGNORE = "ignore", "忽略"

    match = models.ForeignKey(
        DialogueMatch,
        on_delete=models.CASCADE,
        related_name="ai_suggestions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_ai_suggestions",
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    original_content = models.TextField(null=True, blank=True)
    suggested_content = models.TextField()
    user_action = models.CharField(
        max_length=10,
        choices=Action.choices,
        null=True,
        blank=True,
    )
    modified_content = models.TextField(null=True, blank=True)
    final_content = models.TextField(null=True, blank=True)
    response_time_ms = models.IntegerField(null=True, blank=True)
    trigger_score = models.FloatField(null=True, blank=True)
    context_message_ids = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["match", "user", "created_at"],
                name="match_ai_suggestion_lookup_idx",
            ),
        ]

    def __str__(self):
        return (
            f"suggestion match={self.match_id} user={self.user_id} "
            f"category={self.category}"
        )


class MatchStanceDrift(models.Model):
    match = models.ForeignKey(
        DialogueMatch,
        on_delete=models.CASCADE,
        related_name="stance_drifts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_stance_drifts",
    )
    drift_value = models.FloatField()
    measured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["measured_at"]
        indexes = [
            models.Index(
                fields=["match", "user", "measured_at"],
                name="match_stance_drift_lookup_idx",
            ),
        ]

    def __str__(self):
        return (
            f"drift match={self.match_id} user={self.user_id} "
            f"value={self.drift_value:.4f}"
        )
