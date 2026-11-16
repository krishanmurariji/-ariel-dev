"""
vertex/urls.py
"""

from django.urls import path
from django.views.generic import RedirectView

from vertex import (
    cbv,
    cbv_appraisals,
    cbv_performance,
    views,
    views_appraisals,
    views_minutes,
    views_performance,
    views_resources,
    views_reviews,
)

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
    path("projects/<int:project_id>/calls/<int:pk>/link/", views.call_generate_link, name="vertex-call-link"),
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
    # performance: feedback
    path("performance-feedback/", cbv_performance.FeedbackView.as_view(), name="vertex-feedback"),
    path("performance-feedback/nav/", cbv_performance.FeedbackNavView.as_view(), name="vertex-feedback-nav"),
    path("performance-feedback/list/", cbv_performance.FeedbackList.as_view(), name="vertex-feedback-list"),
    path("performance-feedback/new/", cbv_performance.FeedbackFormView.as_view(), name="vertex-feedback-new"),
    path("performance-feedback/<int:pk>/", views_performance.feedback_detail, name="vertex-feedback-detail"),
    path("performance-feedback/<int:pk>/edit/", cbv_performance.FeedbackFormView.as_view(), name="vertex-feedback-edit"),
    path("performance-feedback/<int:pk>/submit/", views_performance.feedback_submit, name="vertex-feedback-submit"),
    path("performance-feedback/<int:pk>/revision/", views_performance.feedback_request_revision, name="vertex-feedback-revision"),
    path("performance-feedback/<int:pk>/approve/", views_performance.feedback_approve, name="vertex-feedback-approve"),
    path("performance-feedback/<int:pk>/publish/", views_performance.feedback_publish, name="vertex-feedback-publish"),
    # performance reports
    path("performance-reports/", cbv_performance.ReportsView.as_view(), name="vertex-performance"),
    path("performance-reports/nav/", cbv_performance.ReportsNavView.as_view(), name="vertex-performance-nav"),
    path("performance-reports/list/", cbv_performance.ReportsList.as_view(), name="vertex-performance-list"),
    path("performance-reports/generate/", cbv_performance.GenerateReportFormView.as_view(), name="vertex-performance-generate"),
    path("performance-reports/<int:pk>/", views_performance.performance_report, name="vertex-performance-report"),
    path("performance-reports/<int:pk>/publish/", views_performance.performance_report_publish, name="vertex-performance-publish"),
    # improvement plans
    path("improvement-plans/", cbv_performance.PipView.as_view(), name="vertex-pip"),
    path("improvement-plans/nav/", cbv_performance.PipNavView.as_view(), name="vertex-pip-nav"),
    path("improvement-plans/list/", cbv_performance.PipList.as_view(), name="vertex-pip-list"),
    path("improvement-plans/new/", cbv_performance.PipFormView.as_view(), name="vertex-pip-new"),
    path("improvement-plans/<int:pk>/edit/", cbv_performance.PipFormView.as_view(), name="vertex-pip-edit"),
    # appraisal cycles
    path("appraisal-cycles/", cbv_appraisals.CyclesView.as_view(), name="vertex-cycles"),
    path("appraisal-cycles/nav/", cbv_appraisals.CyclesNavView.as_view(), name="vertex-cycle-nav"),
    path("appraisal-cycles/list/", cbv_appraisals.CyclesList.as_view(), name="vertex-cycle-list"),
    path("appraisal-cycles/new/", cbv_appraisals.CycleFormView.as_view(), name="vertex-cycle-new"),
    path("appraisal-cycles/<int:pk>/edit/", cbv_appraisals.CycleFormView.as_view(), name="vertex-cycle-edit"),
    path("appraisal-cycles/<int:pk>/activate/", views_appraisals.cycle_activate, name="vertex-cycle-activate"),
    path("appraisal-cycles/<int:pk>/close/", views_appraisals.cycle_close, name="vertex-cycle-close"),
    # appraisals
    path("appraisals/", cbv_appraisals.AppraisalsView.as_view(), name="vertex-appraisals"),
    path("appraisals/nav/", cbv_appraisals.AppraisalsNavView.as_view(), name="vertex-appraisal-nav"),
    path("appraisals/list/", cbv_appraisals.AppraisalsList.as_view(), name="vertex-appraisal-list"),
    path("appraisals/<int:pk>/", views_appraisals.appraisal_detail, name="vertex-appraisal"),
    path("appraisals/<int:pk>/self/", cbv_appraisals.SelfAppraisalFormView.as_view(), name="vertex-appraisal-self"),
    path("appraisals/<int:pk>/manager/", cbv_appraisals.ManagerAppraisalFormView.as_view(), name="vertex-appraisal-manager"),
    path("appraisals/<int:pk>/release/", cbv_appraisals.ReleaseAppraisalFormView.as_view(), name="vertex-appraisal-release"),
    # goals
    path("goals/", cbv_appraisals.GoalsView.as_view(), name="vertex-goals"),
    path("goals/nav/", cbv_appraisals.GoalsNavView.as_view(), name="vertex-goal-nav"),
    path("goals/list/", cbv_appraisals.GoalsList.as_view(), name="vertex-goal-list"),
    path("goals/new/", cbv_appraisals.GoalFormView.as_view(), name="vertex-goal-new"),
    path("goals/<int:pk>/edit/", cbv_appraisals.GoalFormView.as_view(), name="vertex-goal-edit"),
    path("goals/<int:pk>/progress/", cbv_appraisals.GoalProgressFormView.as_view(), name="vertex-goal-progress"),
    path("goals/<int:pk>/delete/", views_appraisals.goal_delete, name="vertex-goal-delete"),
    # the employee's own view
    path("my-performance/", views_performance.my_performance, name="vertex-my-performance"),
]
