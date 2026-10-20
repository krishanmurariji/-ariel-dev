"""
offboarding/exit_manage.py

The rules behind the Manage panel of an exit in progress: getting company
assets back, handing the leaver's team and approval duties to someone else,
and extending the notice period for leave taken during it.

Like exit_service.py this module knows nothing about screens - the pages call
these functions, which check the rules, change the data and write the history.
"""

import math
from datetime import timedelta

from django.apps import apps
from django.db import transaction
from django.utils.translation import gettext as _

from horilla.methods import get_horilla_model_class
from employee.models import Employee, EmployeeWorkInformation
from offboarding import exit_service, exit_tasks
from offboarding.exit_policy import adjust_last_working_day
from offboarding.exit_service import ExitError, _clean_comment
from offboarding.models import ExitActivity, OffboardingEmployee

RETURN_STATUSES = ("Healthy", "Minor damage", "Major damage")


def _all(model):
    """Every row of a company-scoped model, whichever company is selected."""
    manager = model.objects
    return manager.entire() if hasattr(manager, "entire") else manager.all()


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------


def open_assets(employee):
    """Assets the employee still holds (nothing recorded as returned yet)."""
    from asset.models import AssetAssignment

    return (
        _all(AssetAssignment)
        .filter(assigned_to_employee_id=employee, return_status__isnull=True)
        .select_related("asset_id")
        .order_by("assigned_date", "pk")
    )


def returned_assets(employee):
    from asset.models import AssetAssignment

    return (
        _all(AssetAssignment)
        .filter(assigned_to_employee_id=employee, return_status__isnull=False)
        .select_related("asset_id")
        .order_by("-return_date", "-pk")
    )


def assets_state(case):
    """grey = nothing recovered yet, yellow = in progress, green = all back."""
    holding = open_assets(case.employee_id)
    if not holding.exists():
        return "done"
    if holding.filter(return_request=True).exists():
        return "doing"
    if case.notice_date and returned_assets(case.employee_id).filter(
        return_date__gte=case.notice_date
    ).exists():
        return "doing"
    return "todo"


def _holding_or_error(case, assignment_id):
    assignment = open_assets(case.employee_id).filter(pk=assignment_id).first()
    if assignment is None:
        raise ExitError(_("That asset is not with this employee any more."))
    return assignment


@transaction.atomic
def request_asset_return(case, assignment_id, by):
    """Ask the leaver to hand an asset back (also tells them)."""
    assignment = _holding_or_error(case, assignment_id)
    if assignment.return_request:
        raise ExitError(_("A return has already been requested for this asset."))
    assignment.return_request = True
    assignment.save()
    exit_service.log_activity(
        case.employee_id,
        "asset_ask",
        by,
        exit_service._letter_of(case),
        to_value=str(assignment.asset_id),
    )
    exit_service._tell(
        case.employee_id,
        by,
        _("Please return %(asset)s before your last working day.")
        % {"asset": assignment.asset_id},
    )
    return assignment


@transaction.atomic
def recover_asset(case, assignment_id, *, status, returned_on, condition, by):
    """
    Record an asset as handed back. Keeps the asset's stock and availability
    in step the same way the Assets screen does: a healthy unit goes back into
    the pool, a damaged one is taken out of it.
    """
    if status not in RETURN_STATUSES:
        raise ExitError(_("Please choose the condition of the asset."))
    if not returned_on:
        raise ExitError(_("Please choose the return date."))
    condition = _clean_comment(condition, required=False)
    assignment = _holding_or_error(case, assignment_id)
    if returned_on < assignment.assigned_date:
        raise ExitError(_("The return date cannot be before the asset was given."))
    if status != "Healthy" and not condition:
        raise ExitError(_("Please describe the damage."))

    assignment.return_date = returned_on
    assignment.return_status = status
    assignment.return_condition = condition or None
    assignment.return_request = False
    assignment.save()

    from asset.models import AssetAssignment

    asset = assignment.asset_id
    still_out = (
        _all(AssetAssignment)
        .filter(asset_id=asset, return_date__isnull=True)
        .count()
    )
    if status != "Healthy":
        # A damaged unit leaves the serviceable pool (the Asset model itself
        # marks it Not-Available once nothing is left).
        asset.quantity = max(asset.quantity - 1, 0)
        if asset.quantity == 0:
            asset.asset_status = "Not-Available"
        else:
            asset.asset_status = "Available" if still_out < asset.quantity else "In use"
    else:
        asset.asset_status = "Available" if still_out < asset.quantity else "In use"
    asset.save()

    exit_service.log_activity(
        case.employee_id,
        "asset_back",
        by,
        exit_service._letter_of(case),
        comment=condition or "",
        to_value=f"{asset} ({status})"[:100],
    )
    return assignment


# --------------------------------------------------------------------------
# team and approval duties
# --------------------------------------------------------------------------


def _reports_of(employee):
    return _all(EmployeeWorkInformation).filter(
        reporting_manager_id=employee, employee_id__is_active=True
    )


def _role_sources(employee):
    """
    (key, label, queryset-or-None) for everything the leaver looks after.
    The M2M ones are querysets of the objects they manage.
    """
    roles = [("reports", _("Direct reports"), _reports_of(employee), "work")]
    if apps.is_installed("recruitment"):
        Recruitment = get_horilla_model_class("recruitment", "recruitment")
        Stage = get_horilla_model_class("recruitment", "stage")
        roles.append(
            (
                "recruitments",
                _("Recruitments they manage"),
                _all(Recruitment).filter(recruitment_managers=employee),
                "recruitment_managers",
            )
        )
        roles.append(
            (
                "recruitment_stages",
                _("Recruitment stages they manage"),
                _all(Stage).filter(stage_managers=employee),
                "stage_managers",
            )
        )
    if apps.is_installed("onboarding"):
        OnboardingStage = get_horilla_model_class("onboarding", "onboardingstage")
        OnboardingTask = get_horilla_model_class("onboarding", "onboardingtask")
        roles.append(
            (
                "onboarding_stages",
                _("Onboarding stages they manage"),
                _all(OnboardingStage).filter(employee_id=employee),
                "employee_id",
            )
        )
        roles.append(
            (
                "onboarding_tasks",
                _("Onboarding tasks they manage"),
                _all(OnboardingTask).filter(employee_id=employee),
                "employee_id",
            )
        )
    return roles


def team_roles(employee):
    """What still needs a new owner: [{key, label, count}] (only non-empty)."""
    found = []
    for key, label, queryset, _field in _role_sources(employee):
        count = queryset.count()
        if count:
            found.append({"key": key, "label": label, "count": count})
    return found


def team_state(case):
    if not team_roles(case.employee_id):
        return "done"
    if ExitActivity.objects.entire().filter(
        employee_id=case.employee_id, action="team_moved"
    ).exists():
        return "doing"
    return "todo"


def direct_reports(employee):
    return [
        row.employee_id
        for row in _reports_of(employee).select_related("employee_id").order_by(
            "employee_id__employee_first_name"
        )
    ]


@transaction.atomic
def hand_over_team(case, new_owner, by, keys=None):
    """
    Give the leaver's team and approval duties to `new_owner`. `keys` limits
    which kinds of duty move (default: all of them).
    """
    if new_owner is None:
        raise ExitError(_("Please choose who takes over."))
    leaver = case.employee_id
    if new_owner.pk == leaver.pk:
        raise ExitError(_("The leaver cannot take over from themselves."))
    if not new_owner.is_active:
        raise ExitError(_("The new owner must be an active employee."))
    leaving_too = (
        OffboardingEmployee.objects.entire()
        .filter(employee_id=new_owner, status="in_progress")
        .exists()
    )
    if leaving_too:
        raise ExitError(_("The new owner is also leaving - please choose someone else."))

    moved = []
    for key, label, queryset, field in _role_sources(leaver):
        if keys is not None and key not in keys:
            continue
        rows = list(queryset)
        if not rows:
            continue
        if field == "work":
            _all(EmployeeWorkInformation).filter(
                pk__in=[row.pk for row in rows]
            ).update(reporting_manager_id=new_owner)
        else:
            for row in rows:
                relation = getattr(row, field)
                relation.remove(leaver)
                relation.add(new_owner)
        moved.append(f"{label}: {len(rows)}")
    if not moved:
        raise ExitError(_("There is nothing left to hand over."))

    exit_service.log_activity(
        leaver,
        "team_moved",
        by,
        exit_service._letter_of(case),
        comment="; ".join(moved),
        to_value=new_owner.get_full_name(),
    )
    exit_service._tell(
        new_owner,
        by,
        _("%(name)s's team and approvals have been handed over to you.")
        % {"name": leaver.get_full_name()},
    )
    return moved


def possible_new_owners(case):
    """Active employees who can take over: not the leaver, not also leaving."""
    leaving = (
        OffboardingEmployee.objects.entire()
        .filter(status="in_progress")
        .values_list("employee_id", flat=True)
    )
    return (
        Employee.objects.filter(is_active=True)
        .exclude(pk=case.employee_id_id)
        .exclude(pk__in=list(leaving))
        .order_by("employee_first_name", "employee_last_name")
    )


# --------------------------------------------------------------------------
# leave during notice
# --------------------------------------------------------------------------


def leave_in_notice(case):
    """
    Approved leave inside the notice window.
    Returns (rows, total_days) - each row is (leave_request, days_in_window).
    Leave that sticks out of the window counts only for the part inside it.
    """
    if not case.notice_date or not case.last_working_day:
        return [], 0
    LeaveRequest = get_horilla_model_class("leave", "leaverequest")
    start, end = case.notice_date, case.last_working_day
    leaves = (
        _all(LeaveRequest)
        .filter(
            employee_id=case.employee_id,
            status="approved",
            start_date__lte=end,
            end_date__gte=start,
        )
        .order_by("start_date")
    )
    rows, total = [], 0.0
    for leave in leaves:
        whole = (leave.end_date - leave.start_date).days + 1
        inside = (min(leave.end_date, end) - max(leave.start_date, start)).days + 1
        days = leave.requested_days or whole
        days = days if inside >= whole else days * inside / whole
        rows.append((leave, round(days, 1)))
        total += days
    return rows, math.ceil(total - 1e-9) if total else 0


def pending_extension(case):
    """Days the last working day still has to move for leave already taken."""
    _rows, total = leave_in_notice(case)
    return max(total - case.leave_days_extended, 0)


def leave_state(case):
    return "todo" if pending_extension(case) else "done"


@transaction.atomic
def extend_notice_for_leave(case, by):
    """Move the last working day out by the leave days not yet made up."""
    case = OffboardingEmployee.objects.entire().select_for_update().get(pk=case.pk)
    if case.status != "in_progress" or not case.last_working_day:
        raise ExitError(_("Only an exit in progress can be extended."))
    _rows, total = leave_in_notice(case)
    extra = total - case.leave_days_extended
    if extra <= 0:
        raise ExitError(_("There is no leave left to make up."))
    new_day = case.last_working_day + timedelta(days=extra)
    rule = exit_service._settings_for(case.employee_id).last_working_day_holiday_rule
    new_day = adjust_last_working_day(new_day, rule, case.employee_id)
    case = exit_service.change_last_working_day(
        case,
        new_day,
        _("Notice extended by %(days)s day(s) of approved leave.") % {"days": extra},
        by,
    )
    case.leave_days_extended = total
    case.save()
    return case, extra


# --------------------------------------------------------------------------
# the progress icons shown on the Exits in Progress tab
# --------------------------------------------------------------------------


# Tabs switched off for now. Their pages, URLs and data are kept; remove a key from
# this set to bring that tab (and its progress icon) back.
HIDDEN_SECTIONS = {"team", "leave"}


def sections(case):
    from offboarding import exit_survey

    found = [
        {"key": "assets", "label": _("Assets"), "icon": "laptop-outline", "state": assets_state(case)},
        {"key": "team", "label": _("Team"), "icon": "people-outline", "state": team_state(case)},
        {"key": "tasks", "label": _("Tasks"), "icon": "checkbox-outline", "state": exit_tasks.tasks_state(case)},
        {"key": "leave", "label": _("Leave settings"), "icon": "calendar-outline", "state": leave_state(case)},
    ]
    if exit_survey.survey_applies(case):
        found.insert(
            1,
            {"key": "survey", "label": _("Survey"), "icon": "chatbox-ellipses-outline", "state": exit_survey.survey_state(case)},
        )
    return [item for item in found if item["key"] not in HIDDEN_SECTIONS]
