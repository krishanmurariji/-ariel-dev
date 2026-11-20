"""
vertex/cbv_performance.py

Performance pages on Ariel HR's list / filter / modal framework: Feedback, Performance Reports and
Improvement Plans (PIP). Detail pages and workflow actions are in vertex/views_performance.py.
"""

from typing import Any

from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from horilla.methods import handle_no_permission
from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from vertex import access, performance
from vertex.cbv import RELOAD_PAGE, VertexModal, _filter_indications
from vertex.filters import FeedbackFilter, PerformanceReportFilter, PipFilter
from vertex.forms_performance import FeedbackForm, GenerateReportForm, PipForm
from vertex.models import (
    Feedback,
    FeedbackCategoryScore,
    FeedbackStatus,
    PerformanceReport,
    Pip,
    PipStatus,
)
from vertex.notify import employees_with_perm, notify_employees


def _create_attrs(url_name):
    return f"""
        onclick = "event.stopPropagation();"
        data-toggle="oh-modal-toggle"
        data-target="#genericModal"
        hx-target="#genericModalBody"
        hx-get="{reverse(url_name)}"
    """


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


def can_edit_feedback_row(request, instance, *args, **kwargs):
    return access.can_edit_feedback(request.user, instance)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_feedback"), name="dispatch")
class FeedbackView(TemplateView):
    template_name = "vertex/cbv/feedback.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_feedback"), name="dispatch")
class FeedbackNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = FeedbackFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status", "period", "project"]
    filter_body_template = "vertex/cbv/feedback_filter.html"
    hide_actions_button = True
    create_label = _("New feedback")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-feedback-list")
        if self.request.user.has_perm("vertex.add_feedback"):
            self.create_attrs = _create_attrs("vertex-feedback-new")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_feedback"), name="dispatch")
class FeedbackList(HorillaListView):
    model = Feedback
    filter_class = FeedbackFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_feedback(self.request.user).values("pk")).select_related(
            "subject", "author", "project"
        ).prefetch_related("category_scores")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-feedback-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_subject_name"),
            (_("Period"), "period"),
            (_("Project"), "get_project_code"),
            (_("Average"), "get_average_display"),
            (_("Status"), "get_status_html"),
            (_("Written by"), "get_author_name"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Period"), "period"), (_("Created"), "created_at")]

    row_status_indications = _filter_indications("status", FeedbackStatus.choices)
    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Edit"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv_performance.can_edit_feedback_row",
            "attrs": """
                hx-get='{get_update_url}'
                hx-target='#genericModalBody'
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
            """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class FeedbackFormView(VertexModal, HorillaFormView):
    """Modal: write feedback about someone, or revise your own draft / sent-back feedback."""

    model = Feedback
    form_class = FeedbackForm
    new_display_title = _("New feedback")

    def dispatch(self, request, *args, **kwargs):
        me = access.employee_of(request.user)
        pk = kwargs.get("pk")
        if me is None:
            return handle_no_permission(request)
        if pk:
            feedback = access.visible_feedback(request.user).filter(pk=pk).first()
            if feedback is None or not access.can_edit_feedback(request.user, feedback):
                return handle_no_permission(request)
        elif not request.user.has_perm("vertex.add_feedback"):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Edit feedback")
        return context

    @transaction.atomic
    def form_valid(self, form):
        me = access.employee_of(self.request.user)
        creating = form.instance.pk is None
        feedback = form.save(commit=False)
        if creating:
            feedback.author = me
        was_revision = feedback.status == FeedbackStatus.REVISION_REQUESTED
        submit = form.cleaned_data.get("submit_now")
        if submit:
            feedback.status = FeedbackStatus.SUBMITTED
            feedback.revision_reason = ""
        feedback.save()
        feedback.category_scores.all().delete()
        for key, score, not_applicable, comment in form.category_data():
            FeedbackCategoryScore.objects.create(
                feedback=feedback, category=key, score=score,
                not_applicable=not_applicable, comment=comment,
            )
        if submit:
            notify_employees(
                me,
                employees_with_perm("vertex.change_feedbackapproval"),
                str(_("Feedback awaiting approval: %(who)s (%(period)s)."))
                % {"who": feedback.subject.get_full_name(), "period": feedback.period},
                feedback.get_absolute_url(),
                icon="chatbubble-ellipses-outline",
            )
            messages.success(
                self.request,
                _("Feedback resubmitted for approval.") if was_revision else _("Feedback submitted for HR approval."),
            )
        else:
            messages.success(self.request, _("Draft saved."))
        # take the author to the feedback, where the next step (submit / publish) is
        return self.HttpResponse(
            f"<script>window.location.href='{feedback.get_absolute_url()}'</script>"
        )


# ---------------------------------------------------------------------------
# Performance reports
# ---------------------------------------------------------------------------


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_performancereport"), name="dispatch")
class ReportsView(TemplateView):
    template_name = "vertex/cbv/reports.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_performancereport"), name="dispatch")
class ReportsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = PerformanceReportFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["period", "rating", "period_type"]
    filter_body_template = "vertex/cbv/report_filter.html"
    hide_actions_button = True
    create_label = _("Generate report")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-performance-list")
        if self.request.user.has_perm("vertex.add_performancereport"):
            self.create_attrs = _create_attrs("vertex-performance-generate")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_performancereport"), name="dispatch")
class ReportsList(HorillaListView):
    model = PerformanceReport
    filter_class = PerformanceReportFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_reports(self.request.user).values("pk")).select_related("subject")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-performance-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_subject_name"),
            (_("Period"), "period"),
            (_("Score"), "get_score_display"),
            (_("Rating"), "get_rating_html"),
            (_("Status"), "get_published_html"),
            (_("Evidence"), "data_sources"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Period"), "period"), (_("Score"), "overall_score")]

    row_status_class = "status-{is_published}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class GenerateReportFormView(VertexModal, HorillaFormView):
    """Modal: build the draft performance report of one person for one period."""

    model = PerformanceReport
    form_class = GenerateReportForm
    new_display_title = _("Generate performance report")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm("vertex.add_performancereport"):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return None

    def form_valid(self, form):
        data = form.cleaned_data
        report = performance.generate_report(data["subject"], data["period_type"], data["period"])
        messages.success(
            self.request,
            _("Report generated as a draft. Review it, then publish it to the employee."),
        )
        return self.HttpResponse(f"<script>window.location.href='{report.get_absolute_url()}'</script>")


# ---------------------------------------------------------------------------
# Improvement plans
# ---------------------------------------------------------------------------


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_pip"), name="dispatch")
class PipView(TemplateView):
    template_name = "vertex/cbv/pip.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_pip"), name="dispatch")
class PipNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = PipFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status", "outcome", "is_auto"]
    filter_body_template = "vertex/cbv/pip_filter.html"
    hide_actions_button = True
    create_label = _("New improvement plan")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-pip-list")
        if self.request.user.has_perm("vertex.add_pip"):
            self.create_attrs = _create_attrs("vertex-pip-new")


def can_change_pip(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.change_pip")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_pip"), name="dispatch")
class PipList(HorillaListView):
    model = Pip
    filter_class = PipFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_pips(self.request.user).values("pk")).select_related(
            "subject", "created_by"
        )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-pip-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_subject_name"),
            (_("Reason"), "reason"),
            (_("Source"), "get_source_display"),
            (_("Status"), "get_status_html"),
            (_("Outcome"), "get_outcome_html"),
            (_("Started"), "start_date"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Started"), "start_date"), (_("Created"), "created_at")]

    row_status_indications = _filter_indications("status", PipStatus.choices)
    row_status_class = "status-{status}"
    actions_menu = True
    actions = [
        {
            "action": _("Update"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv_performance.can_change_pip",
            "attrs": """
                hx-get='{get_update_url}'
                hx-target='#genericModalBody'
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
            """,
        },
    ]


@method_decorator(login_required, name="dispatch")
class PipFormView(VertexModal, HorillaFormView):
    model = Pip
    form_class = PipForm
    new_display_title = _("New improvement plan")

    def dispatch(self, request, *args, **kwargs):
        perm = "vertex.change_pip" if kwargs.get("pk") else "vertex.add_pip"
        if not request.user.has_perm(perm):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Update improvement plan")
        return context

    def form_valid(self, form):
        creating = form.instance.pk is None
        me = access.employee_of(self.request.user)
        pip = form.save(commit=False)
        if creating:
            pip.created_by = me
            pip.is_auto = False
        pip.save()
        if creating:
            notify_employees(
                me, [pip.subject],
                str(_("A Performance Improvement Plan was created for you: %(reason)s")) % {"reason": pip.reason},
                reverse("vertex-my-performance"), icon="trending-up-outline",
            )
            messages.success(self.request, _("Improvement plan created."))
        else:
            messages.success(self.request, _("Improvement plan updated."))
        return self.HttpResponse()
