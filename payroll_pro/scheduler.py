"""
payroll_pro/scheduler.py

Automatic payroll-run scheduling, mirroring the stock payroll module's
APScheduler pattern. Per company, ``PayrollProAutoSetting`` decides the day
of the month on which the previous month's run is generated, whether it is
auto-finalized, and whether payslips are emailed automatically.
"""

import logging
import sys
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


def _is_last_day_of_month(day: date) -> bool:
    return (day + timedelta(days=1)).month != day.month


def _should_run_today(setting, today: date) -> bool:
    if setting.run_day == "last day":
        return _is_last_day_of_month(today)
    try:
        return int(setting.run_day) == today.day
    except (TypeError, ValueError):
        return False


def auto_payroll_run():
    """
    Create + compute (and optionally finalize / email) the previous month's
    payroll run for every company whose auto-run setting matches today.

    Each company is processed independently, in its own PayrollRun, using
    only that company's own employees - a company without its own
    PayrollProAutoSetting falls back to the single global (company_id=None)
    setting, if one exists and is due today.
    """
    from base.models import Company
    from payroll_pro.models import PayrollProAutoSetting

    today = date.today()
    all_settings = list(PayrollProAutoSetting.objects.filter(auto_run=True))
    setting_by_company = {
        setting.company_id_id: setting
        for setting in all_settings
        if setting.company_id_id is not None
    }
    global_setting = next(
        (setting for setting in all_settings if setting.company_id_id is None), None
    )

    due = []
    for company in Company.objects.all():
        setting = setting_by_company.get(company.pk, global_setting)
        if setting and _should_run_today(setting, today):
            due.append((company, setting))
    if not due:
        return

    previous = today - relativedelta(months=1)
    year, month = previous.year, previous.month

    for company, setting in due:
        try:
            _auto_run_company(company, setting, year, month, today)
        except Exception:
            logger.exception(
                "Auto payroll run failed for company %s (%s/%s)",
                company,
                month,
                year,
            )


def _auto_run_company(company, setting, year, month, today):
    """
    Create/compute (and optionally finalize/email) one company's previous
    month payroll run. Runs with that company set as the thread-local
    selected company so every company-scoped manager (EmployeeSalary,
    PayrollRun, PayrollRunRecord, ...) only ever sees that company's data -
    reset in ``finally`` since APScheduler reuses worker threads, and a
    leftover selected company would silently leak into the next job run on
    the same thread (including unrelated web requests, in the worst case).
    """
    from horilla.horilla_middlewares import set_selected_company
    from payroll_pro.models import EmployeeSalary, PayrollRun, PayrollRunRecord
    from payroll_pro.services import get_month_working_days
    from payroll_pro import services

    set_selected_company(company.pk)
    try:
        run = PayrollRun.objects.filter(year=year, month=month).first()
        if run and run.is_locked:
            return
        if run is None:
            run = PayrollRun.objects.create(
                year=year,
                month=month,
                company_id=company,
                total_days=get_month_working_days(year, month),
            )

        period_end = run.period_end
        existing_ids = set(run.records.values_list("employee_id", flat=True))
        for revision in EmployeeSalary.current_salaries(on_date=period_end):
            if revision.employee_id_id in existing_ids:
                continue
            if not revision.employee_id.is_active:
                continue
            attendance = services.get_attendance_and_lop(
                revision.employee_id, year, month
            )
            PayrollRunRecord.objects.create(
                run_id=run,
                employee_id=revision.employee_id,
                salary_revision_id=revision,
                present_days=attendance["present_days"],
                lop_days=attendance["lop_days"],
            )

        if not setting.auto_finalize:
            logger.info(
                "Auto payroll run created as draft for %s %s/%s", company, month, year
            )
            return

        total_net = 0.0
        for record in run.records.all():
            if record.pay_action != "process":
                continue
            revision = record.salary_revision_id or EmployeeSalary.current_for(
                record.employee_id, period_end
            )
            if revision is None:
                continue
            record.salary_revision_id = revision
            calc = services.compute_for_record(record)
            record.computed = calc
            record.gross_earnings = calc["gross_earnings"]
            record.total_deductions = calc["total_deductions"]
            record.net_pay = calc["net_pay"]
            record.save()
            total_net += calc["net_pay"]
        run.status = "completed"
        run.total_net_pay = round(total_net, 2)
        run.steps_data = dict(
            run.steps_data or {},
            finalize={"at": today.strftime("%d %b %Y"), "by": "Auto Scheduler"},
        )

        email = setting.auto_email_payslips
        if email:
            run.payslips_released = True
        run.save()

        if email:
            _send_auto_mails(run)
        logger.info("Auto payroll run finalized for %s %s/%s", company, month, year)
    finally:
        set_selected_company(None)


def _send_auto_mails(run):
    """Email payslips without an HTTP request context."""
    from django.core.mail import EmailMessage
    from django.template.loader import render_to_string

    from base.backends import ConfiguredEmailBackend
    from payroll_pro.views import MONTH_NAMES, _payslip_context

    period = f"{MONTH_NAMES[run.month - 1]} {run.year}"
    for record in run.records.filter(pay_action="process", computed__isnull=False):
        employee = record.employee_id
        try:
            html_message = render_to_string(
                "payroll_pro/mail_templates/payslip_mail.html",
                {"employee": employee, "period": period, "record": record},
            )
            attachments = []
            try:
                import pdfkit

                pdf_html = render_to_string(
                    "payroll_pro/payslip_pdf.html", _payslip_context(record)
                )
                pdf = pdfkit.from_string(
                    pdf_html,
                    False,
                    options={"page-size": "A4", "encoding": "UTF-8"},
                )
                attachments.append((f"Payslip-{period}.pdf", pdf, "application/pdf"))
            except Exception:
                logger.exception("Auto payslip PDF failed; mailing without it")
            email_backend = ConfiguredEmailBackend()
            email = EmailMessage(
                f"Hello {employee.get_full_name()}, your payslip for {period} "
                "is ready!",
                html_message,
                email_backend.dynamic_from_email_with_display_name,
                [employee.get_mail()],
            )
            email.attachments = attachments
            email.content_subtype = "html"
            email.send()
            record.sent_to_employee = True
            record.save(update_fields=["sent_to_employee"])
        except Exception:
            logger.exception("Auto payslip mail failed for %s", record)


if not any(
    cmd in sys.argv
    for cmd in [
        "makemigrations",
        "migrate",
        "compilemessages",
        "flush",
        "shell",
        "collectstatic",
        "test",
    ]
):
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_payroll_run, "interval", hours=3)
    scheduler.start()
