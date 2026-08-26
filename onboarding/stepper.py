"""
stepper.py

Full-page "Start Onboarding" stepper for a candidate: Pre-Joining
Checklist -> Document Verification -> Offer Letter -> Job Profile ->
Post-Joining Checklist -> Roles & Responsibility -> Finalize. Steps
always run in this fixed order (no per-step skipping). Steps render as
htmx fragments swapped into the page's right-hand content column; the
left column (step list) persists across navigation, and any step can be
jumped to directly, up to the furthest one actually reached.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.models import Department, EmployeeShift, EmployeeType, JobPosition, JobRole, WorkType
from employee.models import Employee
from horilla.decorators import hx_request_required, login_required, permission_required
from onboarding.forms import (
    ChecklistTemplateSelectForm,
    OnboardingDetailsForm,
    OnboardingDocumentRequestForm,
    RolesResponsibilityForm,
    WelcomeMessageForm,
)
from onboarding.models import (
    CandidateChecklistTask,
    CandidateStage,
    OnboardingCandidateDetail,
    TaskTemplate,
    TaskTemplateTask,
)
from recruitment.models import Candidate, CandidateDocument, CandidateDocumentRequest
from recruitment.views.views import notify_candidate_of_document_rejection

STEPS = [
    ("pre_joining", _("Pre-Joining Checklist")),
    ("documents", _("Document Verification")),
    ("welcome", _("Offer Letter")),
    ("details", _("Job Profile")),
    ("post_joining", _("Post-Joining Checklist")),
    ("roles", _("Roles & Responsibility")),
    ("finalize", _("Finalize")),
]
STEP_KEYS = [key for key, _label in STEPS]
STEP_LABELS = dict(STEPS)
PHASE_STEP_LABELS = {
    "pre_joining": _("Pre-Joining Checklist"),
    "post_joining": _("Post-Joining Checklist"),
}


def _get_detail(candidate):
    detail, _created = OnboardingCandidateDetail.objects.get_or_create(candidate_id=candidate)
    return detail


def _maybe_mark_onboarding_complete(candidate):
    """
    Marks the candidate's onboarding fully complete the first time every
    one of their checklist tasks (pre- and post-joining alike) reaches
    Done. One-directional - never unset once set, even if a task is later
    reopened (e.g. a proof removal reverting a task to todo).
    """
    detail = _get_detail(candidate)
    if detail.onboarding_completed_at:
        return
    tasks = candidate.checklist_tasks.all()
    if tasks.exists() and not tasks.exclude(status="done").exists():
        detail.onboarding_completed_at = timezone.now()
        detail.save(update_fields=["onboarding_completed_at"])


def _next_key(current_key):
    idx = STEP_KEYS.index(current_key)
    return STEP_KEYS[idx + 1] if idx + 1 < len(STEP_KEYS) else STEP_KEYS[-1]


def _prev_key(current_key):
    idx = STEP_KEYS.index(current_key)
    return STEP_KEYS[idx - 1] if idx > 0 else STEP_KEYS[0]


def _is_step_unlocked(detail, step_key):
    """
    A step is reachable once the candidate has gotten at least one step
    away from it - i.e. it's already been reached, or it's the very next
    one after the furthest point reached so far. Anything further is
    locked, so the stepper's own nav can't be used to skip past steps
    (and their validation) that haven't actually been completed yet.
    """
    return STEP_KEYS.index(step_key) <= STEP_KEYS.index(detail.furthest_step_key) + 1


def _advance_furthest_if_needed(detail, step_key):
    if STEP_KEYS.index(step_key) > STEP_KEYS.index(detail.furthest_step_key):
        detail.furthest_step_key = step_key
        detail.save(update_fields=["furthest_step_key"])


def _candidate_own_employee_id(candidate):
    """
    Finds the Employee record that corresponds to this candidate, if any -
    checking converted_employee_id first, but falling back to an email
    match, since the self-service onboarding portal's own employee_creation
    view never sets converted_employee_id (only the recruitment-side force
    "Convert to Employee" action does), even though it creates a real
    Employee with the candidate's email.
    """
    if candidate.converted_employee_id_id:
        return candidate.converted_employee_id_id
    match = Employee.objects.filter(email=candidate.email).first()
    return match.pk if match else None


def _assignable_employees(candidate):
    """
    Employees selectable as reporting manager / buddy / task assignee -
    excludes the candidate's own employee record, since a candidate can't
    be their own manager/buddy/task doer.
    """
    employees = Employee.objects.filter(is_active=True)
    own_id = _candidate_own_employee_id(candidate)
    if own_id:
        employees = employees.exclude(pk=own_id)
    return employees


def _nav_context(candidate, detail, active_key):
    nav_steps = [{"key": key, "label": label} for key, label in STEPS]
    return {
        "candidate": candidate,
        "detail": detail,
        "nav_steps": nav_steps,
        "active_key": active_key,
        "active_index": STEP_KEYS.index(active_key),
        "unlocked_index": STEP_KEYS.index(detail.furthest_step_key) + 1,
    }


def _document_context(candidate):
    return {
        "requests": CandidateDocumentRequest.objects.filter(candidate_id=candidate),
        "documents": CandidateDocument.objects.filter(candidate_id=candidate).order_by("-id"),
    }


def _task_groups(candidate, phase, template_ids):
    """
    Tasks for the selected Task Groups, grouped under their group heading.
    Removing a task from a candidate's checklist happens at the group level
    only - deselecting a Task Group above removes all of its tasks - so
    there's no per-task exclusion state to track here.
    """
    tasks = (
        TaskTemplateTask.objects.filter(template_id__in=template_ids)
        .select_related("template_id")
        .order_by("template_id__id", "sequence", "id")
    )
    existing = {
        row.template_task_id_id: row
        for row in candidate.checklist_tasks.filter(phase=phase, template_task_id__in=tasks)
    }
    groups = []
    groups_by_id = {}
    for task in tasks:
        template = task.template_id
        group = groups_by_id.get(template.id)
        if group is None:
            group = {"template": template, "rows": []}
            groups_by_id[template.id] = group
            groups.append(group)
        existing_row = existing.get(task.id)
        group["rows"].append(
            {
                "task": task,
                "assignee_id": existing_row.assignee_id_id if existing_row else None,
                "requires_feedback": existing_row.requires_feedback if existing_row else False,
                "is_mandatory": (
                    existing_row.is_mandatory if existing_row else task.is_mandatory
                ),
                "requires_proof": (
                    existing_row.requires_proof if existing_row else task.requires_proof
                ),
            }
        )
    return groups


def _checklist_context(candidate, phase):
    existing_template_ids = list(
        candidate.checklist_tasks.filter(phase=phase)
        .exclude(template_task_id__isnull=True)
        .values_list("template_task_id__template_id", flat=True)
        .distinct()
    )
    form = ChecklistTemplateSelectForm(initial={"templates": existing_template_ids})
    return {
        "form": form,
        "phase": phase,
        "phase_label": PHASE_STEP_LABELS[phase],
        "groups": _task_groups(candidate, phase, existing_template_ids),
        "employees": _assignable_employees(candidate),
    }


def _render_step(request, candidate, detail, step_key, extra=None):
    if step_key not in STEP_KEYS:
        return _render_step(request, candidate, detail, STEP_KEYS[0], extra)

    context = _nav_context(candidate, detail, step_key)
    context["back_key"] = _prev_key(step_key) if step_key != STEP_KEYS[0] else None
    context["next_key"] = _next_key(step_key) if step_key != "finalize" else None
    context.update(extra or {})

    if step_key == "welcome":
        context.setdefault(
            "form",
            WelcomeMessageForm(
                instance=detail,
                initial={"offer_letter_status": candidate.offer_letter_status},
            ),
        )
        template = "onboarding/stepper/step_welcome.html"

    elif step_key == "details":
        if "form" not in context:
            context["form"] = OnboardingDetailsForm(
                exclude_employee_id=_candidate_own_employee_id(candidate),
                initial={
                    "job_position_id": candidate.job_position_id_id,
                    "department_id": detail.department_id_id,
                    "job_role_id": detail.job_role_id_id,
                    "reporting_manager_id": detail.reporting_manager_id_id,
                    "buddy_id": detail.buddy_id_id,
                    "shift_id": detail.shift_id_id,
                    "work_type_id": detail.work_type_id_id,
                    "employee_type_id": detail.employee_type_id_id,
                    "date_of_joining": candidate.joining_date,
                    "probation_end": candidate.probation_end,
                },
            )
        template = "onboarding/stepper/step_details.html"

    elif step_key in ("pre_joining", "post_joining"):
        context.update(_checklist_context(candidate, step_key))
        template = "onboarding/stepper/step_checklist.html"

    elif step_key == "documents":
        context.update(_document_context(candidate))
        template = "onboarding/stepper/step_documents.html"

    elif step_key == "roles":
        context.setdefault("form", RolesResponsibilityForm(instance=detail))
        template = "onboarding/stepper/step_roles.html"

    else:
        context.update(_finalize_context(candidate, detail))
        template = "onboarding/stepper/step_finalize.html"

    return render(request, template, context)


def _finalize_context(candidate, detail):
    doc_requests = CandidateDocumentRequest.objects.filter(candidate_id=candidate)
    mandatory_requests = doc_requests.filter(is_mandatory=True)
    return {
        "mandatory_requests": mandatory_requests,
        "total_requests": doc_requests.count(),
        "pre_joining_count": candidate.checklist_tasks.filter(phase="pre_joining").count(),
        "post_joining_count": candidate.checklist_tasks.filter(phase="post_joining").count(),
        "portal_sent": candidate.start_onboard,
    }


@login_required
@permission_required("recruitment.change_candidate")
def finalize_onboarding(request, pk):
    """
    Finalize's "Start Onboarding" action - deliberately does not touch the
    shared email_send view (recruitment/onboarding/urls.py "email-send"),
    which is also used by the recruitment pipeline and the candidate list's
    own bulk "Send Portal" action. This just flips the status flag, with no
    email and no portal token, matching the explicit "no mail" requirement.
    """
    candidate = get_object_or_404(Candidate, pk=pk)
    if request.method == "POST":
        Candidate.objects.filter(pk=candidate.pk).update(start_onboard=True)
        try:
            stage = candidate.recruitment_id.onboarding_stage.first()
            CandidateStage.objects.get_or_create(
                candidate_id=candidate, defaults={"onboarding_stage_id": stage}
            )
        except Exception:
            pass
        messages.success(request, _("Onboarding marked as started"))
    next_url = request.POST.get("next") or reverse("candidates-view")
    return redirect(next_url)


@login_required
@permission_required("recruitment.change_candidate")
def onboarding_stepper_page(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)
    detail = _get_detail(candidate)
    return render(
        request,
        "onboarding/stepper/page.html",
        {
            **_nav_context(candidate, detail, STEP_KEYS[0]),
            "landing_key": STEP_KEYS[0],
        },
    )


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def stepper_step(request, pk, step_key):
    candidate = get_object_or_404(Candidate, pk=pk)
    detail = _get_detail(candidate)
    if step_key not in STEP_KEYS:
        step_key = STEP_KEYS[0]

    if request.method == "POST":
        error = _save_step(request, candidate, detail, step_key)
        if error:
            messages.error(request, error)
            return _render_step(request, candidate, detail, step_key)
        next_key = _next_key(step_key)
        _advance_furthest_if_needed(detail, next_key)
        return _render_step(request, candidate, detail, next_key)

    if not _is_step_unlocked(detail, step_key):
        messages.error(
            request, _("Please complete the previous steps first.")
        )
        step_key = detail.furthest_step_key
    else:
        _advance_furthest_if_needed(detail, step_key)

    return _render_step(request, candidate, detail, step_key)


def _save_step(request, candidate, detail, step_key):
    """
    Returns an error message string if the step failed to save/validate,
    otherwise None.
    """
    if step_key == "welcome":
        form = WelcomeMessageForm(request.POST, request.FILES, instance=detail)
        if not form.is_valid():
            return _("Please check the welcome message.")
        form.save()
        if form.cleaned_data.get("offer_letter_status"):
            candidate.offer_letter_status = form.cleaned_data["offer_letter_status"]
            candidate.save()
        return None

    if step_key == "details":
        form = OnboardingDetailsForm(
            request.POST, exclude_employee_id=_candidate_own_employee_id(candidate)
        )
        if not form.is_valid():
            return _("Please check the job profile details.")
        data = form.cleaned_data
        candidate.job_position_id = (
            JobPosition.objects.filter(pk=data["job_position_id"]).first()
            if data["job_position_id"]
            else None
        )
        candidate.joining_date = data["date_of_joining"]
        candidate.probation_end = data["probation_end"]
        candidate.save()

        detail.department_id = (
            Department.objects.filter(pk=data["department_id"]).first()
            if data["department_id"]
            else None
        )
        detail.job_role_id = (
            JobRole.objects.filter(pk=data["job_role_id"]).first()
            if data["job_role_id"]
            else None
        )
        detail.reporting_manager_id = (
            Employee.objects.filter(pk=data["reporting_manager_id"]).first()
            if data["reporting_manager_id"]
            else None
        )
        detail.buddy_id = (
            Employee.objects.filter(pk=data["buddy_id"]).first()
            if data["buddy_id"]
            else None
        )
        detail.shift_id = (
            EmployeeShift.objects.filter(pk=data["shift_id"]).first()
            if data["shift_id"]
            else None
        )
        detail.work_type_id = (
            WorkType.objects.filter(pk=data["work_type_id"]).first()
            if data["work_type_id"]
            else None
        )
        detail.employee_type_id = (
            EmployeeType.objects.filter(pk=data["employee_type_id"]).first()
            if data["employee_type_id"]
            else None
        )
        detail.save()
        return None

    if step_key in ("pre_joining", "post_joining"):
        return _save_checklist_tasks(request, candidate, step_key)

    if step_key == "roles":
        form = RolesResponsibilityForm(request.POST, instance=detail)
        if not form.is_valid():
            return _("Please check the roles & responsibilities.")
        form.save()
        return None

    return None


def _save_checklist_tasks(request, candidate, phase):
    task_ids = request.POST.getlist("task_id")
    unassigned = []
    for task_id in task_ids:
        template_task = TaskTemplateTask.objects.filter(pk=task_id).first()
        if not template_task:
            continue
        assignee_raw = request.POST.get(f"assignee_{task_id}") or None
        assignee = Employee.objects.filter(pk=assignee_raw).first() if assignee_raw else None
        requires_feedback = bool(request.POST.get(f"feedback_{task_id}"))
        is_mandatory = bool(request.POST.get(f"mandatory_{task_id}"))
        requires_proof = bool(request.POST.get(f"proof_{task_id}"))
        if not assignee:
            unassigned.append(template_task.title)
        _task_obj, created = CandidateChecklistTask.objects.update_or_create(
            candidate_id=candidate,
            template_task_id=template_task,
            phase=phase,
            defaults={
                "title": template_task.title,
                "assignee_id": assignee,
                "requires_feedback": requires_feedback,
                "is_mandatory": is_mandatory,
                "requires_proof": requires_proof,
            },
        )
        if created:
            _task_obj.status_updated_at = timezone.now()
            _task_obj.save(update_fields=["status_updated_at"])
    submitted_ids = [int(task_id) for task_id in task_ids if task_id.isdigit()]
    candidate.checklist_tasks.filter(phase=phase).exclude(
        template_task_id__in=submitted_ids
    ).delete()

    if unassigned:
        return _("Choosing an assignee is required for every task: %(tasks)s") % {
            "tasks": ", ".join(unassigned)
        }
    return None


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def checklist_task_rows(request, pk, phase):
    candidate = get_object_or_404(Candidate, pk=pk)
    template_ids = request.GET.getlist("templates")
    context = {
        "candidate": candidate,
        "phase": phase,
        "groups": _task_groups(candidate, phase, template_ids),
        "employees": _assignable_employees(candidate),
    }
    return render(request, "onboarding/stepper/_task_rows.html", context)


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def document_request_modal(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)
    if request.method == "POST":
        form = OnboardingDocumentRequestForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            doc_request = CandidateDocumentRequest.objects.create(
                title=data["title"],
                format=data["format"],
                max_size=data["max_size"],
                is_mandatory=data["is_mandatory"],
                description=data["description"],
            )
            doc_request.candidate_id.add(candidate)
            messages.success(request, _("Document request created"))
            return render(
                request,
                "onboarding/stepper/_document_list.html",
                {"candidate": candidate, **_document_context(candidate)},
            )
    else:
        form = OnboardingDocumentRequestForm()
    return render(
        request,
        "onboarding/stepper/document_request_modal.html",
        {"candidate": candidate, "form": form},
    )


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def document_request_cancel(request, pk, req_id):
    candidate = get_object_or_404(Candidate, pk=pk)
    doc_request = get_object_or_404(
        CandidateDocumentRequest, pk=req_id, candidate_id=candidate
    )
    if request.method == "POST":
        # A CandidateDocument placeholder row (no file) is auto-created the
        # moment a request is made, so a plain .delete() would always hit
        # ProtectedError even when nothing was ever actually submitted -
        # only block cancelling once a real file exists.
        submitted = (
            CandidateDocument.objects.filter(document_request_id=doc_request)
            .exclude(document="")
            .exclude(document__isnull=True)
        )
        if submitted.exists():
            messages.error(
                request,
                _(
                    "A document has already been submitted for this request - "
                    "reject it instead of cancelling."
                ),
            )
        else:
            CandidateDocument.objects.filter(document_request_id=doc_request).delete()
            doc_request.delete()
            messages.success(request, _("Document request cancelled"))
    return render(
        request,
        "onboarding/stepper/_document_list.html",
        {"candidate": candidate, **_document_context(candidate)},
    )


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def document_upload(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        uploaded = request.FILES.get("document")
        if title and uploaded:
            CandidateDocument.objects.create(
                title=title,
                candidate_id=candidate,
                document=uploaded,
                status="submitted",
            )
            messages.success(request, _("Document uploaded"))
    return render(
        request,
        "onboarding/stepper/_document_list.html",
        {"candidate": candidate, **_document_context(candidate)},
    )


def _candidate_tasks_context(candidate):
    tasks = list(
        candidate.checklist_tasks.select_related("assignee_id", "template_task_id").order_by(
            "phase", "id"
        )
    )
    return {"candidate": candidate, "tasks": tasks}


@login_required
@permission_required("recruitment.view_candidate")
def candidate_tasks_tab(request, pk, **kwargs):
    """
    Candidate profile tab listing every CandidateChecklistTask for this
    candidate - task, assignee, completion state, and (once Done, only when
    Response Required is on) a toggle for whether the candidate confirmed
    they're satisfied with how the task was handled.
    """
    candidate = get_object_or_404(Candidate, pk=pk)
    return render(
        request, "onboarding/candidate_tasks_tab.html", _candidate_tasks_context(candidate)
    )


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def candidate_task_toggle_satisfaction(request, pk):
    task = get_object_or_404(CandidateChecklistTask, pk=pk)
    if request.method == "POST" and task.status == "done":
        task.candidate_satisfied = not task.candidate_satisfied
        task.candidate_feedback_at = timezone.now() if task.candidate_satisfied else None
        task.save()
    return render(
        request,
        "onboarding/_candidate_tasks_list.html",
        _candidate_tasks_context(task.candidate_id),
    )


@login_required
@hx_request_required
@permission_required("recruitment.change_candidate")
def document_status_update(request, pk, doc_id, action):
    candidate = get_object_or_404(Candidate, pk=pk)
    document = get_object_or_404(CandidateDocument, pk=doc_id, candidate_id=candidate)
    if request.method == "POST":
        if action == "under_verification":
            document.status = "under_verification"
            document.save()
        elif action == "verified":
            document.status = "approved"
            document.save()
        elif action == "rejected":
            document.reject_reason = request.POST.get("reject_reason", "")
            document.status = "rejected"
            document.save()
            notify_candidate_of_document_rejection(document)
    return render(
        request,
        "onboarding/stepper/_document_list.html",
        {"candidate": candidate, **_document_context(candidate)},
    )
