"""
onboarding/checklist_access.py

Shared visibility/permission rules for CandidateChecklistTask, reused by
the "My Tasks" FBV update endpoints (task_templates.py) and the
Kanban/List CBVs (cbv/checklist_tasks.py) so the rule lives in one place:

- onboarding.view_candidatechecklisttask -> can see every candidate's tasks,
  otherwise only tasks assigned to the requester's own Employee record.
- onboarding.change_candidatechecklisttask -> can update ANY task,
  otherwise only tasks assigned to the requester's own Employee record.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from onboarding.models import CandidateChecklistTask
from onboarding.stepper import _maybe_mark_onboarding_complete
from recruitment.models import Candidate


def visible_tasks_queryset(request, queryset=None):
    """
    Scopes a CandidateChecklistTask queryset to what this user may see.
    """
    qs = CandidateChecklistTask.objects.all() if queryset is None else queryset
    if request.user.has_perm("onboarding.view_candidatechecklisttask"):
        return qs
    employee = getattr(request.user, "employee_get", None)
    if not employee:
        return qs.none()
    return qs.filter(assignee_id=employee)


def visible_candidates_queryset(request):
    """
    Candidates who have at least one checklist task visible to this user -
    backs the My Tasks candidate picker.
    """
    candidate_ids = (
        visible_tasks_queryset(request).values_list("candidate_id", flat=True).distinct()
    )
    return Candidate.objects.filter(id__in=candidate_ids)


def can_manage_task(request, task):
    """
    Whether this user may update the given task.
    """
    if request.user.has_perm("onboarding.change_candidatechecklisttask"):
        return True
    employee = getattr(request.user, "employee_get", None)
    return bool(employee) and task.assignee_id_id == employee.pk


def apply_status_change(task, new_status):
    """
    The one place status/completed_at/onboarding-complete logic lives,
    shared by the self-update endpoint and the Kanban drag-drop pre-move
    check (which is also the authoritative persist point for a drag-drop
    move - see cbv/checklist_tasks.py for why).

    Returns (ok: bool, error_message: str | None).
    """
    if new_status not in dict(CandidateChecklistTask.STATUS_CHOICES):
        return False, str(_("Invalid status."))
    if new_status != task.status:
        task.status_updated_at = timezone.now()
    task.status = new_status
    task.completed_at = timezone.now() if new_status == "done" else None
    task.save()
    if new_status == "done":
        _maybe_mark_onboarding_complete(task.candidate_id)
    return True, None
