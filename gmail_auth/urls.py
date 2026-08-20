"""
gmail_auth/urls.py
"""

from django.urls import path

from gmail_auth import views
from gmail_auth.cbv import views as cbv

urlpatterns = [
    path("login/", views.gmail_login, name="gmail_login"),
    path("refresh/<int:pk>/", views.refresh_token, name="refresh_google_token"),
    path("callback/", views.gmail_callback, name="gmail_callback"),
    path(
        "view-gmail-servers/", views.view_gmail_records, name="gmail_view_records"
    ),
    path("gmail-server-nav/", cbv.ServerNav.as_view(), name="gmail_server_nav"),
    path("gmail-server-list/", cbv.ServerList.as_view(), name="gmail_server_list"),
    path(
        "gmail-server-form/", cbv.ServerForm.as_view(), name="gmail_server_create"
    ),
    path(
        "gmail-server-form/<int:pk>/",
        cbv.ServerForm.as_view(),
        name="gmail_server_change",
    ),
]
