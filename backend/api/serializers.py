from rest_framework import serializers

from .dialogue_topics import get_dialogue_survey
from .models import AIConversation, DiscomfortReport, MatchMessage, PostDialogueResponse


class AIConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversation
        fields = '__all__'


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

        if not attrs.get("session_id") and not attrs.get("room_id"):
            raise serializers.ValidationError(
                "session_id 與 room_id 至少需提供其中一個。"
            )

        return attrs


class PostDialogueResponseConsentSerializer(serializers.Serializer):
    consent_confirmed = serializers.BooleanField()


class PostDialogueResponseOutputSerializer(serializers.ModelSerializer):
    s_post = serializers.SerializerMethodField()
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
            "s_post", "pre_question_map",
            "created_at",
        ]
        read_only_fields = fields
