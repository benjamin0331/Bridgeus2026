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
    # staff-only: M6 觀點知識庫 Step 4 人工終審
    path('summary/viewpoints/', views.ViewpointReviewListView.as_view()),
    path(
        'summary/viewpoints/<int:pk>/review/',
        views.ViewpointReviewDecisionView.as_view(),
    ),
    # 知識庫首頁：所有登入使用者都能看的公開內容
    path('summary/viewpoints/highlights/', views.KnowledgeBaseHighlightsView.as_view()),
    path('summary/viewpoints/browse/', views.KnowledgeBaseViewpointBrowseView.as_view()),
    path(
        'summary/viewpoints/<int:pk>/conversation/',
        views.KnowledgeBaseConversationDetailView.as_view(),
    ),
    path('summary/videos/', views.VideoRecommendationListView.as_view()),
    path('accounts/', views.AccountListCreateView.as_view()),
    path('accounts/<int:pk>/', views.AccountDetailView.as_view()),
    path('accounts/<int:pk>/reset-password/', views.AccountPasswordResetView.as_view()),
    path('settings/display/', views.DisplaySettingsView.as_view()),
    path(
        'settings/display/topics/<int:topic_id>/',
        views.DisplaySettingsTopicView.as_view(),
    ),
    path('dialogue/topics/', views.DialogueTopicListView.as_view()),
    path('dialogue/topics/trending/', views.DialogueTopicTrendingView.as_view()),
    path(
        'dialogue/topics/<int:topic_id>/survey/',
        views.DialogueSurveyView.as_view(),
    ),
    path(
        'dialogue/topics/<int:topic_id>/stance-profile/',
        views.DialogueStanceProfileView.as_view(),
    ),
    path('dialogue/entry/', views.DialogueEntryView.as_view()),
    path('dialogue/entry/fallback/', views.DialogueEntryFallbackView.as_view()),
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
    path('matching/godot-survey/', views.GodotSurveyView.as_view()),
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
    path('message-reactions/', views.MessageReactionView.as_view()),
    path('issues/', views.IssueListCreateView.as_view()),
    path('me/', views.MeView.as_view()),
    path('titles/me/', views.TitleMeView.as_view()),
    path(
        'issues/<int:issue_id>/reactions/',
        views.IssueReactionsView.as_view(),
    ),
    path('godot/tickets/', views.GodotTicketIssueView.as_view()),
    path('godot/tickets/redeem/', views.GodotTicketRedeemView.as_view()),
    path('godot/match-rooms/', views.GodotMatchRoomView.as_view()),
]
