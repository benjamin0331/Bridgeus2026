from rest_framework import serializers
from .models import AIConversation


class AIConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversation
        fields = '__all__'


class DialogueTopicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=500)
    date = serializers.CharField(max_length=32)


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
