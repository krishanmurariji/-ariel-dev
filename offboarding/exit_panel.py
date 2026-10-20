"""
offboarding/exit_panel.py

The Manage panel of an exit in progress - one window with a button per
section: Summary, Assets, Team, Leave settings and Finances (the last one
waits for the full & final settlement work).

Who sees what is the same as the rest of the Exit Process page: HR sees every
exit, a manager sees their own reports'. Changing anything needs the
"manage exits" permission, so a manager can look but not alter. The rules
themselves are in exit_manage.py.
"""

from datetime import date

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.urls import reverse
from django.views.decorators.http import require_POST

from employee.models import Employee
from horilla.decorators import hx_request_required, login_required
from offboarding.form_fields import TextCharField
from offboarding import exit_ai, exit_manage, exit_service, exit_tasks
from offboarding.exit_pages import _case_or_404, _me, exit_access_required, is_exit_admin
from offboarding.exit_service import ExitError
from offboarding.models import ExitActivity, OffboardingEmployee

SECTIONS = (
    ("summary", _("Summary")),
    ("assets", _("Assets")),
    ("finances", _("Finances")),
    ("survey", _("Survey")),
    ("team", _("Team")),
    ("tasks", _("Tasks")),
    ("leave", _("Leave settings")),
)
SECTION_KEYS = [key for key, _label in SECTIONS]
TAB_ICONS = {
    "summary": "grid-outline",
    "assets": "laptop-outline",
    "finances": "cash-outline",
    "survey": "chatbox-ellipses-outline",
    "team": "people-outline",
    "tasks": "checkbox-outline",
    "leave": "calendar-outline",
}


class AssetReturnForm(forms.Form):
    return_status = forms.ChoiceField(
        choices=[
            ("Healthy", _("Returned in good condition")),
            ("Minor damage", _("Returned with minor damage")),
            ("Major damage", _("Returned with major damage")),
        ],
        widget=forms.RadioSelect,
        initial="Healthy",
    )
    return_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    return_condition = TextCharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Notes on the condition")}),
    )


class HandoverForm(forms.Form):
    new_owner = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        error_messages={"required": _("Please choose who takes over.")},
    )

    def __init__(self, *args, case=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_owner"].queryset = exit_manage.possible_new_owners(case)


def _can_change(request):
    return exit_service.can_manage_exits(request.user)


def _panel(request, case, section, *, notice=None, error=None, form=None, returning=None):
    """Render the whole panel with one section open."""
    if section not in SECTION_KEYS or (case.status != "in_progress" and section != "summary"):
        section = "summary"
    employee = case.employee_id
    context = {
        "case": case,
        "employee": employee,
        "section": section,
        "notice": notice,
        "error": error,
        "can_change": _can_change(request),
        "is_admin": is_exit_admin(request.user),
    }
    states = {}
    if case.status == "in_progress":
        context["sections"] = exit_manage.sections(case)
        states = {item["key"]: item["state"] for item in context["sections"]}
    else:
        context["sections"] = []
    wanted = SECTIONS if case.status == "in_progress" else SECTIONS[:1]
    wanted = [(key, label) for key, label in wanted if key not in exit_manage.HIDDEN_SECTIONS]
    context["nav"] = [
        {"key": key, "label": label, "state": states.get(key), "icon": TAB_ICONS.get(key, "ellipse-outline")}
        for key, label in wanted
    ]
    done = sum(1 for item in context["sections"] if item["state"] == "done")
    context["steps_done"], context["steps_total"] = done, len(context["sections"])
    context["steps_percent"] = round(done * 100 / len(context["sections"])) if context["sections"] else 0
    if case.last_working_day:
        days = (case.last_working_day - date.today()).days
        context["days_left"], context["days_left_abs"] = days, abs(days)

    if section == "summary":
        context["letter"] = exit_service._letter_of(case)
        context["activities"] = ExitActivity.objects.entire().filter(
            employee_id=employee
        )[:30]
    elif section == "assets":
        context["holding"] = list(exit_manage.open_assets(employee))
        context["returned"] = list(exit_manage.returned_assets(employee))
        context["can_return"] = request.user.has_perm("asset.change_assetassignment")
        context["returning"] = returning
        context["return_form"] = form if returning else None
    elif section == "team":
        context["roles"] = exit_manage.team_roles(employee)
        context["reports"] = exit_manage.direct_reports(employee)
        context["handover_form"] = form or HandoverForm(case=case)
    elif section == "survey":
        from offboarding import exit_survey_pages

        context.update(exit_survey_pages.panel_context(request, case))
    elif section == "tasks":
        rows = list(exit_tasks.tasks_of(case))
        for row in rows:
            row.can_do = exit_tasks.can_do(request.user, row)
            row.can_review = exit_tasks.can_review(request.user, row)
            row.can_manage = context["can_change"] and row.status in ("scheduled", "pending", "review")
        context["task_rows"] = sorted(rows, key=lambda r: (r.group_title, r.sequence, r.pk))
        context["task_done"], context["task_total"] = exit_tasks.progress(case)
    elif section == "leave":
        rows, total = exit_manage.leave_in_notice(case)
        context["leave_rows"] = rows
        context["leave_total"] = total
        context["already_extended"] = case.leave_days_extended
        context["to_extend"] = max(total - case.leave_days_extended, 0)

    response = render(request, "offboarding/exit_process/manage.html", context)
    if notice:
        # Tell the page behind the window to refresh its progress icons.
        response["HX-Trigger"] = "exitPanelChanged"
    return response


@exit_access_required
@hx_request_required
@require_POST
def exit_history_summary(request, pk):
    """The AI-written summary of this exit's history, shown above the history list."""
    case = _case_or_404(request, pk)
    if case is None:
        return render(request, "no_perm.html")
    result = error = None
    try:
        result = exit_ai.summarize(case, fresh=request.POST.get("fresh") == "1")
    except exit_ai.SummaryError as problem:
        error = str(problem)
    return render(
        request,
        "offboarding/exit_process/history_summary.html",
        {"case": case, "result": result, "error": error},
    )


@exit_access_required
@hx_request_required
def manage_panel(request, pk, section="summary"):
    case = _case_or_404(request, pk)
    if case is None:
        return render(request, "no_perm.html")
    returning = None
    form = None
    if section == "assets" and request.GET.get("return"):
        returning = exit_manage.open_assets(case.employee_id).filter(
            pk=request.GET["return"]
        ).first()
        if returning is not None and request.user.has_perm("asset.change_assetassignment"):
            form = AssetReturnForm(initial={"return_date": date.today()})
        else:
            returning = None
    return _panel(request, case, section, returning=returning, form=form)


def _changeable_case(request, pk):
    case = _case_or_404(request, pk)
    if case is None or not _can_change(request) or case.status != "in_progress":
        return None
    return case


@exit_access_required
@hx_request_required
@require_POST
def exit_asset_ask(request, pk, assignment_id):
    case = _changeable_case(request, pk)
    if case is None:
        return render(request, "no_perm.html")
    try:
        exit_manage.request_asset_return(case, assignment_id, _me(request))
    except ExitError as problem:
        return _panel(request, case, "assets", error=str(problem))
    return _panel(request, case, "assets", notice=_("The employee has been asked to return it."))


@exit_access_required
@hx_request_required
@require_POST
def exit_asset_return(request, pk, assignment_id):
    case = _changeable_case(request, pk)
    if case is None or not request.user.has_perm("asset.change_assetassignment"):
        return render(request, "no_perm.html")
    form = AssetReturnForm(request.POST)
    returning = exit_manage.open_assets(case.employee_id).filter(pk=assignment_id).first()
    if form.is_valid():
        data = form.cleaned_data
        try:
            exit_manage.recover_asset(
                case,
                assignment_id,
                status=data["return_status"],
                returned_on=data["return_date"],
                condition=data["return_condition"],
                by=_me(request),
            )
        except ExitError as problem:
            form.add_error(None, str(problem))
        else:
            return _panel(request, case, "assets", notice=_("The asset has been marked as returned."))
    return _panel(request, case, "assets", form=form, returning=returning)


@exit_access_required
@hx_request_required
@require_POST
def exit_team_handover(request, pk):
    case = _changeable_case(request, pk)
    if case is None:
        return render(request, "no_perm.html")
    form = HandoverForm(request.POST, case=case)
    if form.is_valid():
        try:
            moved = exit_manage.hand_over_team(case, form.cleaned_data["new_owner"], _me(request))
        except ExitError as problem:
            form.add_error(None, str(problem))
        else:
            return _panel(
                request, case, "team", notice=_("Handed over: %(what)s") % {"what": "; ".join(moved)}
            )
    return _panel(request, case, "team", form=form)


@exit_access_required
@hx_request_required
@require_POST
def exit_leave_extend(request, pk):
    case = _changeable_case(request, pk)
    if case is None:
        return render(request, "no_perm.html")
    try:
        case, extra = exit_manage.extend_notice_for_leave(case, _me(request))
    except ExitError as problem:
        return _panel(request, case, "leave", error=str(problem))
    return _panel(
        request,
        case,
        "leave",
        notice=_("The last working day has moved out by %(days)s day(s).") % {"days": extra},
    )


# --------------------------------------------------------------------------
# exit tasks - doing the work
#
# Open to whoever the task is for (its owner, its approver) as well as to
# HR, so these do not need the Exit Process page access that the rest of the
# panel does.
# --------------------------------------------------------------------------


class CompleteTaskForm(forms.Form):
    note = TextCharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Add a note (optional)")}),
    )
    attachment = forms.FileField(required=False)


class ReviewTaskForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[("approve", _("Approve")), ("send_back", _("Send back"))],
        widget=forms.RadioSelect,
        initial="approve",
    )
    comment = TextCharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Type here")}),
    )


class SkipTaskForm(forms.Form):
    reason = TextCharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Why is this being skipped?")}),
        error_messages={"required": _("A reason is required")},
    )


class OwnerTaskForm(forms.Form):
    new_owner = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        error_messages={"required": _("Please choose who does this task.")},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_owner"].queryset = Employee.objects.filter(is_active=True).order_by(
            "employee_first_name", "employee_last_name"
        )


TASK_ACTIONS = {
    "complete": (_("Complete task"), CompleteTaskForm),
    "review": (_("Approve task"), ReviewTaskForm),
    "skip": (_("Skip task"), SkipTaskForm),
    "owner": (_("Change owner"), OwnerTaskForm),
}


def _task_allowed(user, task, action):
    if action == "complete":
        return exit_tasks.can_do(user, task)
    if action == "review":
        return exit_tasks.can_review(user, task)
    return exit_service.can_manage_exits(user)


def _after_task_action(request, task, notice):
    """Back to wherever the person came from: the panel, or reload the page."""
    from offboarding.exit_pages import _done

    # Only people who may open this exit's panel get the panel back (an
    # owner who just does one task must not be shown the whole list).
    if request.POST.get("next") == "panel" and _case_or_404(request, task.case_id_id):
        return _panel(request, task.case_id, "tasks", notice=notice)
    return _done(request, notice)


def _task_for(request, pk):
    from offboarding.models import ExitTask

    return (
        ExitTask.objects.entire()
        .select_related("case_id__employee_id", "owner_id", "approver_id")
        .filter(pk=pk)
        .first()
    )


@login_required
@hx_request_required
def exit_task_action(request, pk, action):
    task = _task_for(request, pk)
    if task is None or action not in TASK_ACTIONS or not _task_allowed(request.user, task, action):
        return render(request, "no_perm.html")
    title, form_class = TASK_ACTIONS[action]
    me = _me(request)
    next_page = request.POST.get("next") or request.GET.get("next") or "mine"
    if request.method == "POST":
        form = form_class(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            try:
                if action == "complete":
                    exit_tasks.complete_task(
                        task, me, data["note"], data["attachment"] or None, user=request.user
                    )
                    notice = _("The task has been completed.")
                elif action == "review":
                    approve = data["decision"] == "approve"
                    exit_tasks.review_task(task, me, approve, data["comment"], user=request.user)
                    notice = _("The task has been approved.") if approve else _("The task has been sent back.")
                elif action == "skip":
                    exit_tasks.skip_task(task, me, data["reason"])
                    notice = _("The task has been skipped.")
                else:
                    exit_tasks.reassign_owner(task, data["new_owner"], me)
                    notice = _("The task has a new owner.")
            except ExitError as problem:
                form.add_error(None, str(problem))
            else:
                return _after_task_action(request, task, notice)
    else:
        form = form_class()
    # opened from the Manage panel: "Cancel" goes back to its Tasks tab, not out of the window
    back_url = None
    if next_page == "panel" and _case_or_404(request, task.case_id_id):
        back_url = reverse("exit-manage", args=[task.case_id_id, "tasks"])
    return render(
        request,
        "offboarding/exit_process/task_action.html",
        {"form": form, "task": task, "action": action, "title": title, "next": next_page, "back_url": back_url},
    )


@login_required
@hx_request_required
@require_POST
def exit_task_remind(request, pk):
    task = _task_for(request, pk)
    if task is None or not exit_service.can_manage_exits(request.user):
        return render(request, "no_perm.html")
    request.POST = request.POST.copy()
    request.POST["next"] = request.GET.get("next", "panel")
    try:
        exit_tasks.remind_task(task, _me(request))
    except ExitError as problem:
        if request.POST["next"] == "panel" and _case_or_404(request, task.case_id_id):
            return _panel(request, task.case_id, "tasks", error=str(problem))
        from offboarding.exit_pages import _done

        messages.error(request, str(problem))
        return _done(request)
    return _after_task_action(request, task, _("Reminder sent."))


@login_required
def exit_my_tasks(request):
    todo, approve = exit_tasks.my_tasks(_me(request)) if _me(request) else ([], [])
    return render(
        request,
        "offboarding/exit_process/my_tasks.html",
        {"to_do": todo, "to_approve": approve},
    )
