"""
payroll_pro/mail.py

Threaded payslip emailing, following the stock payroll module's
MailSendThread pattern (ConfiguredEmailBackend + PDF attachment).
"""

import logging
from threading import Thread

from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from base.backends import ConfiguredEmailBackend

logger = logging.getLogger(__name__)


class PayslipMailThread(Thread):
    """
    Sends payslip emails (PDF attached) for the given PayrollRunRecord ids.
    """

    def __init__(self, request, record_ids):
        Thread.__init__(self)
        self.record_ids = list(record_ids)
        self.request = request
        self.host = request.get_host()
        self.protocol = "https" if request.is_secure() else "http"

    def run(self) -> None:
        from payroll_pro.models import PayrollRunRecord
        from payroll_pro.views import MONTH_NAMES, _payslip_context

        for record_id in self.record_ids:
            record = PayrollRunRecord.objects.filter(pk=record_id).first()
            if not record or not record.computed:
                continue
            employee = record.employee_id
            run = record.run_id
            period = f"{MONTH_NAMES[run.month - 1]} {run.year}"
            try:
                html_message = render_to_string(
                    "payroll_pro/mail_templates/payslip_mail.html",
                    {
                        "employee": employee,
                        "period": period,
                        "record": record,
                        "host": self.host,
                        "protocol": self.protocol,
                    },
                    request=self.request,
                )
                pdf_html = render_to_string(
                    "payroll_pro/payslip_pdf.html",
                    _payslip_context(record),
                    request=self.request,
                )
                attachments = []
                try:
                    import pdfkit

                    pdf = pdfkit.from_string(
                        pdf_html,
                        False,
                        options={
                            "page-size": "A4",
                            "encoding": "UTF-8",
                            "enable-local-file-access": None,
                        },
                    )
                    attachments.append(
                        (f"Payslip-{period}.pdf", pdf, "application/pdf")
                    )
                except Exception:
                    logger.exception(
                        "Payslip PDF generation failed; sending mail without "
                        "attachment"
                    )

                email_backend = ConfiguredEmailBackend()
                display_email_name = (
                    email_backend.dynamic_from_email_with_display_name
                )
                if self.request:
                    try:
                        sender = self.request.user.employee_get
                        display_email_name = (
                            f"{sender.get_full_name()} <{sender.email}>"
                        )
                    except Exception:
                        pass

                email = EmailMessage(
                    f"Hello {employee.get_full_name()}, your payslip for "
                    f"{period} is ready!",
                    html_message,
                    display_email_name,
                    [employee.get_mail()],
                    reply_to=[display_email_name],
                )
                email.attachments = attachments
                email.content_subtype = "html"
                email.send()
                record.sent_to_employee = True
                record.save(update_fields=["sent_to_employee"])
            except Exception:
                logger.exception("Failed to send payslip mail for %s", record)
        return
