from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegistrationView.as_view()),
    path("consent/", views.ConsentDocumentView.as_view()),
    path(
        "password-reset/request/",
        views.PasswordResetRequestView.as_view(),
    ),
    path(
        "password-reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
    ),
]
