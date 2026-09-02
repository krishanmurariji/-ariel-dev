"""
onboarding/cbv/checklist_tasks.py

Kanban and List CBVs backing the "My Tasks" page, plus the Kanban's
drag-and-drop pre-move check. All three share the same permission-scoped
queryset (onboarding.checklist_access) and FilterSet
(onboarding.filters.CandidateChecklistTaskFilter).

Why the pre-move check does the real work:
HorillaKanbanView's generic drop-persist endpoint
(update_kanban_item_group, horilla_views/views.py) updates a "flat"
group_key field - one with no "__" in it, like our `status` CharField -
via `model.objects.bulk_update(...)`. bulk_update() never calls
.save(), so it fires no signals and runs none of our business rules
(status_updated_at/completed_at bookkeeping, onboarding-completion
check). A "nested" group_key (e.g. CandidateKanbanView's
"onboarding_stage__onboarding_stage_id") instead routes through
set_nested_attr(), which does call .save() - a different code path not
available to a flat field.

CandidateKanbanView already relies on the same pre_move_check_url hook
as its real gatekeeper (KanbanRequiredTaskCheck, cbv/pipeline.py), so
doing the authoritative save here - a GET view that runs BEFORE the
generic endpoint - matches that existing pattern. The generic endpoint's
subsequent bulk_update of the same value is a harmless no-op re-write.

Known limitation shared with that existing pattern: genericKanban.js's
pre-move-check AJAX call fails OPEN on any network/auth error (its
`error:` handler calls proceedWithMove() same as a clean pass), so a
timed-out request or an expired session during a drag lets the move
proceed client-side and still reach the generic bulk_update - meaning
the permission check and bookkeeping in this file can be skipped on a
transient failure. This mirrors KanbanRequiredTaskCheck's existing
behavior; fixing it means hardening the shared JS/framework, not this
view alone.
"""

from django.db.models import Count, Q
from django.http import JsonResponse
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from horilla_views.cbv_methods import login_required
from horilla_views.generic.cbv.kanban import HorillaKanbanView
from horilla_views.generic.cbv.views import HorillaListView, HorillaTabView
from onboarding.checklist_access import (
    apply_status_change,
    can_manage_task,
    visible_candidates_queryset,
    visible_tasks_queryset,
)
from onboarding.filters import CandidateChecklistTaskFilter
from onboarding.models import CandidateChecklistTask


@method_decorator(login_required, name="dispatch")
class CandidateChecklistTaskKanbanView(HorillaKanbanView):
    """
    One Kanban board per candidate - columns are
    CandidateChecklistTask.STATUS_CHOICES. The URL kwarg `pk` is the
    Candidate id.
    """

    model = CandidateChecklistTask
    group_key = "status"
    instance_order_by = "id"
    records_per_page = 25
    show_kanban_confirmation = False
    filter_class = CandidateChecklistTaskFilter
    pre_move_check_url = reverse_lazy("onboarding-checklist-task-kanban-move")

    details = {
        "title": "{title}",
        "Phase": "{get_phase_display}",
        "Assignee": "{assignee_id__get_full_name}",
        "Due Date": "{due_date_display}",
    }

    kanban_attrs = """
        hx-get='{get_edit_modal_url}'
        data-toggle="oh-modal-toggle"
        data-target="#genericModal"
        hx-target="#genericModalBody"
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = visible_tasks_queryset(self.request, queryset).select_related(
            "candidate_id", "assignee_id"
        )
        candidate_pk = self.kwargs.get("pk")
        if candidate_pk:
            queryset = queryset.filter(candidate_id=candidate_pk)
        self.queryset = queryset
        return queryset


@method_decorator(login_required, name="dispatch")
class MyTasksCandidateTabsView(HorillaTabView):
    """
    One pill-tab per visible candidate - mirrors the Recruitment Pipeline's
    own tab strip (RecruitmentTabView, cbv/pipeline.py) exactly: a colored
    badge-count pill per item, click-to-lazy-load its board. Here each tab
    is a candidate, and its content is that candidate's Kanban board
    (CandidateChecklistTaskKanbanView).

    Any query-string filters present on this view (status, phase, assignee,
    search) are automatically carried onto every tab's lazy-load URL by
    HorillaTabView.get_context_data - no extra wiring needed for filters
    to reach each candidate's board.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        visible = visible_tasks_queryset(self.request)
        candidates = visible_candidates_queryset(self.request).annotate(
            total_tasks=Count("checklist_tasks", filter=Q(checklist_tasks__in=visible))
        ).order_by("name")

        candidate_filter = self.request.GET.get("candidate")
        if candidate_filter:
            candidates = candidates.filter(pk=candidate_filter)

        self.tabs = [
            {
                "title": candidate.name,
                "url": reverse("my-checklist-tasks-kanban", kwargs={"pk": candidate.pk}),
                "badge_label": _("Tasks"),
                "badge": candidate.total_tasks,
            }
            for candidate in candidates
        ]


@method_decorator(login_required, name="dispatch")
class ChecklistTaskKanbanMoveCheck(View):
    """
    Wired as CandidateChecklistTaskKanbanView.pre_move_check_url - see
    the module docstring for why this is the real gatekeeper (and the
    real save) rather than just a validation check.
    """

    def get(self, request, *args, **kwargs):
        object_id = request.GET.get("objectId")
        target_status = request.GET.get("groupId")
        task = CandidateChecklistTask.objects.filter(pk=object_id).first()
        if not task:
            return JsonResponse({"blocked": False})

        if not can_manage_task(request, task):
            return JsonResponse(
                {"blocked": True, "message": str(_("You cannot update this task."))}
            )

        ok, message = apply_status_change(task, target_status)
        if not ok:
            return JsonResponse({"blocked": True, "message": message})
        return JsonResponse({"blocked": False})


@method_decorator(login_required, name="dispatch")
class CandidateChecklistTaskListView(HorillaListView):
    """
    Table ("List") view of My Tasks - one expandable accordion row per
    candidate (like the Kanban tabs), each holding that candidate's tasks.
    Always grouped by candidate_id (get_context_data below), so there is
    no flat/ungrouped mode to fall back to here.
    """

    model = CandidateChecklistTask
    filter_class = CandidateChecklistTaskFilter
    bulk_select_option = False
    bulk_update = False
    records_per_page = 25

    columns = [
        (_("Task"), "title"),
        (_("Phase"), "get_phase_display"),
        (_("Assignee"), "assignee_id__get_full_name"),
        (_("Status"), "status_col"),
        (_("Last Updated"), "status_updated_at"),
        (_("Due Date"), "due_date"),
    ]

    row_attrs = """
        hx-get="{get_edit_modal_url}"
        hx-target="#genericModalBody"
        data-toggle="oh-modal-toggle"
        data-target="#genericModal"
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = visible_tasks_queryset(self.request, queryset).select_related(
            "candidate_id", "template_task_id", "assignee_id"
        )
        self.queryset = queryset
        return queryset

    def get_context_data(self, **kwargs):
        # HorillaListView only renders the accordion/group_by_table.html
        # template (rather than a flat table) when _saved_filters has a
        # "field" key - forcing it here, rather than relying on the user
        # picking "Group By: Candidate" from the generic nav dropdown,
        # makes the candidate-accordion layout this view's only mode.
        self._saved_filters = self._saved_filters.copy()
        self._saved_filters["field"] = "candidate_id"
        return super().get_context_data(**kwargs)
