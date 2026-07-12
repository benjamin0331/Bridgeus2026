from django.urls import path
from . import views

urlpatterns = [
    path('conversations/', views.AIConversationListCreate.as_view()),
    path('conversations/<int:pk>/', views.AIConversationDetail.as_view()),
    path(
        'history/conversations/',
        views.HistoryConversationListView.as_view(),
    ),
    path(
        'history/conversations/<str:kind>/<str:conversation_id>/',
        views.HistoryConversationDetailView.as_view(),
    ),
    path(
        'history/conversations/<str:kind>/<str:conversation_id>/semantic-tree/timeline/',
        views.HistoryConversationSemanticTreeTimelineView.as_view(),
    ),
    path(
        'history/conversations/<str:kind>/<str:conversation_id>/semantic-tree/analyze/',
        views.HistoryConversationSemanticTreeAnalyzeView.as_view(),
    ),
    # staff-only: research metrics across any conversation / any subject
    path(
        'history/conversations/<str:kind>/<str:conversation_id>/semantic-tree/snapshot-analysis/',
        views.CCNDSnapshotAnalysisView.as_view(),
    ),
    # participant-facing: their OWN concept expansion, gated behind the M6 flow
    path(
        'history/conversations/<str:kind>/<str:conversation_id>/ccnd-insights/',
        views.CCNDInsightsView.as_view(),
    ),
    path('dialogue/topics/', views.DialogueTopicListView.as_view()),
    path(
        'dialogue/topics/<int:topic_id>/survey/',
        views.DialogueSurveyView.as_view(),
    ),
    path('dialogue/sessions/', views.DialogueSessionCreateView.as_view()),
    path(
        'dialogue/sessions/latest/',
        views.DialogueSessionLatestView.as_view(),
    ),
    path(
        'dialogue/sessions/<str:session_id>/',
        views.DialogueSessionDetailView.as_view(),
    ),
    path(
        'dialogue/sessions/<str:session_id>/reply/',
        views.DialogueSessionReplyView.as_view(),
    ),
    path(
        'dialogue/sessions/<str:session_id>/semantic-tree/',
        views.DialogueSessionSemanticTreeView.as_view(),
    ),
    path(
        'dialogue/sessions/<str:session_id>/semantic-tree/analyze/',
        views.DialogueSessionSemanticTreeAnalyzeView.as_view(),
    ),
    path('matching/join/', views.MatchingJoinView.as_view()),
    path('matching/status/', views.MatchingStatusView.as_view()),
    path('matching/cancel/', views.MatchingCancelView.as_view()),
    path(
        'matching/rooms/<str:room_id>/messages/',
        views.MatchingRoomMessagesView.as_view(),
    ),
    path(
        'matching/rooms/<str:room_id>/semantic-tree/',
        views.MatchingRoomSemanticTreeView.as_view(),
    ),
    path(
        'matching/rooms/<str:room_id>/semantic-tree/timeline/',
        views.MatchingRoomSemanticTreeTimelineView.as_view(),
    ),
    path(
        'matching/rooms/<str:room_id>/semantic-tree/analyze/',
        views.MatchingRoomSemanticTreeAnalyzeView.as_view(),
    ),
    path(
        'matching/rooms/<str:room_id>/leave/',
        views.MatchingRoomLeaveView.as_view(),
    ),
    path(
        'post-questionnaire/',
        views.PostDialogueResponseView.as_view(),
    ),
    path(
        'post-questionnaire/<int:response_id>/consent/',
        views.PostDialogueResponseConsentView.as_view(),
    ),
    path(
        'platform-feedback/',
        views.PlatformFeedbackView.as_view(),
    ),
    path('guest/', views.GuestLoginView.as_view()),
    path('issues/', views.IssueListCreateView.as_view()),
]
