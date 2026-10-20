"""
recruitment/rejection_mail.py

The "Application Status - Not Selected" mail, sent to a candidate when they are rejected
(a rejection record is created, from the Update Status window or "Add to Reject").

It is built in - nobody has to create a mail template or a mail automation. The wording is
the one the mail template has always had: one version for someone rejected after an
interview, another for someone rejected at application stage.

If a mail automation for this mail already exists (set up earlier by hand), it owns the mail:
switched on, it keeps sending it; switched off, an admin has chosen not to send it. Either way
this stays quiet, so a candidate is never mailed twice and a switched-off mail stays off.
"""

import logging

from django.utils.html import escape
from django.utils.translation import gettext as _

from base.backends import ConfiguredEmailBackend
from base.mail_letterhead import build_letterhead_email

logger = logging.getLogger(__name__)

TEMPLATE_TITLE = "Application Status - Not Selected"
_P = 'style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;"'


def automation_owns_it():
    """True if a mail automation for this mail exists, on or off."""
    from horilla_automations.models import MailAutomation

    return MailAutomation.objects.filter(
        model="recruitment.models.RejectedCandidate",
        mail_template__title=TEMPLATE_TITLE,
    ).exists()


def _company_of(candidate):
    return getattr(getattr(candidate, "recruitment_id", None), "company_id", None)


def _job_title(candidate):
    position = getattr(candidate, "job_position_id", None)
    return getattr(position, "job_position", None) or getattr(
        getattr(candidate, "recruitment_id", None), "title", ""
    )


def rejection_mail_content(candidate):
    """The inside of the mail (the letterhead is added around it)."""
    company = _company_of(candidate)
    where = f"<strong>{escape(str(company))}</strong>" if company else escape(_("our company"))
    greeting = (
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">%s <strong>%s</strong>,</p>'
        % (escape(_("Dear")), escape(candidate.name or ""))
    )
    if candidate.stage_type_at_rejection == "interview":
        paragraphs = [
            _("Thank you for interviewing at %(where)s.") % {"where": "@@WHERE@@"},
            escape(
                _(
                    "After careful consideration, we regret to inform you that your profile "
                    "has not been selected for the next round."
                )
            ),
            escape(
                _(
                    "We appreciate the time and effort you invested in the interview process "
                    "and wish you all the best in your future endeavors."
                )
            ),
            escape(
                _(
                    "You are welcome to apply again after 3 months should a suitable opening "
                    "arise that matches your skills and experience."
                )
            ),
        ]
    else:
        paragraphs = [
            _("Thank you for applying for the %(job)s position at %(where)s.")
            % {"job": "@@JOB@@", "where": "@@WHERE@@"},
            escape(
                _(
                    "After careful consideration, we regret to inform you that your "
                    "application has not been successful at this time."
                )
            ),
            escape(
                _(
                    "We sincerely appreciate the time and effort you invested in your "
                    "application. We encourage you to apply again after 3 months - we'd be "
                    "glad to consider you for future opportunities that match your skills "
                    "and career goals."
                )
            ),
        ]
    body = "".join(f"<p {_P}>{p}</p>" for p in paragraphs)
    body = body.replace("@@WHERE@@", where).replace("@@JOB@@", f"<strong>{escape(str(_job_title(candidate)))}</strong>")
    return greeting + body


def send_rejection_mail(candidate):
    """
    Send the mail to ``candidate``. Returns True if a mail went out. Never raises: the
    rejection is already saved by the time this runs.
    """
    try:
        if not candidate.email or automation_owns_it():
            return False
        job = _job_title(candidate)
        subject = (
            _("Update on your application for %(job)s") % {"job": job}
            if job
            else _("Update on your application")
        )
        message = build_letterhead_email(
            subject=subject,
            content_html=rejection_mail_content(candidate),
            to=candidate.email,
            tag_label=_("Recruitment"),
            team_label=_("Recruitment Team"),
            company=_company_of(candidate),
            from_email=ConfiguredEmailBackend().dynamic_from_email_with_display_name,
        )
        return bool(message.send())
    except Exception as error:
        logger.error("Rejection mail for candidate %s not sent: %s", getattr(candidate, "pk", None), error)
        return False
