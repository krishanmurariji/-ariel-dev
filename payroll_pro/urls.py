"""
payroll_pro/urls.py
"""

from django.urls import path

from payroll_pro import views

urlpatterns = [
    # 1. Tax Regime
    path("tax-regimes/", views.tax_regime_view, name="pp-tax-regimes"),
    path("tax-regimes/new/", views.tax_regime_form, name="pp-tax-regime-new"),
    path(
        "tax-regimes/<int:pk>/edit/",
        views.tax_regime_form,
        name="pp-tax-regime-edit",
    ),
    path(
        "tax-regimes/<int:pk>/view/",
        views.tax_regime_detail_modal,
        name="pp-tax-regime-view",
    ),
    path("tax-regimes/save/", views.tax_regime_save, name="pp-tax-regime-save"),
    path(
        "tax-regimes/<int:pk>/delete/",
        views.tax_regime_delete,
        name="pp-tax-regime-delete",
    ),
    path(
        "tax-regimes/<int:pk>/duplicate/",
        views.tax_regime_duplicate,
        name="pp-tax-regime-duplicate",
    ),
    path(
        "tax-regimes/<int:pk>/delete-confirm/",
        views.tax_regime_delete_confirm,
        name="pp-tax-regime-delete-confirm",
    ),
    # 2. Salary Structures
    path("structures/", views.structure_view, name="pp-structures"),
    path("structures/new/", views.structure_form, name="pp-structure-new"),
    path("structures/<int:pk>/edit/", views.structure_form, name="pp-structure-edit"),
    path("structures/create/", views.structure_create, name="pp-structure-create"),
    path("structures/<int:pk>/", views.structure_detail, name="pp-structure-detail"),
    path(
        "structures/<int:pk>/update/",
        views.structure_update,
        name="pp-structure-update",
    ),
    path(
        "structures/<int:pk>/delete/",
        views.structure_delete,
        name="pp-structure-delete",
    ),
    path(
        "structures/<int:pk>/duplicate/",
        views.structure_duplicate,
        name="pp-structure-duplicate",
    ),
    path(
        "structures/<int:pk>/delete-confirm/",
        views.structure_delete_confirm,
        name="pp-structure-delete-confirm",
    ),
    path(
        "structures/<int:structure_pk>/components/add/",
        views.component_add,
        name="pp-component-add",
    ),
    path(
        "components/<int:pk>/update/",
        views.component_update,
        name="pp-component-update",
    ),
    path(
        "components/<int:pk>/delete/",
        views.component_delete,
        name="pp-component-delete",
    ),
    # 3. Salary Overview
    path("salary-overview/", views.salary_overview_view, name="pp-salary-overview"),
    path("salary-overview/assign/", views.salary_assign, name="pp-salary-assign"),
    path(
        "salary-overview/<int:employee_pk>/timeline/",
        views.salary_timeline,
        name="pp-salary-timeline",
    ),
    path(
        "salary-overview/<int:employee_pk>/revision/add/",
        views.revision_add,
        name="pp-revision-add",
    ),
    # 3b. Pay Revisions (bifurcated from Salary Overview)
    path("pay-revisions/", views.pay_revisions_view, name="pp-pay-revisions"),
    # 4. Run Payroll
    path("run/", views.run_payroll_view, name="pp-run-payroll"),
    path("run/step-save/", views.run_step_save, name="pp-run-step-save"),
    path("run/<int:pk>/finalize/", views.run_finalize, name="pp-run-finalize"),
    path("run/<int:pk>/reopen/", views.run_reopen, name="pp-run-reopen"),
    path("run/<int:pk>/release/", views.run_release, name="pp-run-release"),
    path(
        "run/<int:run_pk>/claims/<int:claim_pk>/approve/",
        views.claim_approve,
        name="pp-claim-approve",
    ),
    path(
        "run/<int:run_pk>/claims/<int:claim_pk>/reject/",
        views.claim_reject,
        name="pp-claim-reject",
    ),
    path(
        "run/<int:run_pk>/records/<int:record_pk>/detail/",
        views.run_record_detail_modal,
        name="pp-run-record-detail",
    ),
    # 5a. Payslips (admin)
    path("payslips/", views.payslips_view, name="pp-payslips"),
    path(
        "payslips/<int:pk>/pdf/",
        views.payslip_pdf_download,
        name="pp-payslip-pdf",
    ),
    path(
        "payslips/<int:pk>/email/",
        views.payslip_send_mail,
        name="pp-payslip-email",
    ),
    path(
        "payslips/run/<int:run_pk>/email-all/",
        views.payslip_send_mail_all,
        name="pp-payslip-email-all",
    ),
    # 5b. My Finance (ESS)
    path("my-finance/", views.my_finance_view, name="pp-my-finance"),
    path(
        "my-finance/declaration/save/",
        views.tax_declaration_save,
        name="pp-declaration-save",
    ),
    path(
        "my-finance/regime/save/",
        views.tax_regime_choice_save,
        name="pp-regime-choice-save",
    ),
    path(
        "my-finance/reimbursement/request/",
        views.reimbursement_request,
        name="pp-reimbursement-request",
    ),
    # Settings: auto run
    path("settings/auto-run/", views.auto_settings_view, name="pp-auto-settings"),
    path(
        "settings/auto-run/new/",
        views.auto_settings_form,
        name="pp-auto-settings-new",
    ),
    path(
        "settings/auto-run/<int:pk>/edit/",
        views.auto_settings_form,
        name="pp-auto-settings-edit",
    ),
    path(
        "settings/auto-run/save/",
        views.auto_settings_save,
        name="pp-auto-settings-save",
    ),
    path(
        "settings/auto-run/<int:pk>/delete/",
        views.auto_settings_delete,
        name="pp-auto-settings-delete",
    ),
]
