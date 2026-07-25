from django.contrib import admin

from .models import (
    AIConversation,
    CCNDTimelineUnlock,
    DialogueMatch,
    MatchAISuggestion,
    MatchMessage,
    MatchQueueEntry,
    MatchStanceDrift,
    MessageReaction,
    PostDialogueResponse,
    UserStanceProfile,
)


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "session_id",
        "dialogue_phase",
        "created_at",
    )
    list_filter = ("topic_id", "dialogue_phase")
    search_fields = ("user__username", "session_id", "user_prompt", "ai_response")


@admin.register(UserStanceProfile)
class UserStanceProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "stance_score",
        "stance_category",
        "has_q9_embedding",
        "updated_at",
    )
    list_filter = ("topic_id", "stance_category")
    search_fields = ("user__username",)

    def has_q9_embedding(self, obj):
        return obj.q9_embedding is not None
    has_q9_embedding.boolean = True


@admin.register(MatchQueueEntry)
class MatchQueueEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "stance_score",
        "status",
        "waiting_started_at",
        "matched_at",
        "cancelled_at",
    )
    list_filter = ("topic_id", "status")
    search_fields = ("user__username", "match__room_id")


@admin.register(DialogueMatch)
class DialogueMatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "topic_id",
        "user_a",
        "user_b",
        "status",
        "match_score",
        "matching_algorithm_version",
        "room_id",
        "created_at",
    )
    list_filter = ("topic_id", "status")
    search_fields = ("user_a__username", "user_b__username", "room_id")


@admin.register(MatchMessage)
class MatchMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "match", "sender", "emotion_score", "dialogue_phase", "created_at")
    list_filter = ("dialogue_phase",)
    search_fields = ("match__room_id", "sender__username", "content")


@admin.register(MatchAISuggestion)
class MatchAISuggestionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "match",
        "user",
        "category",
        "user_action",
        "trigger_score",
        "created_at",
    )
    list_filter = ("category", "user_action")
    search_fields = (
        "match__room_id",
        "user__username",
        "original_content",
        "suggested_content",
        "final_content",
    )


@admin.register(MatchStanceDrift)
class MatchStanceDriftAdmin(admin.ModelAdmin):
    list_display = ("id", "match", "user", "drift_value", "measured_at")
    search_fields = ("match__room_id", "user__username")


@admin.register(PostDialogueResponse)
class PostDialogueResponseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "experiment_condition",
        "s_pre",
        "s_post_display",
        "delta_s_value",
        "stance_centrism_value",
        "consent_confirmed",
        "created_at",
    )
    list_filter = ("topic_id", "experiment_condition", "consent_confirmed")
    search_fields = ("user__username", "session_id", "room_id")

    @admin.display(description="s_post")
    def s_post_display(self, obj):
        return obj.s_post()


@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "target_type",
        "target_id",
        "value",
        "topic_id",
        "conversation_id",
        "updated_at",
    )
    list_filter = ("target_type", "value", "topic_id")
    search_fields = ("user__username", "conversation_id")


@admin.register(CCNDTimelineUnlock)
class CCNDTimelineUnlockAdmin(admin.ModelAdmin):
    """Researcher escape hatch: grant a participant CCND-timeline access before
    they have finished the M6 questionnaire flow. Adding a row here unlocks it."""

    list_display = ("id", "user", "kind", "conversation_id", "granted_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "conversation_id", "reason")

    def save_model(self, request, obj, form, change):
        if obj.granted_by_id is None:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)
