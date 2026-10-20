"""
offboarding/urls.py

This module is used to register url mappings to functions
"""

from django.apps import apps
from django.urls import path
from django.views.generic import RedirectView

from horilla_views.cbv_methods import check_feature_enabled
from offboarding import dashboard as ob_dashboard
from offboarding import (
    exit_pages,
    exit_panel,
    exit_settings,
    exit_survey_pages,
    exit_survey_settings,
    exit_task_settings,
    views,
)
from offboarding.cbv import exit_process, resignation, resignation_tab
from offboarding.models import OffboardingGeneralSetting

urlpatterns = [
    path(
        "",
        RedirectView.as_view(pattern_name="offboarding-dashboard", permanent=False),
        name="offboarding-root",
    ),
    path(
        "individual-resignation-tab-list/<int:pk>/",
        resignation_tab.ResignationTabView.as_view(),
        name="individual-resignation-tab-list",
    ),
    # The kanban pipeline was replaced by the Exit Process page; old links
    # and bookmarks land there.
    path(
        "offboarding-pipeline/",
        RedirectView.as_view(pattern_name="exit-process", query_string=False),
        name="offboarding-pipeline",
    ),
    # path("create-offboarding/", views.create_offboarding, name="create-offboarding"),
    path(
        "create-offboarding/",
        exit_process.OffboardingCreateFormView.as_view(),
        name="create-offboarding",
    ),
    path(
        "update-offboarding/<int:pk>/",
        exit_process.OffboardingCreateFormView.as_view(),
        name="update-offboarding",
    ),
    path(
        "delete-offboarding/<int:id>/",
        views.delete_offboarding,
        name="delete-offboarding",
    ),
    # path(
    #     "create-offboarding-stage", views.create_stage, name="create-offboarding-stage"
    # ),
    path(
        "get-offboarding-stage/<int:offboarding_id>/",
        exit_process.OffboardingPipelineStage.as_view(),
        name="get-offboarding-stage",
    ),
    path(
        "get-offboarding-kanban-stage/<int:pk>/",
        exit_process.OffboardingKanbanView.as_view(),
        name="get-offboarding-kanban-stage",
    ),
    path(
        "offboarding-kanban-required-task-check/",
        exit_process.OffboardingKanbanRequiredTaskCheck.as_view(),
        name="offboarding-kanban-required-task-check",
    ),
    path(
        "get-offboarding-employees-cbv/",
        exit_process.OffboardingEmployeeList.as_view(),
        name="get-offboarding-employees-cbv",
    ),
    path(
        "get-offboarding-tab/",
        exit_process.PipeLineTabView.as_view(),
        name="get-offboarding-tab",
    ),
    path(
        "create-offboarding-stage/",
        exit_process.OffboardingStageFormView.as_view(),
        name="create-offboarding-stage",
    ),
    path(
        "create-offboarding-stage/<int:pk>/",
        exit_process.OffboardingStageFormView.as_view(),
        name="create-offboarding-stage",
    ),
    path(
        "update-stage-sequence/<int:pk>/",
        views.update_stage_order,
        name="update-stage-sequence",
    ),
    path(
        "offboarding-pipeline-nav/",
        exit_process.OffboardingPipelineNav.as_view(),
        name="offboarding-pipeline-nav",
    ),
    # path("add-employee/", views.add_employee, name="add-employee"),
    path(
        "add-employee/",
        exit_process.OffboardingStageAddEmployeeForm.as_view(),
        name="add-employee",
    ),
    path(
        "add-offboarding-employee/",
        exit_process.OffboardingStageAddEmployeeForm.as_view(),
        name="add-offboarding-employee",
    ),
    path(
        "add-employee/<int:pk>/",
        exit_process.OffboardingStageAddEmployeeForm.as_view(),
        name="add-employee",
    ),
    path(
        "delete-offboarding-stage/", views.delete_stage, name="delete-offboarding-stage"
    ),
    path(
        "offboarding-change-stage/", views.change_stage, name="offboarding-change-stage"
    ),
    path(
        "change-offboarding-stage/",
        views.change_offboarding_stage,
        name="change-offboarding-stage",
    ),
    path(
        "view-offboarding-note/<int:employee_id>/",
        views.view_notes,
        name="view-offboarding-note",
    ),
    path("add-offboarding-note/", views.add_note, name="add-offboarding-note"),
    path(
        "delete-note-attachment/",
        views.delete_attachment,
        name="delete-note-attachment",
    ),
    # path("offboarding-add-task/", views.add_task, name="offboarding-add-task"),
    path(
        "offboarding-add-task/",
        exit_process.OffboardingTaskFormView.as_view(),
        name="offboarding-add-task",
    ),
    path(
        "offboarding-update-task/<int:pk>/",
        exit_process.OffboardingTaskFormView.as_view(),
        name="offboarding-update-task",
    ),
    path("update-task-status/", views.update_task_status, name="update-task-status"),
    path("offboarding-assign-task/", views.task_assign, name="offboarding-assign-task"),
    path(
        "delete-offboarding-employee/",
        views.delete_employee,
        name="delete-offboarding-employee",
    ),
    path("delete-offboarding-task/", views.delete_task, name="delete-offboarding-task"),
    # path(
    #     "offboarding-individual-view/<int:emp_id>/",
    #     views.offboarding_individual_view,
    #     name="offboarding-individual-view",
    # ),
    path(
        "offboarding-individual-view/<int:pk>/",
        exit_process.ExitProcessDetailView.as_view(),
        name="offboarding-individual-view",
    ),
    path(
        "offboarding-note-delete/<int:note_id>/",
        views.offboarding_note_delete,
        name="offboarding-note-delete",
    ),
    # path(
    #     "resignation-requests-view/",
    #     views.request_view,
    #     name="resignation-request-view",
    # ),
    # Retired: the old Resignations page (reviewing now happens under Exit Process).
    # path(
    #     "resignation-requests-view/",
    #     resignation.ResignationLettersView.as_view(),
    #     name="resignation-request-view",
    # ),
    path("resignation-requests-view/", views.legacy_resignations_retired, name="resignation-request-view"),
    # Retired: the old Resignations page (reviewing now happens under Exit Process).
    # path(
    #     "list-resignation-requests/",
    #     resignation.ResignationListView.as_view(),
    #     name="list-resignation-request",
    # ),
    path("list-resignation-requests/", views.legacy_resignations_retired, name="list-resignation-request"),
    path(
        "tab-resignation-requests/<int:pk>/",
        resignation_tab.ResignationTabView.as_view(),
        name="tab-resignation-requests",
    ),
    # path(
    #     "tab-resignation-requests/<int:pk>",
    #     check_feature_enabled("resignation_request", OffboardingGeneralSetting)(
    #         resignation_tab.ResignationTabView.as_view()
    #     ),
    #     name="tab-resignation-requests",
    # ),
    path(
        "tab-resignation-requests-detail-view/<int:pk>/",
        resignation_tab.ResignationTabDetailView.as_view(),
        name="tab-resignation-requests-detail-view",
    ),
    # Retired: the old Resignations page (reviewing now happens under Exit Process).
    # path(
    #     "nav-resignation-requests/",
    #     resignation.ResinationLettersNav.as_view(),
    #     name="nav-resignation-request",
    # ),
    path("nav-resignation-requests/", views.legacy_resignations_retired, name="nav-resignation-request"),
    path(
        "resignation-requests-create/",
        resignation.ResignationLettersFormView.as_view(),
        name="resignation-requests-create",
    ),
    path("exit-process/", exit_pages.exit_process_page, name="exit-process"),
    path("exit-process/tab/<str:tab>/", exit_pages.exit_process_tab, name="exit-process-tab"),
    path("exit-process/initiate/", exit_pages.exit_initiate, name="exit-initiate"),
    path("exit-process/review/<int:pk>/", exit_pages.exit_review, name="exit-review"),
    path("exit-process/remind/<int:pk>/", exit_pages.exit_remind, name="exit-remind"),
    path("exit-process/withdraw/<int:pk>/", exit_pages.exit_withdraw, name="exit-withdraw"),
    path("exit-process/summary/<int:pk>/", exit_panel.manage_panel, name="exit-summary"),
    path("exit-process/manage/<int:pk>/history-summary/", exit_panel.exit_history_summary, name="exit-history-summary"),
    path("exit-process/manage/<int:pk>/<str:section>/", exit_panel.manage_panel, name="exit-manage"),
    path("exit-process/manage/<int:pk>/assets/ask/<int:assignment_id>/", exit_panel.exit_asset_ask, name="exit-asset-ask"),
    path("exit-process/manage/<int:pk>/assets/return/<int:assignment_id>/", exit_panel.exit_asset_return, name="exit-asset-return"),
    path("exit-process/manage/<int:pk>/team/handover/", exit_panel.exit_team_handover, name="exit-team-handover"),
    path("exit-process/task/<int:pk>/remind/", exit_panel.exit_task_remind, name="exit-task-remind"),
    path("exit-process/task/<int:pk>/<str:action>/", exit_panel.exit_task_action, name="exit-task-action"),
    path("exit-process/my-tasks/", exit_panel.exit_my_tasks, name="exit-my-tasks"),
    path("exit-process/manage/<int:pk>/leave/extend/", exit_panel.exit_leave_extend, name="exit-leave-extend"),
    path("exit-process/date/<int:pk>/", exit_pages.exit_change_date, name="exit-change-date"),
    path("exit-process/cancel/<int:pk>/", exit_pages.exit_cancel, name="exit-cancel"),
    path("exit-process/deactivate/<int:pk>/", exit_pages.exit_deactivate, name="exit-deactivate"),
    path("exit-settings/", exit_settings.exit_settings_view, name="exit-settings"),
    path("exit-survey/", exit_survey_pages.exit_survey_fill, name="exit-survey-fill"),
    path("exit-survey/export/", exit_survey_pages.exit_survey_export, name="exit-survey-export"),
    path("exit-survey/response/<int:pk>/", exit_survey_pages.exit_survey_view, name="exit-survey-view"),
    path("exit-process/manage/<int:pk>/survey/send/", exit_survey_pages.exit_survey_send, name="exit-survey-send"),
    path("exit-process/survey/<int:pk>/remind/", exit_survey_pages.exit_survey_remind, name="exit-survey-remind"),
    path("exit-survey-settings/", exit_survey_settings.exit_survey_settings, name="exit-survey-settings"),
    path("exit-survey-settings/auto-send/", exit_survey_settings.exit_survey_auto_send, name="exit-survey-auto-send"),
    path("exit-survey-settings/question/new/", exit_survey_settings.exit_survey_question_save, name="exit-survey-question-new"),
    path("exit-survey-settings/question/<int:pk>/", exit_survey_settings.exit_survey_question_save, name="exit-survey-question-update"),
    path("exit-survey-settings/question/<int:pk>/delete/", exit_survey_settings.exit_survey_question_delete, name="exit-survey-question-delete"),
    path("exit-survey-settings/import/", exit_survey_settings.exit_survey_import, name="exit-survey-import"),
    path("exit-survey-settings/section/", exit_survey_settings.exit_survey_questions_section, name="exit-survey-questions-section"),
    path("exit-survey-settings/question/form/", exit_survey_settings.exit_survey_question_form, name="exit-survey-question-form"),
    path("exit-survey-settings/question/<int:pk>/form/", exit_survey_settings.exit_survey_question_form, name="exit-survey-question-edit-form"),
    path("exit-survey-settings/import/form/", exit_survey_settings.exit_survey_import_form, name="exit-survey-import-form"),
    path("exit-tasks/", exit_task_settings.exit_task_groups, name="exit-task-groups"),
    path("exit-tasks/section/", exit_task_settings.exit_task_groups_section, name="exit-task-groups-section"),
    path("exit-tasks/new/", exit_task_settings.exit_task_group_form, name="exit-task-group-new"),
    path("exit-tasks/<int:pk>/", exit_task_settings.exit_task_group_form, name="exit-task-group-edit"),
    path("exit-tasks/<int:pk>/toggle/", exit_task_settings.exit_task_group_toggle, name="exit-task-group-toggle"),
    path("exit-tasks/<int:pk>/delete/", exit_task_settings.exit_task_group_delete, name="exit-task-group-delete"),
    path("exit-settings/save/", exit_settings.exit_settings_save, name="exit-settings-save"),
    path("exit-settings/reason/save/", exit_settings.exit_reason_save, name="exit-reason-save"),
    path("exit-settings/reason/<int:pk>/save/", exit_settings.exit_reason_save, name="exit-reason-update"),
    path("exit-settings/reason/<int:pk>/delete/", exit_settings.exit_reason_delete, name="exit-reason-delete"),
    path("exit-settings/policy/save/", exit_settings.notice_policy_save, name="notice-policy-save"),
    path("exit-settings/policy/<int:pk>/save/", exit_settings.notice_policy_save, name="notice-policy-update"),
    path("exit-settings/policy/<int:pk>/default/", exit_settings.notice_policy_default, name="notice-policy-default"),
    path("exit-settings/policy/<int:pk>/delete/", exit_settings.notice_policy_delete, name="notice-policy-delete"),
    path("exit-settings/policy/assign/", exit_settings.notice_policy_assign, name="notice-policy-assign"),
    path("exit-settings/section/<str:name>/", exit_settings.exit_settings_section, name="exit-settings-section"),
    path("exit-settings/reason/new/", exit_settings.exit_reason_form, name="exit-reason-form"),
    path("exit-settings/reason/<int:pk>/edit/", exit_settings.exit_reason_form, name="exit-reason-edit-form"),
    path("exit-settings/policy/new/", exit_settings.notice_policy_form, name="notice-policy-form"),
    path("exit-settings/policy/<int:pk>/edit/", exit_settings.notice_policy_form, name="notice-policy-edit-form"),
    path("exit-settings/policy/<int:pk>/people/", exit_settings.notice_policy_people, name="notice-policy-people"),
    path(
        "resignation-self-create/",
        resignation.SelfResignationFormView.as_view(),
        name="resignation-self-create",
    ),
    # Retired: the old Resignations page (reviewing now happens under Exit Process).
    # path(
    #     "resignation-requests-update/<int:pk>/",
    #     resignation.ResignationLettersFormView.as_view(),
    #     name="resignation-request-update",
    # ),
    path("resignation-requests-update/<int:pk>/", views.legacy_resignations_retired, name="resignation-request-update"),
    path(
        "resignation-requests-detail-view/<int:pk>/",
        resignation.ResignationLetterDetailView.as_view(),
        name="resignation-requests-detail-view",
    ),
    path(
        "resignation-requests-detail-view/<int:pk>/",
        resignation.ResignationLetterDetailView.as_view(),
        name="resignation-requests-detail-view",
    ),
    # Retired: the old Resignations page (reviewing now happens under Exit Process).
    # path(
    #     "resignation-requests-single-view/<int:id>/",
    #     views.request_single_view,
    #     name="resignation-request-single-view",
    # ),
    path("resignation-requests-single-view/<int:id>/", views.legacy_resignations_retired, name="resignation-request-single-view"),
    path(
        "create-resignation-request/",
        views.create_resignation_request,
        name="create-resignation-request",
    ),
    path(
        "search-resignation-request/",
        views.search_resignation_request,
        name="search-resignation-request",
    ),
    # (kept: an employee deletes their own pending resignation from their profile with this)
    path(
        "delete-resignation-request/",
        views.delete_resignation_request,
        name="delete-resignation-request",
    ),
    # Retired: the old Resignations page (reviewing now happens under Exit Process).
    # path("update-letter-status/", views.update_status, name="update-letter-status"),
    path("update-letter-status/", views.legacy_resignations_retired, name="update-letter-status"),
    path(
        "enable-resignation-request/",
        views.enable_resignation_request,
        name="enable-resignation-request",
    ),
    path(
        "offboarding-rules-view/",
        views.offboarding_rules_settings_view,
        name="offboarding-rules-view",
    ),
    path("get-notice-period/", views.get_notice_period, name="get-notice-period"),
    path(
        "get-notice-period-end-date/",
        views.get_notice_period_end_date,
        name="get-notice-period-end-date",
    ),
    path(
        "offboarding-pipeline-filter/",
        views.filter_pipeline,
        name="offboarding-pipeline-filter",
    ),
    path(
        "dashboard-task-table/",
        views.dashboard_task_table,
        name="dashboard-task-table",
    ),
    path(
        "dashboard-department-chart/",
        views.department_job_postion_chart,
        name="dashboard-department-chart",
    ),
    path(
        "dashboard-join-chart/",
        views.dashboard_join_chart,
        name="dashboard-join-chart",
    ),
    path(
        "list-dashboard-task-status/",
        exit_process.DashboardTaskListview.as_view(),
        name="list-dashboard-task-status",
    ),
    # ── Offboarding Modern Dashboard ─────────────────────────────────────────
    path(
        "dashboard/",
        ob_dashboard.offboarding_dashboard_view,
        name="offboarding-dashboard",
    ),
    path(
        "dashboard/api/kpi/",
        ob_dashboard.offboarding_kpi_data,
        name="offboarding-dashboard-kpi",
    ),
    path(
        "dashboard/api/pipeline/",
        ob_dashboard.offboarding_pipeline,
        name="offboarding-dashboard-pipeline",
    ),
    path(
        "dashboard/api/resignation/",
        ob_dashboard.offboarding_resignation_status,
        name="offboarding-dashboard-resignation",
    ),
    path(
        "dashboard/api/tasks/",
        ob_dashboard.offboarding_task_status,
        name="offboarding-dashboard-tasks",
    ),
    path(
        "dashboard/api/department/",
        ob_dashboard.offboarding_department_attrition,
        name="offboarding-dashboard-dept",
    ),
    path(
        "dashboard/api/exit-reasons/",
        ob_dashboard.offboarding_exit_reasons,
        name="offboarding-dashboard-reasons",
    ),
    path(
        "dashboard/api/notice/",
        ob_dashboard.offboarding_notice_period_tracker,
        name="offboarding-dashboard-notice",
    ),
    path(
        "dashboard/api/assets/",
        ob_dashboard.offboarding_unreturned_assets,
        name="offboarding-dashboard-assets",
    ),
    path(
        "dashboard/api/join-exit/",
        ob_dashboard.offboarding_joining_vs_exiting,
        name="offboarding-dashboard-join-exit",
    ),
    path(
        "dashboard/api/avg-duration/",
        ob_dashboard.offboarding_avg_duration,
        name="offboarding-dashboard-avg-duration",
    ),
]

if apps.is_installed("asset"):
    urlpatterns += [
        path(
            "dashboard-asset-table/",
            views.dashboard_asset_table,
            name="dashboard-asset-table",
        ),
        path(
            "dashboard-asset-table-cbv/",
            exit_process.DashboardNotReturndAsssets.as_view(),
            name="dashboard-asset-table-cbv",
        ),
    ]

if apps.is_installed("pms"):
    urlpatterns += [
        path(
            "dashboard-feedback-table/",
            views.dashboard_feedback_table,
            name="dashboard-feedback-table",
        ),
        path(
            "dashboard-feedback-table-cbv/",
            exit_process.DashboardFeedbackView.as_view(),
            name="dashboard-feedback-table-cbv",
        ),
    ]
