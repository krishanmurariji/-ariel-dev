"""
vertex/urls.py
"""

from django.urls import path
from django.views.generic import RedirectView

from vertex import views, views_minutes, views_reviews

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="vertex-projects"), name="vertex-home"),
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
    # reviews
    path("reviews/", views_reviews.review_list, name="vertex-reviews"),
    path("reviews/new/", views_reviews.review_create, name="vertex-review-new"),
    path("reviews/<int:pk>/", views_reviews.review_detail, name="vertex-review"),
    path("reviews/<int:pk>/schedule/", views_reviews.review_schedule, name="vertex-review-schedule"),
    path("reviews/<int:pk>/respond/", views_reviews.review_respond, name="vertex-review-respond"),
    path("reviews/<int:pk>/submit/", views_reviews.review_submit, name="vertex-review-submit"),
    # minutes
    path("minutes/", views_minutes.minutes_list, name="vertex-minutes"),
    path("minutes/new/", views_minutes.minutes_create, name="vertex-minute-new"),
    path("minutes/<int:pk>/", views_minutes.minutes_detail, name="vertex-minute"),
    path("minutes/<int:pk>/edit/", views_minutes.minutes_edit, name="vertex-minute-edit"),
    path("minutes/<int:pk>/generate/", views_minutes.minutes_generate, name="vertex-minute-generate"),
    path("minutes/<int:pk>/approve/", views_minutes.minutes_approve, name="vertex-minute-approve"),
    path("minutes/<int:pk>/send/", views_minutes.minutes_send, name="vertex-minute-send"),
    path("minutes/<int:pk>/delete/", views_minutes.minutes_delete, name="vertex-minute-delete"),
]
