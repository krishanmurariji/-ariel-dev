from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from ...api_views.auth.views import LoginAPIView, PasswordResetAPIView

urlpatterns = [
    path("login/", LoginAPIView.as_view()),
    path("refresh/", TokenRefreshView.as_view(), name="api-token-refresh"),
    path("reset-password/", PasswordResetAPIView.as_view(), name="api-reset-password"),
]
