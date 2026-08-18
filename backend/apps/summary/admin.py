from django.contrib import admin

from .models import VideoRecommendation


@admin.register(VideoRecommendation)
class VideoRecommendationAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "topic_id",
        "stance_direction",
        "is_published",
        "display_order",
        "created_at",
    )
    list_filter = ("is_published", "topic_id", "stance_direction")
    search_fields = ("title", "description", "url")
    ordering = ("display_order", "-created_at")
