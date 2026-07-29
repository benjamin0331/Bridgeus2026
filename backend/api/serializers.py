from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from django.contrib.auth.models import Group, User
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.auth.password_validation import validate_password as dj_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.summary.models import VideoRecommendation, ViewpointNode

from .dialogue_topics import get_dialogue_survey
from .models import (
    AIConversation,
    DiscomfortReport,
    MatchMessage,
    PlatformDisplaySetting,
    PlatformFeedback,
    PostDialogueResponse,
)
from .permissions import RESEARCHER_GROUP_NAME


class BridgeUsTokenObtainPairSerializer(TokenObtainPairSerializer):
    """在 JWT access token 的 payload 裡加 is_researcher claim。

    前端 LoginPage 本來就會用 getAccessTokenPayload() 解析 access token 拿
    user_id，這裡多加一個 claim 之後前端不用另外呼叫 API，解 token 就能知道
    目前登入的是不是研究者帳號——用來讓 /viewpoint-review 之類的研究者專用
    頁面知道要不要顯示連結（實際的存取控制仍然是後端 IsResearcher，這裡只是
    給前端一個「要不要顯示」的依據，不是真正的權限判斷）。

    用「研究者」Group 而不是 is_staff：is_staff 語意上是「能不能登入 Django
    /admin/」，跟「有沒有研究者身分」是兩件不一定相同的事。
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["is_researcher"] = user.groups.filter(name=RESEARCHER_GROUP_NAME).exists()
        return token


class ViewpointNodeReviewSerializer(serializers.ModelSerializer):
    """M6 觀點知識庫 Step 4 人工終審用的唯讀列表/詳情欄位。

    dialogue_id 從 summary 帶出來，方便審核者對照原始對話（前端目前還沒有連到
    原始對話內容的連結，只能先靠 dialogue_id 手動查）。
    """

    dialogue_id = serializers.CharField(source="summary.dialogue_id", read_only=True)
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, default=None
    )

    class Meta:
        model = ViewpointNode
        fields = [
            "id",
            "dialogue_id",
            "topic_id",
            "dimension",
            "speaker_side",
            "stance_direction",
            "user_input_text",
            "ai_response_text",
            "viewpoint_summary",
            "composite_score",
            "score_detail",
            "citation_count",
            "review_status",
            "reviewed_by_username",
            "reviewed_at",
            "review_notes",
            "created_at",
        ]
        read_only_fields = fields


class ViewpointNodeReviewDecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject"])
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ViewpointHighlightSerializer(serializers.Serializer):
    """知識庫首頁「熱門對話」卡片、以及「觀看更多」清單用的公開唯讀欄位——
    只允許已通過人工審核（review_status=approved）的 ViewpointNode 走這條
    序列化，見 views.KnowledgeBaseHighlightsView / KnowledgeBaseViewpointBrowseView。
    不重用 ViewpointNodeReviewSerializer，因為那個是研究者審核用，會帶
    user_input_text 等未經篩選的原始逐字稿。"""

    id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    topic_title = serializers.CharField()
    dimension = serializers.CharField()
    dimension_name = serializers.CharField()
    speaker_side = serializers.CharField(allow_blank=True)
    stance_direction = serializers.CharField(allow_blank=True)
    viewpoint_summary = serializers.CharField(allow_blank=True)
    citation_count = serializers.IntegerField()
    composite_score = serializers.FloatField(allow_null=True)
    created_at = serializers.DateTimeField()


class DialogueSummaryDetailSerializer(serializers.Serializer):
    """知識庫「熱門對話」卡片點進去的對話紀錄——只回傳 DialogueSummary 已沉澱
    的摘要欄位，不帶 ViewpointNode.user_input_text/ai_response_text 原始逐字
    稿，避免把真實參與者的對話內容直接開放給任何登入使用者看。"""

    dialogue_summary_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    topic_title = serializers.CharField()
    summary_text = serializers.CharField(allow_blank=True)
    side_a_stance = serializers.CharField(allow_blank=True)
    side_b_stance = serializers.CharField(allow_blank=True)
    quality_score = serializers.FloatField(allow_null=True)
    stance_shift_magnitude = serializers.FloatField(allow_null=True)
    created_at = serializers.DateTimeField()
    viewpoints = ViewpointHighlightSerializer(many=True)


class VideoRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoRecommendation
        fields = [
            "id",
            "title",
            "url",
            "thumbnail_url",
            "description",
            "topic_id",
            "created_at",
        ]


class AIConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversation
        fields = '__all__'


class MessageReactionSerializer(serializers.Serializer):
    """Like/dislike an opponent message; value=0 removes the reaction."""

    target_type = serializers.ChoiceField(choices=["ai", "match"])
    target_id = serializers.IntegerField(min_value=1)
    value = serializers.ChoiceField(choices=[1, -1, 0])


class DialogueSessionCreateSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    topic_title = serializers.CharField(max_length=255)
    topic_description = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
        required=False,
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
    user_initial_argument = serializers.CharField(required=False, allow_blank=True)


class DialogueReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)


class DialogueTopicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=500)
    date = serializers.CharField(max_length=20)


class DialogueTopicTrendingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    hits = serializers.IntegerField()


class DialogueSurveyQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(max_length=10, required=False)
    tag = serializers.CharField(max_length=100)
    text = serializers.CharField(max_length=500)
    question_type = serializers.CharField(max_length=30, required=False)
    dimension = serializers.CharField(max_length=50, required=False, allow_blank=True)
    direction = serializers.CharField(max_length=20, required=False, allow_blank=True)
    reverse_scored = serializers.BooleanField(required=False)
    min_value = serializers.IntegerField(required=False)
    max_value = serializers.IntegerField(required=False)
    placeholder = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    min_sentences = serializers.IntegerField(required=False)
    max_sentences = serializers.IntegerField(required=False)


class DialogueSurveySerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    subtitle = serializers.CharField(max_length=255)
    scale = serializers.DictField(required=False)
    stance_rules = serializers.DictField(required=False)
    questions = DialogueSurveyQuestionSerializer(many=True)
    open_questions = DialogueSurveyQuestionSerializer(many=True, required=False)
    semantic_vector_interface = serializers.DictField(required=False)


class MatchingJoinSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
    restart_existing_match = serializers.BooleanField(
        default=False,
        required=False,
    )

    def validate(self, attrs):
        topic_id = attrs["topic_id"]
        survey = get_dialogue_survey(topic_id)
        if not survey:
            raise serializers.ValidationError(
                {"topic_id": "找不到這個議題的問卷設定。"}
            )

        required_question_ids = {
            str(question["id"]) for question in survey.get("questions", [])
        }
        provided_question_ids = set(attrs["survey_answers"].keys())
        missing_question_ids = sorted(required_question_ids - provided_question_ids)
        if missing_question_ids:
            raise serializers.ValidationError(
                {
                    "survey_answers": (
                        "量表題尚未填寫完整，缺少題號："
                        + ", ".join(missing_question_ids)
                    )
                }
            )

        return attrs


class MatchingTopicSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()

    def validate_topic_id(self, value):
        if not get_dialogue_survey(value):
            raise serializers.ValidationError("找不到這個議題的問卷設定。")
        return value


class MatchingStateSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    status = serializers.CharField(max_length=20)
    stance_score = serializers.DecimalField(
        max_digits=4,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    stance_category = serializers.CharField(
        max_length=20,
        required=False,
        allow_null=True,
    )
    queue_entry_id = serializers.IntegerField(required=False, allow_null=True)
    waiting_started_at = serializers.DateTimeField(required=False, allow_null=True)
    matched_at = serializers.DateTimeField(required=False, allow_null=True)
    cancelled_at = serializers.DateTimeField(required=False, allow_null=True)
    closed_at = serializers.DateTimeField(required=False, allow_null=True)
    match_id = serializers.IntegerField(required=False, allow_null=True)
    room_id = serializers.CharField(max_length=64, required=False, allow_null=True)
    other_user_id = serializers.IntegerField(required=False, allow_null=True)
    other_user_name = serializers.CharField(max_length=150, required=False, allow_null=True)
    presence = serializers.JSONField(required=False, allow_null=True)
    absence_deadline = serializers.DateTimeField(required=False, allow_null=True)
    fallback_offer = serializers.JSONField(required=False, allow_null=True)
    binding_source = serializers.CharField(
        max_length=16, required=False, allow_null=True
    )
    survey_required = serializers.BooleanField(required=False)
    survey_deadline = serializers.DateTimeField(required=False, allow_null=True)
    partner_state = serializers.CharField(
        max_length=16, required=False, allow_null=True
    )


class MatchMessageSerializer(serializers.ModelSerializer):
    sender_id = serializers.IntegerField(source="sender.id", read_only=True)
    sender_name = serializers.SerializerMethodField()

    def get_sender_name(self, obj):
        return "匿名使用者"

    class Meta:
        model = MatchMessage
        fields = (
            "id",
            "sender_id",
            "sender_name",
            "content",
            "created_at",
        )


class MatchingRoomMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000, trim_whitespace=False)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("訊息內容不可為空白。")
        return value


class MatchingRoomStanceDriftSerializer(serializers.Serializer):
    drift_value = serializers.FloatField()
    measured_at = serializers.DateTimeField()


class MatchingRoomMessagesSerializer(serializers.Serializer):
    room_id = serializers.CharField(max_length=64)
    match_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    status = serializers.CharField(max_length=20)
    other_user_id = serializers.IntegerField(required=False, allow_null=True)
    other_user_name = serializers.CharField(max_length=150, required=False, allow_null=True)
    stance_drift = MatchingRoomStanceDriftSerializer(required=False, allow_null=True)
    presence = serializers.JSONField(required=False, allow_null=True)
    absence_deadline = serializers.DateTimeField(required=False, allow_null=True)
    messages = MatchMessageSerializer(many=True)


class MatchingRoomSemanticTreeTimelineSerializer(serializers.Serializer):
    # room_id is always present — for AI sessions it's an alias of session_id
    # (there's no real "room"), so both conversation kinds can share this one
    # serializer/shape instead of branching by kind on the frontend.
    room_id = serializers.CharField(max_length=64)
    match_id = serializers.IntegerField(required=False, allow_null=True)
    session_id = serializers.CharField(max_length=64, required=False)
    topic_id = serializers.IntegerField(allow_null=True)
    asOfMessageId = serializers.CharField(max_length=64)
    asOfTimestamp = serializers.CharField(max_length=64)
    treeData = serializers.JSONField()
    anchors = serializers.JSONField()
    # Nodes this specific message created ([{id, name, stance}]), so the slider
    # can say "這則新增了哪些節點" instead of leaving the diff purely visual.
    bornNodes = serializers.JSONField(required=False)


class MatchingRoomSemanticTreeSerializer(serializers.Serializer):
    room_id = serializers.CharField(max_length=64, required=False)
    match_id = serializers.IntegerField(required=False)
    session_id = serializers.CharField(max_length=64, required=False)
    topic_id = serializers.IntegerField(required=False, allow_null=True)
    semanticMode = serializers.CharField(max_length=32, required=False)
    treeData = serializers.JSONField()
    trees = serializers.JSONField()
    anchors = serializers.JSONField()
    analysisHistory = serializers.JSONField()
    analyzedMessageIds = serializers.ListField(child=serializers.CharField())
    analyzedSourceIds = serializers.ListField(child=serializers.CharField(), required=False)
    analysisStatus = serializers.CharField(max_length=32)
    message = serializers.CharField(required=False, allow_blank=True)
    analyzedCount = serializers.IntegerField()


def _likert_7(required=True):
    return serializers.IntegerField(min_value=1, max_value=7, required=required)


class PostDialogueResponseSerializer(serializers.Serializer):
    # Meta
    topic_id = serializers.IntegerField()
    session_id = serializers.CharField(max_length=64, required=False, allow_blank=True, allow_null=True)
    room_id = serializers.CharField(max_length=64, required=False, allow_blank=True, allow_null=True)
    experiment_condition = serializers.ChoiceField(choices=["ai", "hh"])

    # C-1 (8 Likert, scrambled order; reverse scoring handled in model)
    post_likert_1 = _likert_7()
    post_likert_2 = _likert_7()
    post_likert_3 = _likert_7()
    post_likert_4 = _likert_7()
    post_likert_5 = _likert_7()
    post_likert_6 = _likert_7()
    post_likert_7 = _likert_7()
    post_likert_8 = _likert_7()

    # C-2 (6 Likert)
    exp_stance_change_1 = _likert_7()
    exp_stance_change_2 = _likert_7()
    exp_quality_1 = _likert_7()
    exp_quality_2 = _likert_7()
    exp_reflection_1 = _likert_7()
    exp_reflection_2 = _likert_7()

    # C-3 (3 Likert)
    ccnd_attention = _likert_7()
    ccnd_awareness = _likert_7()
    ccnd_influence = _likert_7()

    # C-4 (H-AI only; H-H sends null)
    opponent_judgment = serializers.ChoiceField(
        choices=[1, 2, 3],
        required=False,
        allow_null=True,
    )

    # D
    post_open_comprehension = serializers.CharField(
        min_length=50,
        max_length=4000,
        trim_whitespace=False,
        error_messages={"min_length": "D1 最少需填寫 50 字。"},
    )
    post_open_feedback = serializers.CharField(
        max_length=4000,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )

    # E
    discomfort_flag = serializers.BooleanField(default=False)
    discomfort_detail = serializers.CharField(
        max_length=2000,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        condition = attrs.get("experiment_condition")
        judgment = attrs.get("opponent_judgment")

        if condition == "ai" and judgment is None:
            raise serializers.ValidationError(
                {"opponent_judgment": "H-AI 組必須填寫對話對象判斷（C4）。"}
            )
        if condition == "hh" and judgment is not None:
            raise serializers.ValidationError(
                {"opponent_judgment": "H-H 組不填此題，請傳入 null。"}
            )

        if attrs.get("discomfort_flag") and not attrs.get("discomfort_detail", "").strip():
            raise serializers.ValidationError(
                {"discomfort_detail": "選擇「是」時請描述不適情況。"}
            )

        session_id = attrs.get("session_id")
        room_id = attrs.get("room_id")
        if condition == "ai" and (not session_id or room_id):
            raise serializers.ValidationError(
                {
                    "session_id": "H-AI 組必須只提供 session_id。",
                    "room_id": "H-AI 組不可提供 room_id。",
                }
            )
        if condition == "hh" and (not room_id or session_id):
            raise serializers.ValidationError(
                {
                    "room_id": "H-H 組必須只提供 room_id。",
                    "session_id": "H-H 組不可提供 session_id。",
                }
            )

        return attrs


class PostDialogueResponseConsentSerializer(serializers.Serializer):
    consent_confirmed = serializers.BooleanField()


class PlatformFeedbackSerializer(serializers.Serializer):
    """Part F — platform experience feedback (7 items)."""

    response_id = serializers.IntegerField()

    # F1–F5 satisfaction Likert (1–7)
    ux_matching = _likert_7()
    ux_chatroom = _likert_7()
    ux_nlp_intervention = _likert_7()
    ux_ccnd = _likert_7()
    ux_overall = _likert_7()

    # F6 NPS (0–10)
    nps_score = serializers.IntegerField(min_value=0, max_value=10)

    # F7 open-ended (optional)
    ux_improvement = serializers.CharField(
        max_length=4000,
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=False,
    )


class PlatformFeedbackOutputSerializer(serializers.ModelSerializer):
    mean_ux = serializers.SerializerMethodField()
    nps_category = serializers.SerializerMethodField()

    def get_mean_ux(self, obj):
        return obj.mean_ux()

    def get_nps_category(self, obj):
        return obj.nps_category()

    class Meta:
        model = PlatformFeedback
        fields = [
            "id",
            "response",
            "ux_matching", "ux_chatroom", "ux_nlp_intervention",
            "ux_ccnd", "ux_overall",
            "nps_score", "ux_improvement",
            "mean_ux", "nps_category",
            "created_at",
        ]
        read_only_fields = fields


class PostDialogueResponseOutputSerializer(serializers.ModelSerializer):
    s_post = serializers.SerializerMethodField()
    delta_s = serializers.FloatField(source="delta_s_value", read_only=True)
    stance_centrism = serializers.FloatField(
        source="stance_centrism_value",
        read_only=True,
    )
    pre_question_map = serializers.SerializerMethodField()

    def get_s_post(self, obj):
        return obj.s_post()

    def get_pre_question_map(self, _obj):
        from .models import POST_LIKERT_TO_PRE_QUESTION
        return POST_LIKERT_TO_PRE_QUESTION

    class Meta:
        model = PostDialogueResponse
        fields = [
            "id", "topic_id", "experiment_condition",
            "session_id", "room_id",
            "post_likert_1", "post_likert_2", "post_likert_3", "post_likert_4",
            "post_likert_5", "post_likert_6", "post_likert_7", "post_likert_8",
            "exp_stance_change_1", "exp_stance_change_2",
            "exp_quality_1", "exp_quality_2",
            "exp_reflection_1", "exp_reflection_2",
            "ccnd_attention", "ccnd_awareness", "ccnd_influence",
            "opponent_judgment",
            "post_open_comprehension", "post_open_feedback",
            "discomfort_flag", "consent_confirmed",
            "s_pre", "s_post", "delta_s", "stance_centrism",
            "pre_question_map",
            "created_at",
        ]
        read_only_fields = fields


class AccountListSerializer(serializers.ModelSerializer):
    """帳號管理清單／回傳用（唯讀）。is_researcher 由「研究者」Group 推導。"""

    is_researcher = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "is_active",
            "is_researcher",
            "is_superuser",
            "last_login",
            "date_joined",
        ]
        read_only_fields = fields

    def get_is_researcher(self, obj):
        return any(g.name == RESEARCHER_GROUP_NAME for g in obj.groups.all())


class AccountCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    password = serializers.CharField(write_only=True)
    is_researcher = serializers.BooleanField(required=False, default=False)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("這個帳號名稱已經有人用了。")
        return value

    def validate_password(self, value):
        try:
            dj_validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        is_researcher = validated_data.pop("is_researcher", False)
        user = User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )
        if is_researcher:
            group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
            user.groups.add(group)  # signal 會把 is_staff 設成 True
        return user


class AccountUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    is_researcher = serializers.BooleanField(required=False)


class PasswordResetSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        try:
            dj_validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class PlatformDisplaySettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformDisplaySetting
        fields = [
            "participant_entry_mode",
            "researcher_entry_mode",
            "match_fallback_timeout_minutes",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_match_fallback_timeout_minutes(self, value):
        if not 1 <= value <= 120:
            raise serializers.ValidationError("等待時間需介於 1 到 120 分鐘。")
        return value


class TopicDisplayOverrideSerializer(serializers.Serializer):
    """單一議題的顯示覆寫。四個欄位都選填；門檻傳 null＝還原成程式碼預設值。

    門檻驗證必須看「套用後的實際結果」而不是只看這次送來的欄位：只送
    support=3.0 但目前 oppose 是 3.5 的話，合起來是不合法的，得擋下來。
    """

    visible_to_participant = serializers.BooleanField(required=False)
    visible_to_researcher = serializers.BooleanField(required=False)
    support_threshold = serializers.FloatField(required=False, allow_null=True)
    oppose_threshold = serializers.FloatField(required=False, allow_null=True)

    def validate(self, attrs):
        from .display_settings import default_stance_thresholds, get_stance_thresholds

        topic_id = self.context["topic_id"]
        survey_config = get_dialogue_survey(topic_id) or {}
        scale_config = survey_config.get("scale", {})
        scale_min = float(scale_config.get("min", 1))
        scale_max = float(scale_config.get("max", 7))

        current_support, current_oppose = get_stance_thresholds(topic_id=topic_id)
        default_support, default_oppose = default_stance_thresholds(topic_id=topic_id)

        def resolve(field, current, default):
            if field not in attrs:
                return current
            value = attrs[field]
            return default if value is None else float(value)

        support = resolve("support_threshold", current_support, default_support)
        oppose = resolve("oppose_threshold", current_oppose, default_oppose)

        for label, value in (("支持門檻", support), ("反對門檻", oppose)):
            if not scale_min <= value <= scale_max:
                raise serializers.ValidationError(
                    f"{label}需介於 {scale_min} 到 {scale_max} 之間。"
                )

        if oppose >= support:
            raise serializers.ValidationError("反對門檻必須小於支持門檻。")

        return attrs


class DialogueEntrySerializer(serializers.Serializer):
    """混合入口的輸入。

    刻意不收 topic_title / topic_description / user_initial_argument——
    那些一律由後端從 TOPIC_CONFIGS 與問卷 Q9 補齊，少一組可被客戶端
    竄改的輸入。
    """

    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
