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
        child=serializers.IntegerField(min_value=1, max_value=5),
        required=False,
    )
    user_initial_argument = serializers.CharField(required=False, allow_blank=True)


class DialogueReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)
