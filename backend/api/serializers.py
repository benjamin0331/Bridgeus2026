from rest_framework import serializers

from .dialogue_topics import get_dialogue_survey
from .models import AIConversation, MatchMessage


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


class MatchingRoomMessagesSerializer(serializers.Serializer):
    room_id = serializers.CharField(max_length=64)
    match_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    status = serializers.CharField(max_length=20)
    other_user_id = serializers.IntegerField(required=False, allow_null=True)
    other_user_name = serializers.CharField(max_length=150, required=False, allow_null=True)
    messages = MatchMessageSerializer(many=True)
