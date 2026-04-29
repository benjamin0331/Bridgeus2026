from django.urls import path
from . import views

urlpatterns = [
    path('conversations/', views.AIConversationListCreate.as_view()),
    path('conversations/<int:pk>/', views.AIConversationDetail.as_view()),
    path('dialogue/topics/', views.DialogueTopicListView.as_view()),
    path(
        'dialogue/topics/<int:topic_id>/survey/',
        views.DialogueSurveyView.as_view(),
    ),
    path('dialogue/sessions/', views.DialogueSessionCreateView.as_view()),
    path(
        'dialogue/sessions/<str:session_id>/reply/',
        views.DialogueSessionReplyView.as_view(),
    ),
    path('matching/join/', views.MatchingJoinView.as_view()),
    path('matching/status/', views.MatchingStatusView.as_view()),
    path('matching/cancel/', views.MatchingCancelView.as_view()),
]
