from django.contrib import admin

from .models import (
    AIConversation,
    CCNDTimelineUnlock,
    DialogueEntryAssignment,
    DialogueMatch,
    DialogueSessionRecord,
    IssueReaction,
    MatchAISuggestion,
    MatchInputGateStat,
    MatchMessage,
    MatchQueueEntry,
    MatchStanceDrift,
    MessageReaction,
    PlatformDisplaySetting,
    PostDialogueResponse,
    Title,
    TopicDisplayOverride,
    UserStanceProfile,
    UserTitle,
)

from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

User = get_user_model()

# auth 在 INSTALLED_APPS 早於 api，預設 UserAdmin 先註冊了 User，這裡換成
# 加了 last_login／is_active 到列表的版本，方便 Supervisor 一眼看帳號狀態。
admin.site.unregister(User)


@admin.register(User)
class BridgeUsUserAdmin(UserAdmin):
    list_display = UserAdmin.list_display + ("is_active", "last_login")


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
    list_filter = ("topic_id", "dialogue_phase", "ai_turn_is_question")
    search_fields = ("user__username", "session_id", "user_prompt", "ai_response")


# Input gate 計數的檢視入口。研究端據此決定樣本排除，系統不自動排除。
@admin.register(DialogueSessionRecord)
class DialogueSessionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "session_id",
        "user",
        "topic_id",
        "status",
        "invalid_input_count",
        "invalid_input_total",
        "input_attempt_total",
        "invalid_ratio",
        "substantive_turn_count",
        "last_activity_at",
    )
    list_filter = ("topic_id", "status")
    search_fields = ("user__username", "session_id", "topic_title")


@admin.register(MatchInputGateStat)
class MatchInputGateStatAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "match",
        "user",
        "invalid_input_count",
        "invalid_input_total",
        "input_attempt_total",
        "invalid_ratio",
        "substantive_turn_count",
        "updated_at",
    )
    search_fields = ("user__username", "match__room_id")


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


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "color")
    search_fields = ("name",)


@admin.register(UserTitle)
class UserTitleAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "is_selected", "unlocked_at")
    list_filter = ("is_selected",)
    search_fields = ("user__username", "title__name")


@admin.register(IssueReaction)
class IssueReactionAdmin(admin.ModelAdmin):
    list_display = ("id", "issue", "reactor", "emoji_index", "created_at")
    search_fields = ("issue__title", "reactor__username")


@admin.register(PlatformDisplaySetting)
class PlatformDisplaySettingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "participant_entry_mode",
        "researcher_entry_mode",
        "match_fallback_timeout_minutes",
        "updated_by",
        "updated_at",
    )


@admin.register(TopicDisplayOverride)
class TopicDisplayOverrideAdmin(admin.ModelAdmin):
    list_display = (
        "topic_id",
        "visible_to_participant",
        "visible_to_researcher",
        "support_threshold",
        "oppose_threshold",
        "updated_by",
        "updated_at",
    )


@admin.register(DialogueEntryAssignment)
class DialogueEntryAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "route",
        "stance_category",
        "stance_score",
        "fallback_accepted_at",
        "assigned_at",
    )
    list_filter = ("route", "stance_category", "topic_id")
    search_fields = ("user__username",)
