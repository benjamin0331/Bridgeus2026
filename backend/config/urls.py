from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Auth (M1)
    path('api/v1/auth/', include('apps.auth_module.urls')),

    # Topic & Stance (M2)
    path('api/v1/stance/', include('apps.stance.urls')),

    # Matching & AI Agent (M3)
    path('api/v1/matching/', include('apps.matching.urls')),

    # Dialogue Room (M4) — HTTP endpoints; WebSocket via routing.py
    path('api/v1/dialogue/', include('apps.dialogue.urls')),

    # NLP & CCND (M5)
    path('api/v1/nlp/', include('apps.nlp_analysis.urls')),

    # Summary & KB (M6)
    path('api/v1/summary/', include('apps.summary.urls')),

    # JWT token refresh
    path('api/v1/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
