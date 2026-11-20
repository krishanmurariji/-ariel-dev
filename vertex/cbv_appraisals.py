"""
vertex/cbv_appraisals.py

Appraisal Cycles, Appraisals and Goals on Ariel HR's list / filter / modal framework.
The appraisal detail page and the POST actions (activate, close, delete) are in
vertex/views_appraisals.py.
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
from vertex import access
from vertex.cbv import RELOAD_PAGE, VertexModal, _filter_indications
from vertex.filters import AppraisalFilter, CycleFilter, GoalFilter
from vertex.forms_appraisals import (
    CycleForm,
    GoalForm,
    GoalProgressForm,
    ManagerAppraisalForm,
    ReleaseAppraisalForm,
    SelfAppraisalForm,
)
from vertex.models import (
    Appraisal,
    AppraisalCycle,
    AppraisalStage,
    CycleStatus,
    Goal,
    GoalStatus,
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


def _modal_attrs(url_expression):
    return f"""
        hx-get='{url_expression}'
        hx-target='#genericModalBody'
        data-toggle="oh-modal-toggle"
        data-target="#genericModal"
    """


def _post_attrs(url_expression, confirm):
    return f"""
        hx-post='{url_expression}'
        hx-confirm='{confirm}'
        hx-swap='none'
        hx-on::after-request='window.location.reload()'
    """


# ---------------------------------------------------------------------------
# Appraisal cycles
# ---------------------------------------------------------------------------


def can_manage_cycle_row(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.change_appraisalcycle") and instance.status != CycleStatus.CLOSED


def can_activate_cycle_row(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.change_appraisalcycle") and instance.status == CycleStatus.DRAFT


def can_close_cycle_row(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.change_appraisalcycle") and instance.status == CycleStatus.ACTIVE


def can_open_cycle_appraisals(request, instance, *args, **kwargs):
    return request.user.has_perm("vertex.view_appraisal")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_appraisalcycle"), name="dispatch")
class CyclesView(TemplateView):
    template_name = "vertex/cbv/cycles.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_appraisalcycle"), name="dispatch")
class CyclesNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = CycleFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status"]
    filter_body_template = "vertex/cbv/cycle_filter.html"
    hide_actions_button = True
    create_label = _("New cycle")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-cycle-list")
        if self.request.user.has_perm("vertex.add_appraisalcycle"):
            self.create_attrs = _create_attrs("vertex-cycle-new")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_appraisalcycle"), name="dispatch")
class CyclesList(HorillaListView):
    model = AppraisalCycle
    filter_class = CycleFilter
    bulk_select_option = False
    quick_export = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-cycle-list")

    @cached_property
    def columns(self):
        return [
            (_("Cycle"), "name"),
            (_("Period"), "get_period_display"),
            (_("Status"), "get_status_html"),
            (_("Appraisals"), "get_progress_display"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Cycle"), "name"), (_("Start"), "start_date")]

    row_status_indications = _filter_indications("status", CycleStatus.choices)
    row_status_class = "status-{status}"
    actions_menu = True
    actions = [
        {
            "action": _("Appraisals"),
            "icon": "open-outline",
            "accessibility": "vertex.cbv_appraisals.can_open_cycle_appraisals",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_appraisals_url}'" """,
        },
        {
            "action": _("Edit"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv_appraisals.can_manage_cycle_row",
            "attrs": _modal_attrs("{get_update_url}"),
        },
        {
            "action": _("Activate"),
            "icon": "play-outline",
            "accessibility": "vertex.cbv_appraisals.can_activate_cycle_row",
            "attrs": _post_attrs(
                "{get_activate_url}",
                "Open this cycle? An appraisal is created for every active employee and they are asked for a self assessment.",
            ),
        },
        {
            "action": _("Close"),
            "icon": "lock-closed-outline",
            "accessibility": "vertex.cbv_appraisals.can_close_cycle_row",
            "attrs": _post_attrs(
                "{get_close_url}", "Close this cycle? No more ratings can be added to its appraisals."
            ),
        },
    ]


@method_decorator(login_required, name="dispatch")
class CycleFormView(VertexModal, HorillaFormView):
    model = AppraisalCycle
    form_class = CycleForm
    new_display_title = _("New appraisal cycle")

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        if pk:
            cycle = AppraisalCycle.objects.filter(pk=pk).first()
            if (
                cycle is None
                or cycle.status == CycleStatus.CLOSED
                or not request.user.has_perm("vertex.change_appraisalcycle")
            ):
                return handle_no_permission(request)
        elif not request.user.has_perm("vertex.add_appraisalcycle"):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Edit appraisal cycle")
        return context

    def form_valid(self, form):
        creating = form.instance.pk is None
        cycle = form.save(commit=False)
        if creating:
            cycle.created_by = access.employee_of(self.request.user)
        cycle.save()
        messages.success(self.request, _("Cycle created. Activate it to open the appraisals.") if creating else _("Cycle updated."))
        return self.HttpResponse(RELOAD_PAGE)


# ---------------------------------------------------------------------------
# Appraisals
# ---------------------------------------------------------------------------


def can_self_assess_row(request, instance, *args, **kwargs):
    return access.can_self_assess(request.user, instance)


def can_evaluate_row(request, instance, *args, **kwargs):
    return access.can_evaluate(request.user, instance)


def can_release_row(request, instance, *args, **kwargs):
    return access.can_release(request.user, instance)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_appraisal"), name="dispatch")
class AppraisalsView(TemplateView):
    template_name = "vertex/cbv/appraisals.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_appraisal"), name="dispatch")
class AppraisalsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = AppraisalFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["cycle", "stage"]
    filter_body_template = "vertex/cbv/appraisal_filter.html"
    hide_actions_button = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-appraisal-list")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_appraisal"), name="dispatch")
class AppraisalsList(HorillaListView):
    model = Appraisal
    filter_class = AppraisalFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_appraisals(self.request.user).values("pk")).select_related(
            "cycle", "employee", "manager"
        )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-appraisal-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_employee_name"),
            (_("Cycle"), "get_cycle_name"),
            (_("Manager"), "get_manager_name"),
            (_("Stage"), "get_stage_html"),
            (_("Self rating"), "get_self_rating_display"),
            (_("Final rating"), "get_final_rating_display"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Created"), "created_at"), (_("Final rating"), "final_rating")]

    row_status_indications = _filter_indications("stage", AppraisalStage.choices)
    row_status_class = "status-{stage}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Self assessment"),
            "icon": "person-circle-outline",
            "accessibility": "vertex.cbv_appraisals.can_self_assess_row",
            "attrs": _modal_attrs("{get_self_url}"),
        },
        {
            "action": _("Evaluate"),
            "icon": "ribbon-outline",
            "accessibility": "vertex.cbv_appraisals.can_evaluate_row",
            "attrs": _modal_attrs("{get_manager_url}"),
        },
        {
            "action": _("Release rating"),
            "icon": "checkmark-done-outline",
            "accessibility": "vertex.cbv_appraisals.can_release_row",
            "attrs": _modal_attrs("{get_release_url}"),
        },
    ]


class _AppraisalStageForm(VertexModal, HorillaFormView):
    """Base for the three appraisal stage modals; ``allowed`` decides who may open and submit."""

    model = Appraisal

    @staticmethod
    def allowed(user, appraisal):
        return False

    def get_appraisal(self):
        return access.visible_appraisals(self.request.user).filter(pk=self.kwargs.get("pk")).first()

    def dispatch(self, request, *args, **kwargs):
        appraisal = access.visible_appraisals(request.user).filter(pk=kwargs.get("pk")).first()
        if appraisal is None or not self.allowed(request.user, appraisal):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)


@method_decorator(login_required, name="dispatch")
class SelfAppraisalFormView(_AppraisalStageForm):
    form_class = SelfAppraisalForm
    allowed = staticmethod(access.can_self_assess)

    @transaction.atomic
    def form_valid(self, form):
        appraisal = form.save(commit=False)
        appraisal.stage = AppraisalStage.SELF_SUBMITTED
        appraisal.self_submitted_at = timezone.now()
        appraisal.save()
        me = access.employee_of(self.request.user)
        if appraisal.manager_id:
            notify_employees(
                me, [appraisal.manager],
                str(_("%(name)s submitted their self assessment. Please evaluate it.")) % {"name": me},
                reverse("vertex-appraisals"), icon="ribbon-outline",
            )
        messages.success(self.request, _("Self assessment submitted."))
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class ManagerAppraisalFormView(_AppraisalStageForm):
    form_class = ManagerAppraisalForm
    allowed = staticmethod(access.can_evaluate)

    @transaction.atomic
    def form_valid(self, form):
        me = access.employee_of(self.request.user)
        appraisal = form.save(commit=False)
        appraisal.stage = AppraisalStage.MANAGER_COMPLETED
        appraisal.manager_reviewed_at = timezone.now()
        if appraisal.manager_id is None:
            appraisal.manager = me
        appraisal.save()
        releasers = employees_with_perm("vertex.change_appraisalrelease")
        notify_employees(
            me, list(releasers),
            str(_("%(name)s's appraisal is evaluated and ready for release.")) % {"name": appraisal.employee},
            reverse("vertex-appraisals"), icon="ribbon-outline",
        )
        messages.success(self.request, _("Evaluation saved. HR can now release the final rating."))
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class ReleaseAppraisalFormView(_AppraisalStageForm):
    form_class = ReleaseAppraisalForm
    allowed = staticmethod(access.can_release)

    @transaction.atomic
    def form_valid(self, form):
        me = access.employee_of(self.request.user)
        appraisal = form.save(commit=False)
        appraisal.stage = AppraisalStage.RELEASED
        appraisal.released_at = timezone.now()
        appraisal.save()
        notify_employees(
            me, [appraisal.employee],
            str(_("Your appraisal for '%(cycle)s' is released. Final rating: %(rating)s / 5."))
            % {"cycle": appraisal.cycle.name, "rating": appraisal.final_rating},
            appraisal.get_absolute_url(), icon="ribbon-outline",
        )
        messages.success(self.request, _("Final rating released."))
        return self.HttpResponse(RELOAD_PAGE)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


def can_edit_goal_row(request, instance, *args, **kwargs):
    return access.can_edit_goal(request.user, instance)


def can_update_goal_row(request, instance, *args, **kwargs):
    return access.can_update_goal_progress(request.user, instance)


def can_delete_goal_row(request, instance, *args, **kwargs):
    return access.can_delete_goal(request.user, instance)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_goal"), name="dispatch")
class GoalsView(TemplateView):
    template_name = "vertex/cbv/goals.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_goal"), name="dispatch")
class GoalsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = GoalFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status", "cycle", "category", "employee"]
    filter_body_template = "vertex/cbv/goal_filter.html"
    hide_actions_button = True
    create_label = _("Assign goal")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-goal-list")
        if self.request.user.has_perm("vertex.add_goal"):
            self.create_attrs = _create_attrs("vertex-goal-new")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_goal"), name="dispatch")
class GoalsList(HorillaListView):
    model = Goal
    filter_class = GoalFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_goals(self.request.user).values("pk")).select_related(
            "employee", "assigned_by", "cycle"
        )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-goal-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_employee_name"),
            (_("Goal"), "title"),
            (_("Category"), "category"),
            (_("Cycle"), "get_cycle_name"),
            (_("Weight"), "get_weight_display"),
            (_("Progress"), "get_progress_display"),
            (_("Target date"), "target_date"),
            (_("Status"), "get_status_html"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Target date"), "target_date"), (_("Progress"), "progress"), (_("Created"), "created_at")]

    row_status_indications = _filter_indications("status", GoalStatus.choices)
    row_status_class = "status-{status}"
    actions_menu = True
    actions = [
        {
            "action": _("Update progress"),
            "icon": "trending-up-outline",
            "accessibility": "vertex.cbv_appraisals.can_update_goal_row",
            "attrs": _modal_attrs("{get_progress_url}"),
        },
        {
            "action": _("Edit"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv_appraisals.can_edit_goal_row",
            "attrs": _modal_attrs("{get_update_url}"),
        },
        {
            "action": _("Delete"),
            "icon": "trash-outline",
            "accessibility": "vertex.cbv_appraisals.can_delete_goal_row",
            "attrs": _post_attrs("{get_delete_url}", "Delete this goal? This cannot be undone."),
        },
    ]


@method_decorator(login_required, name="dispatch")
class GoalFormView(VertexModal, HorillaFormView):
    model = Goal
    form_class = GoalForm
    new_display_title = _("Assign goal")

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        if pk:
            goal = access.visible_goals(request.user).filter(pk=pk).first()
            if goal is None or not access.can_edit_goal(request.user, goal):
                return handle_no_permission(request)
        elif not request.user.has_perm("vertex.add_goal") or not access.assignable_employees(request.user).exists():
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Edit goal")
        return context

    def form_valid(self, form):
        creating = form.instance.pk is None
        me = access.employee_of(self.request.user)
        goal = form.save(commit=False)
        if creating:
            goal.assigned_by = me
        goal.save()
        if creating:
            notify_employees(
                me, [goal.employee],
                str(_("A new goal was assigned to you: %(title)s")) % {"title": goal.title},
                reverse("vertex-goals"), icon="flag-outline",
            )
            messages.success(self.request, _("Goal assigned."))
        else:
            messages.success(self.request, _("Goal updated."))
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class GoalProgressFormView(VertexModal, HorillaFormView):
    model = Goal
    form_class = GoalProgressForm

    def dispatch(self, request, *args, **kwargs):
        goal = access.visible_goals(request.user).filter(pk=kwargs.get("pk")).first()
        if goal is None or not access.can_update_goal_progress(request.user, goal):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Progress updated."))
        return self.HttpResponse(RELOAD_PAGE)
