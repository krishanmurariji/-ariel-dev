"""
vertex/urls.py
"""

from django.urls import path
from django.views.generic import RedirectView

from vertex import cbv, views, views_minutes, views_resources, views_reviews

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="vertex-projects"), name="vertex-home"),
    path("projects/", cbv.ProjectsView.as_view(), name="vertex-projects"),
    path("projects/nav/", cbv.ProjectsNavView.as_view(), name="vertex-project-nav"),
    path("projects/list/", cbv.ProjectsList.as_view(), name="vertex-project-list"),
    path("projects/card/", cbv.ProjectsCard.as_view(), name="vertex-project-card"),
    path("projects/new/", cbv.ProjectFormView.as_view(), name="vertex-project-new"),
    path("projects/<int:pk>/", views.project_detail, name="vertex-project"),
    path("projects/<int:pk>/edit/", cbv.ProjectFormView.as_view(), name="vertex-project-edit"),
    path("projects/<int:project_id>/members/add/", cbv.ProjectMemberFormView.as_view(), name="vertex-member-add"),
    path(
        "projects/<int:pk>/members/<int:member_id>/remove/",
        views.member_remove,
        name="vertex-member-remove",
    ),
    path("projects/<int:pk>/comments/add/", views.comment_add, name="vertex-comment-add"),
    path("projects/<int:project_id>/documents/add/", cbv.ProjectDocumentFormView.as_view(), name="vertex-document-add"),
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
    path("projects/<int:project_id>/calls/add/", cbv.ProjectCallFormView.as_view(), name="vertex-call-add"),
    # status updates
    path("status-updates/", cbv.StatusUpdatesView.as_view(), name="vertex-status"),
    path("status-updates/nav/", cbv.StatusUpdatesNavView.as_view(), name="vertex-status-nav"),
    path("status-updates/list/", cbv.StatusUpdatesList.as_view(), name="vertex-status-list"),
    path("status-updates/new/", cbv.StatusUpdateFormView.as_view(), name="vertex-status-new"),
    # resources: hiring requests and resource visibility
    path("hiring/", cbv.ResourceRequestsView.as_view(), name="vertex-hiring"),
    path("hiring/nav/", cbv.ResourceRequestsNavView.as_view(), name="vertex-hiring-nav"),
    path("hiring/list/", cbv.ResourceRequestsList.as_view(), name="vertex-hiring-list"),
    path("hiring/new/", cbv.ResourceRequestFormView.as_view(), name="vertex-hiring-new"),
    path("hiring/<int:pk>/", views_resources.resource_request_detail, name="vertex-resource-request"),
    path("hiring/<int:pk>/status/", views_resources.resource_request_status, name="vertex-resource-request-status"),
    path("hiring/<int:pk>/comment/", views_resources.resource_request_comment, name="vertex-resource-request-comment"),
    path("resources/", views_resources.resource_visibility, name="vertex-resources"),
    # reviews
    path("reviews/", cbv.ReviewsView.as_view(), name="vertex-reviews"),
    path("reviews/nav/", cbv.ReviewsNavView.as_view(), name="vertex-review-nav"),
    path("reviews/list/", cbv.ReviewsList.as_view(), name="vertex-review-list"),
    path("reviews/new/", cbv.ReviewRequestFormView.as_view(), name="vertex-review-new"),
    path("reviews/<int:pk>/", views_reviews.review_detail, name="vertex-review"),
    path("reviews/<int:pk>/schedule/", views_reviews.review_schedule, name="vertex-review-schedule"),
    path("reviews/<int:pk>/respond/", views_reviews.review_respond, name="vertex-review-respond"),
    path("reviews/<int:pk>/submit/", views_reviews.review_submit, name="vertex-review-submit"),
    # minutes
    path("minutes/", cbv.MinutesView.as_view(), name="vertex-minutes"),
    path("minutes/nav/", cbv.MinutesNavView.as_view(), name="vertex-minutes-nav"),
    path("minutes/list/", cbv.MinutesList.as_view(), name="vertex-minutes-list"),
    path("minutes/new/", cbv.MinutesFormView.as_view(), name="vertex-minute-new"),
    path("minutes/<int:pk>/", views_minutes.minutes_detail, name="vertex-minute"),
    path("minutes/<int:pk>/edit/", cbv.MinutesFormView.as_view(), name="vertex-minute-edit"),
    path("minutes/<int:pk>/generate/", views_minutes.minutes_generate, name="vertex-minute-generate"),
    path("minutes/<int:pk>/approve/", views_minutes.minutes_approve, name="vertex-minute-approve"),
    path("minutes/<int:pk>/send/", views_minutes.minutes_send, name="vertex-minute-send"),
    path("minutes/<int:pk>/delete/", views_minutes.minutes_delete, name="vertex-minute-delete"),
]
