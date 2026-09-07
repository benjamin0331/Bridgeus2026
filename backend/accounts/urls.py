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
    path(
        "email-verification/request/",
        views.EmailVerificationRequestView.as_view(),
    ),
    path(
        "email-verification/confirm/",
        views.EmailVerificationConfirmView.as_view(),
    ),
    path(
        "me/email/verify/request/",
        views.MeEmailVerificationRequestView.as_view(),
    ),
    path(
        "me/email/verify/confirm/",
        views.MeEmailVerificationConfirmView.as_view(),
    ),
]
