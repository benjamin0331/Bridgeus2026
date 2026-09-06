from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegistrationView.as_view()),
    path("consent/", views.ConsentDocumentView.as_view()),
]
