from django.contrib import admin

from .models import (
    AIConversation,
    DialogueMatch,
    MatchMessage,
    MatchQueueEntry,
    UserStanceProfile,
)


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user_prompt", "created_at")
    search_fields = ("user_prompt", "ai_response")


@admin.register(UserStanceProfile)
class UserStanceProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "topic_id",
        "stance_score",
        "stance_category",
        "updated_at",
    )
    list_filter = ("topic_id", "stance_category")
    search_fields = ("user__username",)


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
        "room_id",
        "created_at",
    )
    list_filter = ("topic_id", "status")
    search_fields = ("user_a__username", "user_b__username", "room_id")


@admin.register(MatchMessage)
class MatchMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "match", "sender", "created_at")
    search_fields = ("match__room_id", "sender__username", "content")
