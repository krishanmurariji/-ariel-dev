"""
vertex/urls.py
"""

from django.urls import path

from vertex import views

urlpatterns = [
    path("roles/", views.roles_view, name="vertex-roles"),
    path("roles/<int:employee_id>/update/", views.role_update, name="vertex-role-update"),
]
