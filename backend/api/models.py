from django.conf import settings
from django.contrib.auth.models import User
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
    # 384-dim embedding of user_prompt (paraphrase-multilingual-MiniLM-L12-v2);
    # populated at write time so stance-drift can read it instead of re-encoding
    # every turn on each message. Mirrors MatchMessage.embedding.
    embedding = VectorField(dimensions=384, null=True, blank=True)
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


# ---------------------------------------------------------------------------
# Post-dialogue questionnaire
# ---------------------------------------------------------------------------

# C1 reversed items: C1-2→Q5, C1-6→Q4, C1-7→Q6, C1-8→Q2
_POST_LIKERT_REVERSED = {2, 6, 7, 8}
# (post_index, pre_question_id) — needed for research cross-referencing
POST_LIKERT_TO_PRE_QUESTION = {1: 8, 2: 5, 3: 3, 4: 7, 5: 1, 6: 4, 7: 6, 8: 2}


def _likert_field(verbose_name):
    return models.PositiveSmallIntegerField(
        verbose_name=verbose_name,
        help_text="1–7 Likert scale",
    )


class PostDialogueResponse(models.Model):
    class ExperimentCondition(models.TextChoices):
        AI = "ai", "H-AI"
        HH = "hh", "H-H"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="post_dialogue_responses",
    )
    topic_id = models.PositiveIntegerField(db_index=True)
    session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    room_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    experiment_condition = models.CharField(
        max_length=4,
        choices=ExperimentCondition.choices,
        db_index=True,
    )

    # Part C-1: Post stance Likert (8 items, scrambled order mirrors pre-test Q1-Q8)
    post_likert_1 = _likert_field("C1-1 (pre=Q8, positive)")
    post_likert_2 = _likert_field("C1-2 (pre=Q5, reverse)")
    post_likert_3 = _likert_field("C1-3 (pre=Q3, positive)")
    post_likert_4 = _likert_field("C1-4 (pre=Q7, positive)")
    post_likert_5 = _likert_field("C1-5 (pre=Q1, positive)")
    post_likert_6 = _likert_field("C1-6 (pre=Q4, reverse)")
    post_likert_7 = _likert_field("C1-7 (pre=Q6, reverse)")
    post_likert_8 = _likert_field("C1-8 (pre=Q2, reverse)")

    # Part C-2: Experience scale
    exp_stance_change_1 = _likert_field("C2-1 主觀立場改變自覺")
    exp_stance_change_2 = _likert_field("C2-2 主觀立場改變自覺")
    exp_quality_1 = _likert_field("C2-3 對話品質感知")
    exp_quality_2 = _likert_field("C2-4 對話品質感知")
    exp_reflection_1 = _likert_field("C2-5 自我反思/元認知")
    exp_reflection_2 = _likert_field("C2-6 自我反思/元認知")

    # Part C-3: CCND assessment
    ccnd_attention = _likert_field("C3-1 注意力門檻")
    ccnd_awareness = _likert_field("C3-2 認知差異覺察")
    ccnd_influence = _likert_field("C3-3 表達/思考調整")

    # Part C-4: Opponent judgment (H-AI only; NULL for H-H)
    # 1=human, 2=AI, 3=uncertain
    opponent_judgment = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="1=真人, 2=AI, 3=不確定；H-H 組為 NULL",
    )

    # Part D: Open questions
    post_open_comprehension = models.TextField(
        help_text="D1 — 對立觀點陳述（最低 50 字）；與前測 Q10 同題幹，向量化後存 pgvector"
    )
    post_open_feedback = models.TextField(
        blank=True,
        help_text="D2 — 自由回饋，無字數限制",
    )

    # Part E: Discomfort flag (detail stored in DiscomfortReport)
    discomfort_flag = models.BooleanField(default=False)

    # Debriefing consent: NULL=pending, True=consent, False=withdrawn
    consent_confirmed = models.BooleanField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(opponent_judgment__isnull=True)
                | Q(opponent_judgment__in=[1, 2, 3]),
                name="post_opponent_judgment_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "topic_id", "created_at"],
                name="post_response_user_topic_idx",
            ),
        ]

    # --- Scoring methods ---

    def _adjusted_c1_scores(self) -> list[float]:
        scores = []
        for i in range(1, 9):
            raw = getattr(self, f"post_likert_{i}")
            scores.append(8 - raw if i in _POST_LIKERT_REVERSED else float(raw))
        return scores

    def s_post(self) -> float:
        return round(sum(self._adjusted_c1_scores()) / 8, 4)

    def delta_s(self, s_pre: float) -> float:
        return round(self.s_post() - float(s_pre), 4)

    def stance_centrism(self, s_pre: float) -> float:
        """< 0 = depolarized, > 0 = polarized further, = 0 = unchanged."""
        return round(abs(self.s_post() - 4) - abs(float(s_pre) - 4), 4)

    def __str__(self):
        return (
            f"PostResponse user={self.user_id} topic={self.topic_id} "
            f"cond={self.experiment_condition}"
        )


class DiscomfortReport(models.Model):
    response = models.OneToOneField(
        PostDialogueResponse,
        on_delete=models.CASCADE,
        related_name="discomfort_report",
    )
    detail = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DiscomfortReport response={self.response_id}"


# ---------------------------------------------------------------------------
# Part F — platform experience feedback (collected after debriefing)
# ---------------------------------------------------------------------------


class PlatformFeedback(models.Model):
    """Part F: 7-item platform UX feedback, filled right after debriefing.

    Linked one-to-one to a PostDialogueResponse so the experiment condition,
    topic and stance-change deltas can be JOINed for the cross-indicators
    described in Part F (e.g. F5 vs |ΔS|, NPS H-AI vs H-H).
    """

    response = models.OneToOneField(
        PostDialogueResponse,
        on_delete=models.CASCADE,
        related_name="platform_feedback",
    )

    # F1–F5: 7-point satisfaction Likert (1=非常不滿意, 7=非常滿意)
    ux_matching = _likert_field("F1 配對機制")
    ux_chatroom = _likert_field("F2 對話室體驗")
    ux_nlp_intervention = _likert_field("F3 NLP 介入機制")
    ux_ccnd = _likert_field("F4 概念認知網路圖")
    ux_overall = _likert_field("F5 系統整體可用性")

    # F6: NPS (0–10)
    nps_score = models.PositiveSmallIntegerField(help_text="F6 推薦意願 0–10")

    # F7: open-ended improvement suggestion (optional)
    ux_improvement = models.TextField(
        blank=True,
        null=True,
        help_text="F7 最需要改進的地方（選填）",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(nps_score__gte=0) & Q(nps_score__lte=10),
                name="platform_feedback_nps_0_10",
            ),
        ]

    # --- Analysis helpers ---

    def mean_ux(self) -> float:
        """F1–F5 平均功能滿意度。"""
        total = (
            self.ux_matching
            + self.ux_chatroom
            + self.ux_nlp_intervention
            + self.ux_ccnd
            + self.ux_overall
        )
        return round(total / 5, 4)

    def nps_category(self) -> str:
        """9–10 推薦者、7–8 被動者、0–6 批評者。"""
        if self.nps_score >= 9:
            return "promoter"
        if self.nps_score >= 7:
            return "passive"
        return "detractor"

    def __str__(self):
        return (
            f"PlatformFeedback response={self.response_id} "
            f"mean_ux={self.mean_ux()} nps={self.nps_score}"
        )
class Issue(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="issues")
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    stance = models.CharField(max_length=20, null=True, blank=True)
    emotion = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Issue({self.id}) by user={self.author_id}: {self.title[:40]}"
