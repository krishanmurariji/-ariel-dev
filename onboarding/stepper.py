"""
stepper.py

Full-page "Configure Onboarding" stepper for a candidate: Offer, Hiring &
Closure -> Pre-Joining Checklist -> Job Profile -> Post-Joining Checklist
-> Roles & Responsibility -> Employee Checklist -> Finalize. Steps always
run in this fixed order (no per-step skipping).
"Offer, Hiring & Closure" (step key "documents", kept from when this was
just Document Verification) bundles three tabs - Offer Letter/LOI,
Document Verification, and Background Verification - so all of a
candidate's closing paperwork lives in one place instead of Document
Verification sitting alone as step 1 and the offer letter separately
near the end.
Employee Checklist tasks are always done by the candidate themself (no
assignee to pick), unlike pre_joining/post_joining tasks which are done by
someone else about the candidate. Steps render as
htmx fragments swapped into the page's right-hand content column; the
left column (step list) persists across navigation, and any step can be
jumped to directly, up to the furthest one actually reached.
"""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _

from base.models import Department, EmployeeShift, EmployeeType, JobPosition, JobRole, WorkType
from employee.models import Employee
from horilla.decorators import hx_request_required, login_required, permission_required
from onboarding.forms import (
    BackgroundVerificationForm,
    ChecklistTemplateSelectForm,
    OnboardingDetailsForm,
    OnboardingDocumentRequestForm,
    RolesResponsibilityForm,
    WelcomeMessageForm,
)
from onboarding.mailers import (
    notify_onboarding_buddy_assigned,
    notify_reporting_manager_assigned,
    notify_reporting_manager_removed,
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
    ("documents", _("Offer, Hiring & Closure")),
    ("pre_joining", _("Pre-Joining Checklist")),
    ("details", _("Job Profile")),
    ("post_joining", _("Post-Joining Checklist")),
    ("roles", _("Roles & Responsibility")),
    ("employee_checklist", _("Employee Checklist")),
    ("finalize", _("Finalize")),
]
STEP_KEYS = [key for key, _label in STEPS]
STEP_LABELS = dict(STEPS)
PHASE_STEP_LABELS = {
    "pre_joining": _("Pre-Joining Checklist"),
    "post_joining": _("Post-Joining Checklist"),
    "employee_checklist": _("Employee Checklist"),
}
# Phases whose tasks the candidate is the one who does the work, so the
# stepper never asks for an employee assignee - unlike pre_joining/
# post_joining, which are always done by someone else about the candidate.
CANDIDATE_ASSIGNED_PHASES = {"employee_checklist"}

# CandidateDocument.clean()'s size/format checks only run when a
# document_request_id is attached (they read its configured max_size/
# format), which the HR ad hoc "Documents" tab upload never has - so it
# needs its own cap rather than relying on that check.
DEFAULT_MAX_UPLOAD_SIZE_MB = 25


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


def _last_saved_welcome_message():
    """
    Fallback prefill for a candidate who doesn't have their own welcome
    message yet - HR usually writes near-identical welcome notes for
    every new hire, so starting from whatever was last actually saved
    (for any candidate) saves re-typing it each time. Only ever used as
    the form's initial value, never written to the candidate's own row
    unless they actually save it themselves.
    """
    row = (
        OnboardingCandidateDetail.objects.exclude(welcome_message="")
        .exclude(welcome_message__isnull=True)
        .order_by("-welcome_message_updated_at")
        .first()
    )
    return row.welcome_message if row else ""


def _task_groups(candidate, phase, template_ids):
    """
    Tasks for the selected Task Groups, grouped under their group heading.
    Removing a task from a candidate's checklist happens at the group level
    only - deselecting a Task Group above removes all of its tasks - so
    there's no per-task exclusion state to track here.
    """
    # TaskTemplateTask has no company-scoped manager of its own - route the
    # requested template_ids through TaskTemplate.objects (which is
    # HorillaCompanyManager-scoped) first, so a request naming another
    # company's TaskTemplate silently drops it instead of leaking that
    # company's group/task titles into this candidate's checklist.
    visible_template_ids = TaskTemplate.objects.filter(pk__in=template_ids)
    tasks = (
        TaskTemplateTask.objects.filter(template_id__in=visible_template_ids)
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
        if existing_row and existing_row.due_date:
            due_date = existing_row.due_date
        elif template.default_due_offset:
            # Pre-Joining Checklist tasks are configured before Job
            # Profile (where joining_date actually gets set), so there's
            # often no joining date yet at this point - fall back to
            # today rather than leaving the field blank just because the
            # candidate hasn't been given a joining date yet.
            reference_date = candidate.joining_date or timezone.now().date()
            due_date = reference_date + timedelta(days=template.default_due_offset)
        else:
            due_date = None
        group["rows"].append(
            {
                "task": task,
                "assignee_id": (
                    existing_row.assignee_id_id
                    if existing_row
                    else template.default_assignee_id_id
                ),
                "due_date": due_date,
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
    form = ChecklistTemplateSelectForm(
        phase=phase, initial={"templates": existing_template_ids}
    )
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

    if step_key == "details":
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
                    # Falls back to what the current Default Probation
                    # Period setting would compute, when nothing has been
                    # saved yet - Candidate.save() only auto-fills this
                    # once, at the moment of hiring, so a default period
                    # set (or changed) afterward would otherwise never
                    # show up here at all.
                    "probation_end": candidate.get_default_probation_end(),
                },
            )
        template = "onboarding/stepper/step_details.html"

    elif step_key in ("pre_joining", "post_joining", "employee_checklist"):
        context.update(_checklist_context(candidate, step_key))
        template = "onboarding/stepper/step_checklist.html"

    elif step_key == "documents":
        context.update(_document_context(candidate))
        context.setdefault(
            "welcome_form",
            WelcomeMessageForm(
                instance=detail,
                initial={
                    "welcome_message": detail.welcome_message
                    or _last_saved_welcome_message()
                },
            ),
        )
        context.setdefault(
            "background_form", BackgroundVerificationForm(instance=detail)
        )
        context["latest_loi"] = candidate.get_latest_loi_response()
        template = "onboarding/stepper/step_offer_hiring_closure.html"

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
        "employee_checklist_count": candidate.checklist_tasks.filter(
            phase="employee_checklist"
        ).count(),
        "portal_sent": candidate.start_onboard,
    }


@login_required
@permission_required("recruitment.change_candidate")
def finalize_onboarding(request, pk):
    """
    Finalize's "Finish Configuration" action - deliberately does not touch
    the shared email_send view (recruitment/onboarding/urls.py "email-send"),
    which is also used by the recruitment pipeline and the candidate list's
    own bulk "Send Portal" action. This just flips the status flag, with no
    email and no portal token, matching the explicit "no mail" requirement.
    """
    candidate = get_object_or_404(Candidate, pk=pk)
    detail = _get_detail(candidate)
    if request.method == "POST":
        if detail.furthest_step_key != "finalize":
            messages.error(
                request, _("Please complete every step before finishing configuration.")
            )
            return redirect(reverse("onboarding-stepper-page", kwargs={"pk": candidate.pk}))
        # Reporting Manager/Buddy assignment mails only go out the first
        # time this candidate's configuration is finalized - re-finalizing
        # (not currently possible from the UI once start_onboard is set,
        # but guarded here regardless) must never re-send them.
        is_first_finalize = not candidate.start_onboard
        Candidate.objects.filter(pk=candidate.pk).update(start_onboard=True)
        if not detail.configuration_finalized_at:
            detail.configuration_finalized_at = timezone.now()
            detail.save(update_fields=["configuration_finalized_at"])
        if is_first_finalize:
            notify_reporting_manager_assigned(candidate, detail.reporting_manager_id)
            notify_onboarding_buddy_assigned(candidate, detail.buddy_id)
        try:
            stage = candidate.recruitment_id.onboarding_stage.first()
            CandidateStage.objects.get_or_create(
                candidate_id=candidate, defaults={"onboarding_stage_id": stage}
            )
        except Exception:
            pass
        messages.success(request, _("Onboarding configuration completed"))
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

    if not _is_step_unlocked(detail, step_key):
        messages.error(
            request, _("Please complete the previous steps first.")
        )
        step_key = detail.furthest_step_key
        return _render_step(request, candidate, detail, step_key)

    if request.method == "POST":
        error = _save_step(request, candidate, detail, step_key)
        if error:
            messages.error(request, error)
            return _render_step(request, candidate, detail, step_key)
        next_key = _next_key(step_key)
        _advance_furthest_if_needed(detail, next_key)
        return _render_step(request, candidate, detail, next_key)

    _advance_furthest_if_needed(detail, step_key)
    return _render_step(request, candidate, detail, step_key)


def _save_step(request, candidate, detail, step_key):
    """
    Returns an error message string if the step failed to save/validate,
    otherwise None.
    """
    if step_key == "documents":
        # Both forms are bound to the same `detail` instance, so validating
        # both before saving either leaves `detail` fully populated from
        # one save() call - no need for two separate writes.
        welcome_form = WelcomeMessageForm(request.POST, request.FILES, instance=detail)
        if not welcome_form.is_valid():
            return _("Please check the welcome message.")
        background_form = BackgroundVerificationForm(request.POST, instance=detail)
        if not background_form.is_valid():
            return _("Please check the background verification notes.")
        if detail.welcome_message:
            detail.welcome_message_updated_at = timezone.now()
        detail.save()
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
        previous_reporting_manager = detail.reporting_manager_id
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

        # Only notify on a change made *after* the initial finalize (which
        # already sent its own assignment mail) - re-saving this step
        # during the first-time setup, before finalize, must stay silent
        # even if the reporting manager is picked, cleared, and re-picked
        # a few times along the way.
        if candidate.start_onboard and previous_reporting_manager != detail.reporting_manager_id:
            if previous_reporting_manager:
                notify_reporting_manager_removed(candidate, previous_reporting_manager)
            if detail.reporting_manager_id:
                notify_reporting_manager_assigned(candidate, detail.reporting_manager_id)
        return None

    if step_key in ("pre_joining", "post_joining", "employee_checklist"):
        return _save_checklist_tasks(request, candidate, step_key)

    if step_key == "roles":
        form = RolesResponsibilityForm(request.POST, instance=detail)
        if not form.is_valid():
            return _("Please check the roles & responsibilities.")
        form.save()
        return None

    return None


def _save_checklist_tasks(request, candidate, phase):
    candidate_assigned = phase in CANDIDATE_ASSIGNED_PHASES
    task_ids = request.POST.getlist("task_id")
    # See the matching comment in _task_groups - TaskTemplateTask isn't
    # company-scoped on its own, so route through TaskTemplate.objects
    # (which is) to keep a POSTed task_id from another company's template
    # from being honored here.
    template_tasks = {
        str(task.pk): task
        for task in TaskTemplateTask.objects.filter(
            pk__in=task_ids, template_id__in=TaskTemplate.objects.all()
        ).select_related("template_id")
    }
    assignee_ids = {
        request.POST.get(f"assignee_{task_id}")
        for task_id in task_ids
        if request.POST.get(f"assignee_{task_id}")
    }
    employees = {
        str(employee.pk): employee
        for employee in Employee.objects.filter(pk__in=assignee_ids)
    }

    candidate_own_employee = (
        Employee.objects.filter(pk=_candidate_own_employee_id(candidate)).first()
        if candidate_assigned
        else None
    )

    unassigned = []
    for task_id in task_ids:
        template_task = template_tasks.get(task_id)
        if not template_task:
            continue
        if candidate_assigned:
            # Employee Checklist tasks are self-service - the stepper form
            # never shows an assignee picker for this phase (_task_rows.html)
            # - so the assignee is the candidate's own Employee record
            # (once they have one), not left blank. That also makes these
            # tasks actually show up in the candidate's own My Tasks once
            # they're an employee, since visibility there is assignee-based
            # (checklist_access.visible_tasks_queryset).
            assignee = candidate_own_employee
        else:
            assignee_raw = request.POST.get(f"assignee_{task_id}") or None
            assignee = employees.get(assignee_raw) if assignee_raw else None
            if not assignee:
                unassigned.append(template_task.title)
        # The date input is pre-filled (see _task_groups' matching due_date
        # computation) with the group's default offset from the candidate's
        # joining date, but it's a real date picker - whatever the user
        # actually submits wins, whether that's the untouched prefill or a
        # manually-picked date. Only falls back to auto-computing here if
        # the field came through empty/unparseable (e.g. JS disabled).
        due_date = parse_date(request.POST.get(f"due_date_{task_id}") or "")
        if due_date is None:
            due_offset = template_task.template_id.default_due_offset
            if due_offset:
                reference_date = candidate.joining_date or timezone.now().date()
                due_date = reference_date + timedelta(days=due_offset)
            else:
                due_date = None
        # Not update_or_create(): it wraps get_or_create() in
        # select_for_update(), and CandidateChecklistTask's manager
        # (HorillaCompanyManager) adds a DISTINCT to every queryset once a
        # company is selected - Postgres rejects "FOR UPDATE" combined with
        # DISTINCT outright, so update_or_create() 500s here in production
        # (sqlite doesn't enforce that combination, so this never showed up
        # locally). A plain get-then-save/create sidesteps the locking
        # entirely, which this per-request save doesn't need anyway.
        existing_task = CandidateChecklistTask.objects.filter(
            candidate_id=candidate, template_task_id=template_task, phase=phase
        ).first()
        if existing_task:
            existing_task.title = template_task.title
            existing_task.assignee_id = assignee
            existing_task.due_date = due_date
            existing_task.save(update_fields=["title", "assignee_id", "due_date"])
            _task_obj, created = existing_task, False
        else:
            _task_obj = CandidateChecklistTask.objects.create(
                candidate_id=candidate,
                template_task_id=template_task,
                phase=phase,
                title=template_task.title,
                assignee_id=assignee,
                due_date=due_date,
            )
            created = True
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
            # Nothing else guards against requesting the same document
            # twice for one candidate (e.g. a double click on "Request
            # Document") - each click created its own separate
            # CandidateDocumentRequest row with the same title, so one
            # could end up approved while its identically-named sibling
            # still sat there unfilled, reading like a stale/duplicate
            # entry that "won't go away" even after the other was dealt
            # with. Block an exact-title repeat instead - locking the
            # candidate row for the duration of the check-then-create
            # closes the race between two near-simultaneous submissions
            # (candidate_id is a ManyToManyField, so there's no direct FK
            # to lock on CandidateDocumentRequest itself).
            with transaction.atomic():
                Candidate.objects.select_for_update().get(pk=candidate.pk)
                duplicate = CandidateDocumentRequest.objects.filter(
                    candidate_id=candidate, title__iexact=data["title"]
                ).exists()
                if not duplicate:
                    doc_request = CandidateDocumentRequest.objects.create(
                        title=data["title"],
                        format=data["format"],
                        max_size=data["max_size"],
                        is_mandatory=data["is_mandatory"],
                        description=data["description"],
                    )
                    doc_request.candidate_id.add(candidate)
            if duplicate:
                # The modal's own hx-target is #documentListContainer (see
                # document_request_modal.html), not the modal body itself,
                # and its form unconditionally closes the modal ~150ms
                # after any submit - so a field error rendered back into
                # the modal would never actually be seen. Surface this via
                # the same messages framework "Document request created"
                # already relies on below, and re-render the unchanged
                # list so the response still matches what htmx expects to
                # swap into that target.
                messages.error(
                    request,
                    _("This candidate already has a document request titled \"%(title)s\".")
                    % {"title": data["title"]},
                )
                return render(
                    request,
                    "onboarding/stepper/_document_list.html",
                    {"candidate": candidate, **_document_context(candidate)},
                )
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
        # The CandidateDocument placeholder row (no file) is auto-created the
        # moment a request is made and protected against deletion, so it has
        # to go first regardless of whether a file was ever submitted.
        CandidateDocument.objects.filter(document_request_id=doc_request).delete()
        doc_request.delete()
        messages.success(request, _("Document request removed"))
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
            if uploaded.size > DEFAULT_MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                messages.error(
                    request,
                    _("File size exceeds the %(limit)s MB limit.")
                    % {"limit": DEFAULT_MAX_UPLOAD_SIZE_MB},
                )
                return render(
                    request,
                    "onboarding/stepper/_document_list.html",
                    {"candidate": candidate, **_document_context(candidate)},
                )
            document = CandidateDocument(
                title=title,
                candidate_id=candidate,
                document=uploaded,
                status="submitted",
            )
            try:
                document.full_clean()
                document.save()
                messages.success(request, _("Document uploaded"))
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
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
    phase_groups = [
        {"key": key, "label": label, "tasks": [task for task in tasks if task.phase == key]}
        for key, label in CandidateChecklistTask.PHASE_CHOICES
    ]
    return {"candidate": candidate, "tasks": tasks, "phase_groups": phase_groups}


@login_required
@permission_required("recruitment.view_candidate")
def candidate_tasks_tab(request, pk, **kwargs):
    """
    Candidate profile tab listing every CandidateChecklistTask for this
    candidate - task, assignee, and completion state.
    """
    candidate = get_object_or_404(Candidate, pk=pk)
    return render(
        request, "onboarding/candidate_tasks_tab.html", _candidate_tasks_context(candidate)
    )


@login_required
@permission_required("recruitment.change_candidate")
def send_portal_status(request, pk):
    """
    Status fragment shown inside the "Send Onboarding Portal" confirmation
    modal, right before the actual send - either a warning that Configure
    Onboarding hasn't been done yet for this candidate (no reporting
    manager/buddy/checklist tasks exist at all), or, once it has, a
    Pre-Joining Checklist progress bar - so whoever's about to notify the
    candidate can see what state things are actually in first.

    Called via plain jQuery $.get() (see actions.html / send_mail.html),
    not htmx, so this deliberately has no @hx_request_required - that
    decorator would 405 every one of these calls since none of them ever
    carry an HX-Request header.
    """
    candidate = get_object_or_404(Candidate, pk=pk)
    configured = OnboardingCandidateDetail.objects.filter(candidate_id=candidate).exists()
    context = {"candidate": candidate, "configured": configured}
    if configured:
        pre_joining_tasks = candidate.checklist_tasks.filter(phase="pre_joining")
        total = pre_joining_tasks.count()
        done = pre_joining_tasks.filter(status="done").count()
        context.update(
            total=total,
            done=done,
            percent=round(done / total * 100) if total else 0,
        )
    return render(request, "onboarding/stepper/_send_portal_status.html", context)


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
