"""
offboarding/exit_policy.py

The notice-period policy an employee is told about when they resign.
"""

from datetime import date, timedelta

from django.apps import apps

from horilla.methods import get_horilla_model_class


# What the Offboarding Rules settings page shows (and the setting's own
# default) while no notice period has been saved yet.
DEFAULT_NOTICE_PERIOD_DAYS = 30


def notice_period_days(employee):
    """
    Days of notice this employee must serve. None only when a notice period
    of zero has been set on purpose (so there is no notice end date).

    Their active contract's own notice period wins; otherwise the payroll
    general setting for their company, then the company-less one, and - on a
    single-company install where there is just one such setting - that one.
    With nothing saved at all, the same default the settings page displays.
    """
    if not apps.is_installed("payroll"):
        return DEFAULT_NOTICE_PERIOD_DAYS

    contract = (
        get_horilla_model_class(app_label="payroll", model="contract")
        .objects.filter(employee_id=employee, contract_status="active")
        .first()
    )
    if contract and contract.notice_period_in_days:
        return contract.notice_period_in_days

    settings_model = get_horilla_model_class(
        app_label="payroll", model="payrollgeneralsetting"
    )
    work_info = getattr(employee, "employee_work_info", None)
    company_id = getattr(work_info, "company_id_id", None)
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
