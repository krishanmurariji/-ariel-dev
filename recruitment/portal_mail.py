"""
recruitment/portal_mail.py

The "application received" mail with the candidate's portal login details, sent when a
candidate is created (they applied, or staff added them).

It is built in - nobody has to create a mail template or a mail automation. It follows the
setting Settings > Recruitment > Candidate Self Tracking > "Send Candidate Portal Mail", which
only takes effect while Application Tracking is also on, the same rule the setting has always
stated. The setting that counts is the one of the candidate's recruitment's company; a
setting that is not tied to a company is the fallback.

If a mail automation that sends this same mail is still switched on (set up earlier by hand),
it keeps being the sender and this stays quiet, so a candidate never gets it twice.
"""

import logging

from django.utils.html import escape
from django.utils.translation import gettext as _

from base.backends import ConfiguredEmailBackend
from base.mail_helpers import action_button, greeting, paragraph
from base.mail_letterhead import build_letterhead_email

logger = logging.getLogger(__name__)

PORTAL_TEMPLATE_TITLE = "Candidate Portal Login"
PORTAL_PATH = "/recruitment/candidate-login/"


def _company_of(candidate):
    return getattr(getattr(candidate, "recruitment_id", None), "company_id", None)


def portal_mail_enabled(candidate):
    """Whether the setting asks for this mail for ``candidate`` (own company first, then global)."""
    from recruitment.models import RecruitmentGeneralSetting

    company = _company_of(candidate)
    row = None
    if company is not None:
        row = RecruitmentGeneralSetting.objects.filter(company_id=company).first()
    if row is None:
        row = RecruitmentGeneralSetting.objects.filter(company_id__isnull=True).first()
    return bool(row and row.candidate_self_tracking and row.send_candidate_portal_mail)


def automation_sends_it():
    """True while a switched-on mail automation sends this mail already."""
    from horilla_automations.models import MailAutomation

    return MailAutomation.objects.filter(
        model="recruitment.models.Candidate",
        mail_template__title=PORTAL_TEMPLATE_TITLE,
        trigger="on_create",
        is_active=True,
    ).exists()


def portal_link():
    """The candidate portal's address: the site the person applied on, else a configured origin."""
    from django.conf import settings

    from horilla.horilla_middlewares import _thread_locals

    request = getattr(_thread_locals, "request", None)
    if request is not None:
        try:
            return request.build_absolute_uri(PORTAL_PATH)
        except Exception:  # an unusable host header must not stop the mail
            pass
    for origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or []:
        if origin.startswith("http"):
            return origin.rstrip("/") + PORTAL_PATH
    return PORTAL_PATH


def _job_title(candidate):
    position = getattr(candidate, "job_position_id", None)
    return getattr(position, "job_position", None) or getattr(
        getattr(candidate, "recruitment_id", None), "title", ""
    )


def portal_mail_content(candidate, link):
    """
    The inside of the mail (the letterhead is added around it): a button to the candidate
    portal, and how to sign in - the details themselves are not printed in the mail.
    """
    job = _job_title(candidate)
    intro = (
        _("Thank you for applying for %(job)s. We have received your application.") % {"job": job}
        if job
        else _("Thank you for applying. We have received your application.")
    )
    name = candidate.get_full_name() if hasattr(candidate, "get_full_name") else candidate.name
    return (
        greeting(name)
        + paragraph(escape(intro) + " " + escape(_("You can follow the progress of your application on the candidate portal.")))
        + action_button(link, _("Open Candidate Portal"))
        + paragraph(
            escape(_("To sign in, your email address is your username and your mobile number is your password.")),
            size=13,
        )
        + paragraph(escape(_("For security, please keep these details private.")), size=13, color="#888888")
    )


def send_portal_mail(candidate, link=None):
    """
    Send the mail to ``candidate`` if the setting asks for it. Returns True if a mail went out.
    Never raises: the candidate is already saved by the time this runs. ``link`` is the portal
    address worked out while the request is still known (see portal_link()).
    """
    try:
        if not candidate.email or not portal_mail_enabled(candidate):
            return False
        if automation_sends_it():
            return False
        link = link or portal_link()
        job = _job_title(candidate)
        subject = (
            _("We received your application for %(job)s") % {"job": job}
            if job
            else _("We received your application")
        )
        message = build_letterhead_email(
            subject=subject,
            content_html=portal_mail_content(candidate, link),
            to=candidate.email,
            tag_label=_("Recruitment"),
            team_label=_("Recruitment Team"),
            company=_company_of(candidate),
            from_email=ConfiguredEmailBackend().dynamic_from_email_with_display_name,
        )
        return bool(message.send())
    except Exception as error:
        logger.error("Candidate portal mail for candidate %s not sent: %s", getattr(candidate, "pk", None), error)
        return False
