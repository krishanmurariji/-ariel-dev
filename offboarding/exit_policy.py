"""
offboarding/exit_policy.py

The notice-period rules an employee is told about when they resign, and the
last-working-day adjustments Exit settings can ask for.
"""

from datetime import date, timedelta

from django.apps import apps

from base.methods import get_company_leave_dates, get_holiday_dates
from horilla.methods import get_horilla_model_class
from offboarding.models import EmployeeNoticePolicy, NoticePeriodPolicy

# What the Offboarding Rules settings page shows (and the setting's own
# default) while no notice period has been saved yet.
DEFAULT_NOTICE_PERIOD_DAYS = 30


def _company_id(employee):
    work_info = getattr(employee, "employee_work_info", None)
    return getattr(work_info, "company_id_id", None)


def notice_period_days(employee):
    """
    Days of notice this employee must serve. None only when a notice period
    of zero has been set on purpose (so there is no notice end date).

    In order: the notice period policy assigned to them, their active
    contract's own notice period, the default policy (their company's, else
    the global one), the payroll general setting, and finally the 30 days the
    settings page shows when nothing at all is saved.
    """
    assigned = (
        EmployeeNoticePolicy.objects.entire()
        .filter(employee_id=employee)
        .select_related("policy_id")
        .first()
    )
    if assigned:
        return assigned.policy_id.days or None

    company_id = _company_id(employee)
    if apps.is_installed("payroll"):
        contract = (
            get_horilla_model_class(app_label="payroll", model="contract")
            .objects.filter(employee_id=employee, contract_status="active")
            .first()
        )
        if contract and contract.notice_period_in_days:
            return contract.notice_period_in_days

    policies = NoticePeriodPolicy.objects.entire().filter(is_default=True)
    default = None
    if company_id:
        default = policies.filter(company_id=company_id).first()
    if default is None:
        default = policies.filter(company_id__isnull=True).first()
    if default:
        return default.days or None

    if not apps.is_installed("payroll"):
        return DEFAULT_NOTICE_PERIOD_DAYS

    settings_model = get_horilla_model_class(
        app_label="payroll", model="payrollgeneralsetting"
    )
    rows = settings_model.objects.order_by("pk")
    row = None
    if company_id:
        row = rows.filter(company_id=company_id).first()
    if row is None:
        row = rows.filter(company_id__isnull=True).first()
    if row is None and rows.count() == 1:
        row = rows.first()
    if row is None:
        return DEFAULT_NOTICE_PERIOD_DAYS
    return row.notice_period or None


def notice_period_end(days, start=None):
    start = start or date.today()
    return start + timedelta(days=days) if days else None


def is_non_working_day(day, employee=None):
    """A holiday or a company weekly off (never looks at the person's own leave)."""
    return day in get_holiday_dates(day, day, employee) or day in get_company_leave_dates(
        day.year
    )


def adjust_last_working_day(day, rule, employee=None):
    """
    Apply Exit settings' "if the last working day is a holiday or weekly off"
    rule: "none" keeps it; "before" steps back, "after" steps forward, to the
    nearest working day. Bounded so a bad calendar can never loop forever.
    """
    if not day or rule not in ("before", "after"):
        return day
    step = timedelta(days=-1 if rule == "before" else 1)
    for _ in range(31):
        if not is_non_working_day(day, employee):
            return day
        day += step
    return day
