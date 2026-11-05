"""
vertex/urls.py
"""

from django.urls import path

from vertex import views

urlpatterns = [
    path("projects/", views.project_list, name="vertex-projects"),
    path("projects/new/", views.project_create, name="vertex-project-new"),
    path("projects/<int:pk>/", views.project_detail, name="vertex-project"),
    path("projects/<int:pk>/edit/", views.project_edit, name="vertex-project-edit"),
    path("projects/<int:pk>/members/add/", views.member_add, name="vertex-member-add"),
    path(
        "projects/<int:pk>/members/<int:member_id>/remove/",
        views.member_remove,
        name="vertex-member-remove",
    ),
    path("projects/<int:pk>/comments/add/", views.comment_add, name="vertex-comment-add"),
    path("projects/<int:pk>/documents/add/", views.document_add, name="vertex-document-add"),
    path(
        "projects/<int:pk>/documents/<int:doc_id>/download/",
        views.document_download,
        name="vertex-document-download",
    ),
    path(
        "projects/<int:pk>/documents/<int:doc_id>/delete/",
        views.document_delete,
        name="vertex-document-delete",
    ),
    path("projects/<int:pk>/calls/add/", views.call_add, name="vertex-call-add"),
]
