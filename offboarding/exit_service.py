"""
offboarding/exit_service.py

Every change to an exit goes through here - the Exit Process screens, the
old Resignations list, the employee's own page and the daily job all call
these functions, so the rules (who may do what, mandatory comments, what is
recorded) live in exactly one place.

The shape, as in Keka: a *request* (ResignationLetter) sits under review; once
accepted it becomes an exit *case* (OffboardingEmployee, status in_progress);
after the last working day it is Exited. Nothing here ever silently deletes
history - cancellations and date changes are logged in ExitActivity.
"""

import contextlib
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from horilla.models import has_xss
from notifications.signals import notify
from offboarding.exit_policy import adjust_last_working_day
from offboarding.models import (
    ExitActivity,
    OffboardingEmployee,
    OffboardingGeneralSetting,
    ResignationLetter,
)


class ExitError(Exception):
    """A rule was broken; the message is written for the person who did it."""


# --------------------------------------------------------------------------
# who may do what
# --------------------------------------------------------------------------


def is_reporting_manager_of(user, employee):
    manager = employee.get_reporting_manager()
    return bool(manager and manager.employee_user_id_id == getattr(user, "pk", None))


def can_see_exit_of(user, employee):
    """The reporting manager, or a second manager who was given at least view access."""
    from horilla.access import can_manager_view

    return can_manager_view(user, employee, "resignation")


def can_review(user, letter):
    """
    HR (change_resignationletter) can review anyone. A manager can review their own
    reports: the reporting manager always, a second manager only with approve access.
    Whoever decides first decides - a request that is no longer under review is closed.
    """
    from horilla.access import can_manager_approve

    return user.has_perm("offboarding.change_resignationletter") or can_manager_approve(
        user, letter.employee_id, "resignation"
    )


def can_manage_exits(user):
    return user.has_perm("offboarding.change_offboardingemployee")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _clean_comment(text, *, required, label=None):
    text = (text or "").strip()
    if required and not text:
        raise ExitError(_("%(label)s is required.") % {"label": label or _("A comment")})
    if text and has_xss(text):
        raise ExitError(
            _(
                "The text contains content that is not allowed (such as script "
                "tags or event handlers). Please remove it."
            )
        )
    return text


def log_activity(
    employee, action, by=None, letter=None, comment="", from_value="", to_value=""
):
    return ExitActivity.objects.create(
        employee_id=employee,
        letter_id=letter,
        action=action,
        comment=comment,
        from_value=str(from_value or ""),
        to_value=str(to_value or ""),
        performed_by=by,
    )


def _tell(recipient_employee, actor, verb, redirect="#"):
    """In-app notification (the bell, no email); a notification problem must never undo the action."""
    user = getattr(recipient_employee, "employee_user_id", None)
    if user is None:
        return
    with contextlib.suppress(Exception):
        notify.send(
            actor or recipient_employee,
            recipient=user,
            verb=verb,
            verb_ar="",
            verb_de="",
            verb_es="",
            verb_fr="",
            redirect=redirect,
            icon="information",
        )


def _settings_for(employee):
    """Exit settings that apply to this employee (their company's row, else global)."""
    company_id = getattr(
        getattr(employee, "employee_work_info", None), "company_id_id", None
    )
    rows = OffboardingGeneralSetting.objects.entire().order_by("pk")
    row = rows.filter(company_id=company_id).first() if company_id else None
    return row or rows.filter(company_id__isnull=True).first() or OffboardingGeneralSetting()


# --------------------------------------------------------------------------
# the exit case
# --------------------------------------------------------------------------


def start_exit_case(
    employee,
    *,
    exit_type,
    last_working_day,
    notice_date=None,
    rehire_eligible=None,
    rehire_comment="",
):
    """
    Create the in-progress exit case for `employee`, or reopen their earlier
    one (an employee has at most one case row; an earlier cancelled or
    finished case is reused, its history stays in ExitActivity).
    """
    case = OffboardingEmployee.objects.entire().filter(employee_id=employee).first()
    if case is None:
        case = OffboardingEmployee(employee_id=employee)
    case.status = "in_progress"
    case.exit_type = exit_type
    case.notice_date = notice_date or date.today()
    case.last_working_day = last_working_day
    case.rehire_eligible = rehire_eligible
    case.rehire_comment = rehire_comment or ""
    case.exited_on = None
    # Old-pipeline fields kept in step for everything that still reads them.
    case.notice_period_starts = case.notice_date
    case.notice_period_ends = last_working_day
    if last_working_day and case.notice_date:
        days = (last_working_day - case.notice_date).days
        case.notice_period = days if days > 0 else None
        case.unit = "day" if days > 0 else None
    case.save()
    from offboarding import exit_tasks

    exit_tasks.create_tasks(case)
    from offboarding import exit_survey

    exit_survey.auto_send(case)
    return case


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------


def settle_new_request(letter):
    """
    Called right after a request is saved. Under "Acknowledgement" review
    mode there is nothing to wait for - the exit starts straight away.
    Returns the case if it did, else None (the request stays under review).
    """
    if letter.status != "requested":
        return None
    if _settings_for(letter.employee_id).review_mode != "acknowledgement":
        return None
    return review_request(
        letter,
        reviewer=letter.initiated_by or letter.employee_id,
        decision="accept",
        comment=_("Accepted automatically (review mode: acknowledgement)."),
        last_working_day=letter.planned_to_leave_on,
        _system=True,
    )


def _tell_other_managers(letter, reviewer, outcome):
    """
    Either manager may decide and the first decision stands - so tell the other manager
    (reporting manager or second manager) what was decided and by whom.
    """
    from horilla.access import VIEW, managers_for

    employee = letter.employee_id
    for manager in managers_for(employee, "resignation", VIEW):
        if reviewer is not None and manager.pk == reviewer.pk:
            continue
        _tell(
            manager,
            reviewer,
            _("%(reviewer)s %(outcome)s %(name)s's resignation request.")
            % {
                "reviewer": reviewer.get_full_name() if reviewer else _("HR"),
                "outcome": outcome,
                "name": employee.get_full_name(),
            },
        )


@transaction.atomic
def review_request(
    letter,
    reviewer,
    decision,
    comment,
    last_working_day=None,
    rehire_eligible=None,
    rehire_comment="",
    _system=False,
):
    """
    Accept, reject or retain a request that is under review. A comment is
    mandatory (Keka asks for one with every decision).
    """
    letter = ResignationLetter.objects.entire().select_for_update().get(pk=letter.pk)
    if letter.status != "requested":
        raise ExitError(_("This request has already been reviewed."))
    if decision not in ("accept", "reject", "retain"):
        raise ExitError(_("Choose to accept, reject or retain."))
    comment = _clean_comment(comment, required=True, label=_("A comment"))
    rehire_comment = _clean_comment(rehire_comment, required=False)

    employee = letter.employee_id
    now = timezone.now()
    letter.review_comment = comment
    letter.reviewed_by = reviewer
    letter.reviewed_at = now

    if decision == "reject":
        letter.status = "rejected"
        letter.save()
        log_activity(employee, "rejected", reviewer, letter, comment)
        _tell(employee, reviewer, _("Your resignation request has been rejected."))
        _tell_other_managers(letter, reviewer, _("rejected"))
        return None
    if decision == "retain":
        letter.status = "retained"
        letter.save()
        log_activity(employee, "retained", reviewer, letter, comment)
        _tell(
            employee,
            reviewer,
            _("Your resignation request was reviewed - you have been retained."),
        )
        _tell_other_managers(letter, reviewer, _("retained the employee on"))
        return None

    day = last_working_day or letter.planned_to_leave_on
    if not day:
        raise ExitError(_("Please choose the last working day."))
    day = adjust_last_working_day(
        day, _settings_for(employee).last_working_day_holiday_rule, employee
    )
    letter.status = "approved"
    letter.planned_to_leave_on = day
    notice_date = letter.notice_date or notice_start_for(letter)
    case = start_exit_case(
        employee,
        exit_type=letter.exit_type,
        last_working_day=day,
        notice_date=notice_date,
        rehire_eligible=rehire_eligible,
        rehire_comment=rehire_comment,
    )
    letter.offboarding_employee_id = case
    letter.save()
    log_activity(
        employee,
        "accepted",
        reviewer,
        letter,
        comment,
        to_value=day.strftime("%d %b %Y"),
    )
    if not _system:
        _tell(
            employee,
            reviewer,
            _("Your resignation has been accepted. Your last working day is %(day)s.")
            % {"day": day.strftime("%d %b %Y")},
        )
        _tell_other_managers(letter, reviewer, _("accepted"))
    return case


def notice_start_for(letter):
    """
    When this request's notice period starts: the day it was submitted, or -
    when Exit settings say the notice only starts once accepted - today.
    """
    if _settings_for(letter.employee_id).notice_starts_on_resignation_date:
        return letter.created_at.date() if letter.created_at else date.today()
    return date.today()


def suggested_last_working_day(letter):
    """The last working day to offer the reviewer: the employee's own preference,
    else the end of the notice period counted the way Exit settings ask."""
    if letter.preferred_last_working_day or not letter.notice_period_days:
        return letter.planned_to_leave_on
    if _settings_for(letter.employee_id).notice_starts_on_resignation_date:
        return letter.planned_to_leave_on
    return date.today() + timedelta(days=letter.notice_period_days)


def withdraw_request(letter, by):
    """The employee takes their own request back, while it is still under review."""
    letter = ResignationLetter.objects.entire().get(pk=letter.pk)
    if letter.status != "requested":
        raise ExitError(_("Only a request that is still under review can be withdrawn."))
    employee = letter.employee_id
    is_self = by is not None and by.pk == employee.pk
    if is_self and not _settings_for(employee).allow_withdrawal:
        raise ExitError(_("Withdrawing a resignation is not allowed."))
    if not is_self and not (by and by.employee_user_id.has_perm("offboarding.change_resignationletter")):
        raise ExitError(_("You cannot withdraw this request."))
    letter.status = "withdrawn"
    letter.save()
    log_activity(employee, "withdrawn", by, letter)
    return letter


def send_reminder(letter, by):
    """Nudge whoever has to review this request (bell and email). True if the email went out."""
    if letter.status != "requested":
        raise ExitError(_("Only a request under review can be reminded."))
    from horilla.access import APPROVE, managers_for
    from offboarding.mailers import remind_reviewer

    # everyone who may decide on it: the reporting manager and a second manager with approve access
    managers = managers_for(letter.employee_id, "resignation", APPROVE)
    if not managers:
        raise ExitError(_("This employee has no reporting manager to remind."))
    mailed = False
    for manager in managers:
        _tell(
            manager,
            by,
            _("Reminder: %(name)s's %(kind)s is waiting for your review.")
            % {"name": letter.employee_id.get_full_name(), "kind": letter.get_exit_type_display()},
        )
        mailed = remind_reviewer(letter, manager, by) or mailed
    log_activity(
        letter.employee_id, "reminder", by, letter,
        to_value=", ".join(m.get_full_name() for m in managers)[:100],
    )
    return mailed


# --------------------------------------------------------------------------
# changing a running exit
# --------------------------------------------------------------------------


def _letter_of(case):
    return (
        ResignationLetter.objects.entire()
        .filter(offboarding_employee_id=case)
        .order_by("-pk")
        .first()
    )


@transaction.atomic
def change_last_working_day(case, new_day, reason, by):
    reason = _clean_comment(reason, required=True, label=_("A reason"))
    if not new_day:
        raise ExitError(_("Please choose the new last working day."))
    case = OffboardingEmployee.objects.entire().select_for_update().get(pk=case.pk)
    if case.status == "cancelled":
        raise ExitError(_("This exit has been cancelled."))
    old = case.last_working_day
    if old == new_day:
        raise ExitError(_("That is already the last working day."))
    case.last_working_day = new_day
    case.notice_period_ends = new_day
    # Moving the date forward on someone already exited reopens nothing -
    # but a future date means they are not out yet, so bring them back.
    if case.status == "exited" and new_day >= date.today():
        case.status = "in_progress"
        case.exited_on = None
    case.save()
    letter = _letter_of(case)
    if letter:
        letter.planned_to_leave_on = new_day
        letter.save()
    if case.status == "in_progress":
        from offboarding import exit_tasks

        exit_tasks.reschedule(case)
    log_activity(
        case.employee_id,
        "lwd_changed",
        by,
        letter,
        reason,
        from_value=old.strftime("%d %b %Y") if old else "",
        to_value=new_day.strftime("%d %b %Y"),
    )
    return case


@transaction.atomic
def cancel_exit(case, by, reason):
    """
    Call the exit off. The employee stays (or, if they had already exited,
    comes back - see rejoin). Their resignation request is marked cancelled
    so they can raise a fresh one later.
    """
    reason = _clean_comment(reason, required=True, label=_("A reason"))
    case = OffboardingEmployee.objects.entire().select_for_update().get(pk=case.pk)
    if case.status == "cancelled":
        raise ExitError(_("This exit is already cancelled."))
    was_exited = case.status == "exited"
    case.status = "cancelled"
    case.exited_on = None
    case.save()
    from offboarding import exit_tasks

    exit_tasks.cancel_open_tasks(case)
    from offboarding import exit_survey

    exit_survey.retire_surveys(case)
    employee = case.employee_id
    if was_exited:
        _reactivate(employee)
    letter = _letter_of(case)
    if letter and letter.status == "approved":
        letter.status = "cancelled"
        letter.save()
    log_activity(employee, "rejoined" if was_exited else "cancelled", by, letter, reason)
    _tell(
        employee,
        by,
        _("Your exit has been cancelled.") if not was_exited else _("Welcome back - your exit has been cancelled."),
    )
    return case


def _reactivate(employee):
    """Make sure an employee who had been deactivated can log in again."""
    changed = []
    if not employee.is_active:
        employee.is_active = True
        employee.save(update_fields=["is_active"])
        changed.append("employee")
    user = employee.employee_user_id
    if user is not None and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
        changed.append("login")
    return changed


# --------------------------------------------------------------------------
# finishing
# --------------------------------------------------------------------------


@transaction.atomic
def mark_exited(case, by=None):
    case = OffboardingEmployee.objects.entire().select_for_update().get(pk=case.pk)
    if case.status != "in_progress":
        return case
    case.status = "exited"
    case.exited_on = case.last_working_day or date.today()
    case.save()
    log_activity(case.employee_id, "exited", by, _letter_of(case))
    return case


def process_due_exits(today=None):
    """
    Daily job: everyone whose last working day has passed moves to Exited.
    This changes their exit *status* only. It does not switch off their
    login - until final settlement exists, a leaver may still need payroll
    for their last month - so deactivating the account stays a deliberate
    HR action.
    """
    today = today or date.today()
    due = OffboardingEmployee.objects.entire().filter(
        status="in_progress", last_working_day__lt=today
    )
    done = 0
    for case in due:
        mark_exited(case)
        done += 1
    return done
