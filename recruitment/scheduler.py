import calendar
import datetime as dt
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.relativedelta import relativedelta

END_DATE_REMINDER_DAYS_BEFORE = 5
CLOSING_REMINDER_MAIL_TEMPLATE_TITLE = "Job Opening Closing Reminder"


def recruitment_close():
    """
    Closes recruitment campaigns that have reached their end date.

    """
    from recruitment.models import Recruitment

    today_date = datetime.now().date()

    recruitments = Recruitment.objects.filter(closed=False)

    for rec in recruitments:
        if rec.end_date:
            if rec.end_date == today_date:
                rec.closed = True
                rec.is_published = False
                rec.save()


def _send_closing_reminder_mail(rec, template_body, email_backend):
    """Emails every recruitment head assigned to `rec` that it's closing soon."""
    import logging
    from django import template as django_template
    from django.core.mail import EmailMessage

    heads = rec.recruitment_heads.select_related("employee_work_info")
    to_emails = list(filter(None, {head.get_mail() for head in heads}))
    if not to_emails:
        return

    rendered_body = django_template.Template(template_body).render(
        django_template.Context({"instance": rec})
    )
    try:
        email = EmailMessage(
            subject=f'Job opening "{rec}" closes on {rec.end_date}',
            body=rendered_body,
            from_email=email_backend.dynamic_from_email_with_display_name,
            to=to_emails,
        )
        email.content_subtype = "html"
        email.send()
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to send closing reminder mail for recruitment %s", rec.pk
        )


def recruitment_end_date_reminder():
    """
    Notifies (in-app) and emails every recruitment head assigned to a
    recruitment (recruitment_heads) once, when its end date is exactly
    END_DATE_REMINDER_DAYS_BEFORE days away, so they get a heads-up before
    it auto-closes and is unpublished (see recruitment_close). Interviewers
    (recruitment_managers) are not notified. Guarded by
    Recruitment.end_date_reminder_sent so it only fires once per end date -
    re-armed automatically if the date is later moved (Recruitment.save()).
    """
    from django.urls import reverse
    from notifications.signals import notify

    from base.backends import ConfiguredEmailBackend
    from base.models import HorillaMailTemplate
    from recruitment.models import Recruitment

    today_date = datetime.now().date()
    reminder_date = today_date + timedelta(days=END_DATE_REMINDER_DAYS_BEFORE)

    recruitments = Recruitment.objects.filter(
        closed=False,
        end_date=reminder_date,
        end_date_reminder_sent=False,
    )

    mail_template = HorillaMailTemplate.objects.filter(
        title=CLOSING_REMINDER_MAIL_TEMPLATE_TITLE
    ).first()
    email_backend = ConfiguredEmailBackend() if mail_template else None

    for rec in recruitments:
        heads = rec.recruitment_heads.select_related("employee_user_id")
        users = [head.employee_user_id for head in heads if head.employee_user_id]
        if users:
            notify.send(
                rec,
                recipient=users,
                verb=f"Recruitment {rec} closes on {rec.end_date} "
                f"({END_DATE_REMINDER_DAYS_BEFORE} days left).",
                verb_ar=f"يُغلق التوظيف {rec} في {rec.end_date} "
                f"(متبقي {END_DATE_REMINDER_DAYS_BEFORE} أيام).",
                verb_de=f"Rekrutierung {rec} schließt am {rec.end_date} "
                f"(noch {END_DATE_REMINDER_DAYS_BEFORE} Tage).",
                verb_es=f"La contratación {rec} se cierra el {rec.end_date} "
                f"(quedan {END_DATE_REMINDER_DAYS_BEFORE} días).",
                verb_fr=f"Le recrutement {rec} se termine le {rec.end_date} "
                f"({END_DATE_REMINDER_DAYS_BEFORE} jours restants).",
                icon="time-outline",
                redirect=reverse("pipeline"),
            )
        if mail_template:
            _send_closing_reminder_mail(rec, mail_template.body, email_backend)
        rec.end_date_reminder_sent = True
        rec.save()


def candidate_convert():
    """
    Converts candidates to a "converted" state if they already exist as users.
    """
    from horilla_auth.models import HorillaUser
    from recruitment.models import Candidate

    mails = list(
        Candidate.objects.filter(is_active=True).values_list("email", flat=True)
    )
    existing_emails = list(
        HorillaUser.objects.filter(email__in=mails).values_list("email", flat=True)
    )
    Candidate.objects.filter(
        is_active=True,
        email__in=existing_emails,
        converted=False,
    ).update(converted=True)


def poll_candidate_intake_mailboxes():
    """
    Polls every active CandidateIntakeMailbox for new application emails
    and turns them into Candidate records (or logs why one couldn't be
    created). See recruitment/intake.py. Guarded so this never runs at
    the same time as a manually-triggered "Run Poll Now".
    """
    import logging

    from recruitment.intake import run_guarded_poll

    try:
        ran = run_guarded_poll()
        if not ran:
            logging.getLogger(__name__).info(
                "Skipped scheduled candidate intake poll - one was already running"
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "Candidate intake mailbox polling failed"
        )


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell", "test"]
):
    """
    Initializes and starts background tasks using APScheduler when the server is running.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(candidate_convert, "interval", minutes=5)
    scheduler.add_job(recruitment_close, "interval", hours=1)
    scheduler.add_job(
        recruitment_end_date_reminder,
        "interval",
        hours=1,
        id="recruitment_end_date_reminder",
    )
    scheduler.add_job(
        poll_candidate_intake_mailboxes,
        "interval",
        minutes=5,
        id="poll_candidate_intake_mailboxes",
    )

    scheduler.start()
