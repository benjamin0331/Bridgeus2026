from django.urls import path
from . import views

urlpatterns = [
    path('conversations/', views.AIConversationListCreate.as_view()),
    path('conversations/<int:pk>/', views.AIConversationDetail.as_view()),
    path('dialogue/topics/', views.DialogueTopicListView.as_view()),
    path('dialogue/sessions/', views.DialogueSessionCreateView.as_view()),
    path(
        'dialogue/sessions/<str:session_id>/reply/',
        views.DialogueSessionReplyView.as_view(),
    ),
]
