"""
offboarding/exit_pages.py

The Exit Process page (Under Review / Exits in Progress / Exited) and the
small windows opened from it: review a request, initiate an exit, change a
last working day, cancel or rejoin, and the per-employee summary.

Who sees what: people with the offboarding view permission see every exit
(inside the company chosen at the top); anyone else only sees their own
direct reports - the manager's "review my team's resignations" view.
All the actual rules live in exit_service.py; this module only checks who is
asking, validates what they typed, and shows the result.
"""

import contextlib
from datetime import date

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape, linebreaks
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from employee.models import Employee, EmployeeWorkInformation
from horilla.decorators import hx_request_required, login_required
from horilla.models import has_xss
from employee.models import SecondaryManager
from horilla.access import (
    ACCESS_LEVELS,
    APPROVE,
    VIEW,
    manager_q,
    managers_for,
)
from offboarding.form_fields import TextCharField
from offboarding import exit_manage, exit_service, exit_tasks
from offboarding.exit_policy import notice_period_days, notice_period_end
from offboarding.exit_service import ExitError
from offboarding.models import (
    ExitActivity,
    ExitReasonOption,
    ExitTask,
    OffboardingEmployee,
    ResignationLetter,
)

PAGE_SIZE = 10
TABS = ("review", "progress", "exited", "tasks", "survey")


# --------------------------------------------------------------------------
# who is asking
# --------------------------------------------------------------------------


def _me(request):
    return getattr(request.user, "employee_get", None)


def is_exit_admin(user):
    return user.has_perm("offboarding.view_offboardingemployee")


def _has_reports(employee):
    """A reporting manager, or a second manager who may at least see resignations."""
    if not employee:
        return False
    if EmployeeWorkInformation.objects.entire().filter(reporting_manager_id=employee).exists():
        return True
    if SecondaryManager.objects.filter(
        manager_id=employee, access__resignation__in=list(ACCESS_LEVELS)
    ).exists():
        return True
    # (a manager of managers always has a direct report - the manager - so is covered above)
    return False


def has_exit_access(request):
    return is_exit_admin(request.user) or _has_reports(_me(request))


def _scoped(request, qs, employee_path):
    """Admins see everything; a manager only their direct reports."""
    if is_exit_admin(request.user):
        return qs
    return qs.filter(manager_q(_me(request), "resignation", VIEW, employee_path)).distinct()


def exit_access_required(view):
    """Login + (exit admin or someone with direct reports)."""

    @login_required
    def wrapper(request, *args, **kwargs):
        if not has_exit_access(request):
            messages.info(request, _("You don't have permission."))
            return redirect("/")
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


PANEL_SECTIONS = ("summary", "assets", "finances", "survey", "tasks", "team", "leave")


def back_to_panel(request, case, default="summary"):
    """
    Where "Cancel" / "Back" should go when a window was opened from the Manage panel
    (its links carry ?from=panel&section=...): the panel, on the same tab. None when it
    was opened from somewhere else - then the button just closes the window.
    """
    if case is None or request.GET.get("from") != "panel":
        return None
    section = request.GET.get("section")
    return reverse("exit-manage", args=[case.pk, section if section in PANEL_SECTIONS else default])


def _done(request, message=None):
    """Success for a modal form: refresh the page so every tab and count is right."""
    if message:
        messages.success(request, message)
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


# --------------------------------------------------------------------------
# the page and its tabs
# --------------------------------------------------------------------------


def _queryset(request, tab):
    if tab == "review":
        qs = ResignationLetter.objects.filter(status="requested").select_related(
            "employee_id"
        )
        qs = _scoped(request, qs, "employee_id").order_by("-created_at")
    else:
        qs = OffboardingEmployee.objects.filter(
            status="in_progress" if tab == "progress" else "exited"
        ).select_related("employee_id")
        qs = _scoped(request, qs, "employee_id").order_by(
            "last_working_day" if tab == "progress" else "-last_working_day"
        )

    path = "employee_id"
    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(
            Q(**{f"{path}__employee_first_name__icontains": query})
            | Q(**{f"{path}__employee_last_name__icontains": query})
            | Q(**{f"{path}__badge_id__icontains": query})
        )
    exit_type = request.GET.get("type")
    if exit_type in ("resignation", "termination"):
        qs = qs.filter(exit_type=exit_type)
    return qs


def _counts(request):
    counts = {
        tab: _queryset_unfiltered(request, tab).count()
        for tab in TABS
        if tab not in ("tasks", "survey")
    }
    counts["tasks"] = _task_queryset(request, status="open").count()
    if is_exit_admin(request.user):
        from offboarding import exit_survey_pages

        counts["survey"] = exit_survey_pages.count_completed()
    return counts


TASK_STATUS_FILTERS = {
    "open": ["pending", "review"],
    "scheduled": ["scheduled"],
    "pending": ["pending"],
    "review": ["review"],
    "done": ["done"],
    "skipped": ["skipped"],
}


def _task_queryset(request, status=None):
    """Exit tasks of exits in progress that this person may see."""
    qs = ExitTask.objects.entire().filter(case_id__status="in_progress").select_related(
        "case_id__employee_id", "owner_id", "approver_id"
    )
    qs = _scoped(request, qs, "case_id__employee_id")
    status = status or request.GET.get("status") or "open"
    if status in TASK_STATUS_FILTERS:
        qs = qs.filter(status__in=TASK_STATUS_FILTERS[status])
    else:
        qs = qs.exclude(status="cancelled")
    query = (request.GET.get("q") or "").strip()
    if query:
        employee = "case_id__employee_id"
        qs = qs.filter(
            Q(**{f"{employee}__employee_first_name__icontains": query})
            | Q(**{f"{employee}__employee_last_name__icontains": query})
            | Q(title__icontains=query)
        )
    return qs.order_by("due_date", "case_id", "sequence", "pk")


def _queryset_unfiltered(request, tab):
    saved = request.GET
    request.GET = type(saved)()  # no search/type narrowing for the tab badges
    try:
        return _queryset(request, tab)
    finally:
        request.GET = saved


def _sections(case):
    """Progress icons for an exit in progress (grey / yellow / green)."""
    return exit_manage.sections(case)


@exit_access_required
def exit_process_page(request):
    tab = request.GET.get("tab")
    return render(
        request,
        "offboarding/exit_process/page.html",
        {
            "tab": tab if tab in TABS else "review",
            "counts": _counts(request),
            "is_admin": is_exit_admin(request.user),
        },
    )


@exit_access_required
@hx_request_required
def exit_process_tab(request, tab):
    if tab not in TABS:
        return HttpResponse(status=404)
    if tab == "tasks":
        return _tasks_tab(request)
    if tab == "survey":
        from offboarding import exit_survey_pages

        return exit_survey_pages.survey_tab(request)
    page = Paginator(_queryset(request, tab), PAGE_SIZE).get_page(request.GET.get("page"))
    rows = list(page.object_list)
    me = _me(request)
    for row in rows:
        if tab == "review":
            row.can_review = exit_service.can_review(request.user, row)
            row.waiting_days = (date.today() - row.created_at.date()).days if row.created_at else 0
            row.reviewer = row.employee_id.get_reporting_manager()
            row.reviewers = managers_for(row.employee_id, "resignation", APPROVE)
        else:
            if tab == "progress":
                row.sections = _sections(row)
                row.days_left = (
                    (row.last_working_day - date.today()).days if row.last_working_day else None
                )
    return render(
        request,
        "offboarding/exit_process/tab.html",
        {
            "tab": tab,
            "page": page,
            "rows": rows,
            "is_admin": is_exit_admin(request.user),
            "q": request.GET.get("q", ""),
            "type": request.GET.get("type", ""),
            "querystring": _querystring(request),
            "me": me,
        },
    )


def _tasks_tab(request):
    """Task tracking: every exit task by task, or progress by employee."""
    view = request.GET.get("view") if request.GET.get("view") in ("task", "employee") else "task"
    status = request.GET.get("status") or "open"
    context = {
        "tab": "tasks",
        "view": view,
        "status": status,
        "status_choices": [
            ("open", _("Waiting on someone")),
            ("scheduled", _("Not started yet")),
            ("review", _("Waiting for approval")),
            ("done", _("Done")),
            ("skipped", _("Skipped")),
            ("all", _("All")),
        ],
        "is_admin": is_exit_admin(request.user),
        "can_change": exit_service.can_manage_exits(request.user),
        "q": request.GET.get("q", ""),
        "querystring": _querystring(request),
    }
    if view == "task":
        page = Paginator(_task_queryset(request), PAGE_SIZE).get_page(request.GET.get("page"))
        rows = list(page.object_list)
        for row in rows:
            row.can_do = exit_tasks.can_do(request.user, row)
            row.can_review = exit_tasks.can_review(request.user, row)
            row.late = row.status in ("pending", "review") and row.due_date < date.today()
        context.update(page=page, rows=rows)
    else:
        cases = OffboardingEmployee.objects.entire().filter(status="in_progress").select_related(
            "employee_id"
        )
        cases = _scoped(request, cases, "employee_id")
        query = (request.GET.get("q") or "").strip()
        if query:
            cases = cases.filter(
                Q(employee_id__employee_first_name__icontains=query)
                | Q(employee_id__employee_last_name__icontains=query)
            )
        page = Paginator(cases.order_by("last_working_day"), PAGE_SIZE).get_page(
            request.GET.get("page")
        )
        rows = list(page.object_list)
        for row in rows:
            row.done_count, row.total_count = exit_tasks.progress(row)
            row.open_count = exit_tasks.tasks_of(row).filter(status__in=["pending", "review"]).count()
        context.update(page=page, rows=rows)
    return render(request, "offboarding/exit_process/tasks_tab.html", context)


def _querystring(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------

YES_NO = [("yes", _("Yes")), ("no", _("No"))]


def _tri(value):
    return {"yes": True, "no": False}.get(value)


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            ("accept", _("Accept the resignation")),
            ("reject", _("Reject the resignation")),
            ("retain", _("Retain the employee")),
        ],
        widget=forms.RadioSelect,
        initial="accept",
    )
    last_working_day = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    rehire_eligible = forms.ChoiceField(
        required=False, choices=YES_NO, widget=forms.RadioSelect, initial="yes"
    )
    rehire_comment = TextCharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
    comment = TextCharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Type here")}),
        error_messages={"required": _("A comment is required")},
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "accept" and not cleaned.get("last_working_day"):
            self.add_error("last_working_day", _("Please choose the last working day"))
        return cleaned


@exit_access_required
@hx_request_required
def exit_review(request, pk):
    letter = get_object_or_404(ResignationLetter.objects.entire(), pk=pk)
    if not exit_service.can_review(request.user, letter):
        return render(request, "no_perm.html")
    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                exit_service.review_request(
                    letter,
                    reviewer=_me(request),
                    decision=data["decision"],
                    comment=data["comment"],
                    last_working_day=data["last_working_day"],
                    rehire_eligible=_tri(data["rehire_eligible"]),
                    rehire_comment=data["rehire_comment"],
                )
            except ExitError as error:
                form.add_error(None, str(error))
            else:
                return _done(request, _("The review has been saved."))
    else:
        form = ReviewForm(initial={"last_working_day": exit_service.suggested_last_working_day(letter)})
    return render(
        request,
        "offboarding/exit_process/review_form.html",
        {"form": form, "letter": letter, "employee": letter.employee_id},
    )


# --------------------------------------------------------------------------
# initiate
# --------------------------------------------------------------------------


class InitiateForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.none())
    exit_type = forms.ChoiceField(
        choices=OffboardingEmployee.EXIT_TYPES,
        initial="resignation",
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(max_length=100, error_messages={"required": _("Please select a reason")})
    discussed = forms.ChoiceField(choices=YES_NO, widget=forms.RadioSelect, initial="yes")
    discussion_summary = TextCharField(
        required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3})
    )
    notice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    last_working_day = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    rehire_eligible = forms.ChoiceField(required=False, choices=YES_NO, widget=forms.RadioSelect)
    rehire_comment = TextCharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
    attachment = forms.FileField(required=False)

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        employees = Employee.objects.filter(is_active=True)
        if not is_exit_admin(request.user):
            # a manager starts an exit for the people they may decide on
            employees = employees.filter(manager_q(_me(request), "resignation", APPROVE)).distinct()
        busy = list(
            OffboardingEmployee.objects.entire()
            .filter(status__in=["in_progress", "exited"])
            .values_list("employee_id", flat=True)
        ) + list(
            ResignationLetter.objects.entire()
            .filter(status="requested")
            .values_list("employee_id", flat=True)
        )
        self.fields["employee"].queryset = employees.exclude(pk__in=busy).order_by(
            "employee_first_name"
        )
        self.reasons = list(
            ExitReasonOption.objects.entire()
            .filter(Q(company_id__isnull=True) | Q(company_id=_company_id(request)))
            .order_by("exit_type", "title")
        )

    def clean_discussion_summary(self):
        text = (self.cleaned_data.get("discussion_summary") or "").strip()
        if text and has_xss(text):
            raise forms.ValidationError(_("This text contains content that is not allowed."))
        return text

    def clean_rehire_comment(self):
        text = (self.cleaned_data.get("rehire_comment") or "").strip()
        if text and has_xss(text):
            raise forms.ValidationError(_("This text contains content that is not allowed."))
        return text

    def clean(self):
        cleaned = super().clean()
        exit_type = cleaned.get("exit_type")
        reason = cleaned.get("reason")
        if exit_type and reason and not any(
            r.title == reason and r.exit_type == exit_type for r in self.reasons
        ):
            self.add_error("reason", _("Please select a reason from the list"))
        if cleaned.get("discussed") == "yes" and not cleaned.get("discussion_summary"):
            self.add_error("discussion_summary", _("Summary is required"))
        employee = cleaned.get("employee")
        notice_date = cleaned.get("notice_date")
        if employee and notice_date and not cleaned.get("last_working_day"):
            days = notice_period_days(employee)
            end = notice_period_end(days, notice_date)
            if end is None:
                self.add_error(
                    "last_working_day",
                    _("This employee has no notice period - please choose the last working day"),
                )
            else:
                cleaned["last_working_day"] = end
        lwd = cleaned.get("last_working_day")
        if lwd and notice_date and lwd < notice_date:
            self.add_error("last_working_day", _("The last working day cannot be before the notice date"))
        return cleaned


def _company_id(request):
    selected = request.session.get("selected_company")
    return int(selected) if selected and str(selected).isdigit() else None


def _mail_exit_initiated(letter, initiator):
    """Tell the employee and their managers; never lets a mail problem break the exit."""
    from offboarding.mailers import notify_exit_initiated

    with contextlib.suppress(Exception):
        letter.refresh_from_db()  # reflects the final last working day and status
        notify_exit_initiated(letter, initiator)


@exit_access_required
@hx_request_required
def exit_initiate(request):
    if request.method == "POST":
        form = InitiateForm(request.POST, request.FILES, request=request)
        if form.is_valid():
            data = form.cleaned_data
            me = _me(request)
            employee = data["employee"]
            option = (
                ExitReasonOption.objects.entire()
                .filter(title=data["reason"], exit_type=data["exit_type"])
                .first()
            )
            summary = data["discussion_summary"] if data["discussed"] == "yes" else ""
            letter = ResignationLetter(
                employee_id=employee,
                exit_type=data["exit_type"],
                initiated_by=me,
                title=f"{dict(OffboardingEmployee.EXIT_TYPES)[data['exit_type']]} - {employee.get_full_name()}"[:100],
                description=linebreaks(escape(summary or data["reason"])),
                planned_to_leave_on=data["last_working_day"],
                notice_date=data["notice_date"],
                reason=data["reason"],
                reason_option=option,
                discussed_with_manager=data["discussed"] == "yes",
                discussion_summary=summary,
                status="requested",
            )
            if data["attachment"]:
                letter.attachment = data["attachment"]
            try:
                letter.save()
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
                return render(
                    request, "offboarding/exit_process/initiate_form.html", {"form": form}
                )
            exit_service.log_activity(
                employee, "initiated", me, letter, to_value=data["reason"]
            )
            try:
                if is_exit_admin(request.user):
                    # HR initiating has no one above them to approve it.
                    exit_service.review_request(
                        letter,
                        reviewer=me,
                        decision="accept",
                        comment=_("Initiated by HR."),
                        last_working_day=data["last_working_day"],
                        rehire_eligible=_tri(data["rehire_eligible"]),
                        rehire_comment=data["rehire_comment"],
                    )
                    _mail_exit_initiated(letter, me)
                    return _done(request, _("The exit has been started."))
                exit_service.settle_new_request(letter)
            except ExitError as error:
                form.add_error(None, str(error))
            else:
                _mail_exit_initiated(letter, me)
                return _done(request, _("The exit request has been submitted for review."))
    else:
        form = InitiateForm(
            initial={
                "notice_date": date.today(),
                "employee": request.GET.get("employee"),
            },
            request=request,
        )
    return render(request, "offboarding/exit_process/initiate_form.html", {"form": form})


# --------------------------------------------------------------------------
# a running exit: summary, last working day, cancel / rejoin, deactivate
# --------------------------------------------------------------------------


def _case_or_404(request, pk):
    case = get_object_or_404(OffboardingEmployee.objects.entire(), pk=pk)
    if not is_exit_admin(request.user) and not exit_service.can_see_exit_of(
        request.user, case.employee_id
    ):
        return None
    return case


class ReasonedDateForm(forms.Form):
    last_working_day = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    reason = TextCharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Type here")}),
        error_messages={"required": _("A reason is required")},
    )


class ReasonForm(forms.Form):
    reason = TextCharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Type here")}),
        error_messages={"required": _("A reason is required")},
    )


@exit_access_required
@hx_request_required
def exit_change_date(request, pk):
    case = _case_or_404(request, pk)
    if case is None or not exit_service.can_manage_exits(request.user):
        return render(request, "no_perm.html")
    if request.method == "POST":
        form = ReasonedDateForm(request.POST)
        if form.is_valid():
            try:
                exit_service.change_last_working_day(
                    case, form.cleaned_data["last_working_day"], form.cleaned_data["reason"], _me(request)
                )
            except ExitError as error:
                form.add_error(None, str(error))
            else:
                return _done(request, _("The last working day has been updated."))
    else:
        form = ReasonedDateForm(initial={"last_working_day": case.last_working_day})
    return render(
        request,
        "offboarding/exit_process/date_form.html",
        {"form": form, "case": case, "employee": case.employee_id, "back_url": back_to_panel(request, case)},
    )


@exit_access_required
@hx_request_required
def exit_cancel(request, pk):
    case = _case_or_404(request, pk)
    if case is None or not exit_service.can_manage_exits(request.user):
        return render(request, "no_perm.html")
    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                exit_service.cancel_exit(case, _me(request), form.cleaned_data["reason"])
            except ExitError as error:
                form.add_error(None, str(error))
            else:
                return _done(
                    request,
                    _("The employee has rejoined.") if case.status == "exited" else _("The exit has been cancelled."),
                )
    else:
        form = ReasonForm()
    return render(
        request,
        "offboarding/exit_process/cancel_form.html",
        {"form": form, "case": case, "employee": case.employee_id, "back_url": back_to_panel(request, case)},
    )


@exit_access_required
@require_POST
def exit_remind(request, pk):
    letter = get_object_or_404(ResignationLetter.objects.entire(), pk=pk)
    if not is_exit_admin(request.user):
        return render(request, "no_perm.html")
    try:
        mailed = exit_service.send_reminder(letter, _me(request))
    except ExitError as error:
        messages.error(request, str(error))
    else:
        if mailed:
            messages.success(request, _("Reminder sent by notification and email."))
        else:
            messages.warning(
                request,
                _("The reminder was sent as a notification, but the email could not be sent."),
            )
    return _back_to_page()


@login_required
def exit_withdraw(request, pk):
    """Confirm (GET) and perform (POST) taking back a resignation under review."""
    letter = get_object_or_404(ResignationLetter.objects.entire(), pk=pk)
    me = _me(request)
    if me is None or (letter.employee_id_id != me.pk and not is_exit_admin(request.user)):
        return render(request, "no_perm.html")
    if request.method == "POST":
        try:
            exit_service.withdraw_request(letter, me)
        except ExitError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("The resignation has been withdrawn."))
        if request.headers.get("HX-Request"):
            return _done(request)
        return redirect(request.META.get("HTTP_REFERER") or "/")
    return render(
        request, "offboarding/exit_process/withdraw_form.html", {"letter": letter}
    )


@exit_access_required
@require_POST
def exit_deactivate(request, pk):
    """Switch an exited employee's account off - a deliberate HR action, never automatic."""
    case = get_object_or_404(OffboardingEmployee.objects.entire(), pk=pk)
    if not exit_service.can_manage_exits(request.user) or case.status != "exited":
        return render(request, "no_perm.html")
    employee = case.employee_id
    if employee.is_active:
        employee.is_active = False
        employee.save(update_fields=["is_active"])
    user = employee.employee_user_id
    if user is not None and user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    messages.success(
        request, _("%(name)s's account has been deactivated.") % {"name": employee.get_full_name()}
    )
    return _back_to_page("exited")


def _back_to_page(tab=None):
    return redirect(f"/offboarding/exit-process/?tab={tab}" if tab else "/offboarding/exit-process/")
