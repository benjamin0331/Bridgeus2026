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
        'history/conversations/<str:kind>/<str:conversation_id>/semantic-tree/analyze/',
        views.HistoryConversationSemanticTreeAnalyzeView.as_view(),
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
        'matching/rooms/<str:room_id>/semantic-tree/analyze/',
        views.MatchingRoomSemanticTreeAnalyzeView.as_view(),
    ),
    path(
        'matching/rooms/<str:room_id>/leave/',
        views.MatchingRoomLeaveView.as_view(),
    ),
]
