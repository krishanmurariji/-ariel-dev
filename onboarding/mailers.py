"""
onboarding/mailers.py

Reporting Manager / Onboarding Buddy assignment emails. Sent once, when a
candidate's onboarding configuration is finalized (stepper.finalize_onboarding),
and again for the Reporting Manager specifically whenever it's changed on a
candidate whose onboarding was already finalized (stepper._save_step's
"details" branch) - the old manager is told they've been replaced, the new
one gets the same assignment mail as on first finalize.
"""

import logging

from django.utils.html import escape
from django.utils.translation import gettext_lazy as _

from base.backends import ConfiguredEmailBackend
from base.mail_letterhead import build_letterhead_email

logger = logging.getLogger(__name__)

_TAG_LABEL = _("Onboarding")
_TEAM_LABEL = _("HR Team")

REPORTING_MANAGER_ASSIGNED_CONTENT = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>%(recipient)s</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You have been assigned as the Reporting Manager for our newest team member:</p>
<div style="margin:24px 0;padding:20px;background:#f4f8f6;border-left:4px solid #0f6e5c;border-radius:8px;">
  <p style="margin:0 0 6px;font-size:15px;color:#2d2d2d;"><strong>%(candidate)s</strong></p>
  <p style="margin:0 0 4px;font-size:13px;color:#555555;">%(designation)s</p>
  <p style="margin:0;font-size:13px;color:#555555;">Joining on %(joining_date)s</p>
</div>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">As their Reporting Manager, you will be their primary point of contact for guidance, expectations, and support as they settle into the role. Please plan a brief introductory conversation with them during their first week.</p>
<p style="margin:0;font-size:13px;color:#888888;">If you have any questions about this assignment, please reach out to HR.</p>"""

ONBOARDING_BUDDY_ASSIGNED_CONTENT = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>%(recipient)s</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You have been assigned as the Onboarding Buddy for our newest team member:</p>
<div style="margin:24px 0;padding:20px;background:#f4f8f6;border-left:4px solid #0f6e5c;border-radius:8px;">
  <p style="margin:0 0 6px;font-size:15px;color:#2d2d2d;"><strong>%(candidate)s</strong></p>
  <p style="margin:0 0 4px;font-size:13px;color:#555555;">%(designation)s</p>
  <p style="margin:0;font-size:13px;color:#555555;">Joining on %(joining_date)s</p>
</div>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">As their Onboarding Buddy, you will be their friendly go-to person for the informal questions - where things are, how things work, and how to settle in quickly. A short welcome message before their first day goes a long way.</p>
<p style="margin:0;font-size:13px;color:#888888;">If you have any questions about this role, please reach out to HR.</p>"""

REPORTING_MANAGER_REMOVED_CONTENT = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>%(recipient)s</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">This is to let you know that the reporting structure for <strong>%(candidate)s</strong> has been updated, and you are no longer their Reporting Manager as of today.</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Thank you for the support you have provided during their onboarding so far. No further action is needed on your part.</p>
<p style="margin:0;font-size:13px;color:#888888;">If you believe this change was made in error, please reach out to HR.</p>"""


def _send(subject, content_html, to_email, company):
    if not to_email:
        return
    email_backend = ConfiguredEmailBackend()
    display_email_name = email_backend.dynamic_from_email_with_display_name
    try:
        message = build_letterhead_email(
            subject=subject,
            content_html=content_html,
            to=to_email,
            tag_label=_TAG_LABEL,
            team_label=_TEAM_LABEL,
            company=company,
            from_email=display_email_name,
            reply_to=[display_email_name],
        )
        message.send()
    except Exception as e:
        logger.error("Onboarding notification mail not sent to %s: %s", to_email, e)


def _candidate_designation(candidate):
    if candidate.job_position_id_id:
        return candidate.job_position_id.job_position or _("New Hire")
    return _("New Hire")


def _candidate_company(candidate):
    return getattr(candidate.recruitment_id, "company_id", None)


def notify_reporting_manager_assigned(candidate, employee):
    """
    Emails `employee` that they've been assigned as Reporting Manager for
    `candidate` - sent once onboarding configuration is finalized, and
    again for whoever the Reporting Manager is changed to afterward.
    """
    if not employee:
        return
    _send(
        subject=_("You've been assigned as %(candidate)s's Reporting Manager")
        % {"candidate": candidate.name},
        content_html=REPORTING_MANAGER_ASSIGNED_CONTENT
        % {
            "recipient": escape(employee.get_full_name()),
            "candidate": escape(candidate.name),
            "designation": escape(_candidate_designation(candidate)),
            "joining_date": candidate.joining_date.strftime("%b %d, %Y")
            if candidate.joining_date
            else _("Not yet set"),
        },
        to_email=employee.get_mail(),
        company=_candidate_company(candidate),
    )


def notify_onboarding_buddy_assigned(candidate, employee):
    """
    Emails `employee` that they've been assigned as Onboarding Buddy for
    `candidate` - sent once onboarding configuration is finalized.
    """
    if not employee:
        return
    _send(
        subject=_("You've been assigned as %(candidate)s's Onboarding Buddy")
        % {"candidate": candidate.name},
        content_html=ONBOARDING_BUDDY_ASSIGNED_CONTENT
        % {
            "recipient": escape(employee.get_full_name()),
            "candidate": escape(candidate.name),
            "designation": escape(_candidate_designation(candidate)),
            "joining_date": candidate.joining_date.strftime("%b %d, %Y")
            if candidate.joining_date
            else _("Not yet set"),
        },
        to_email=employee.get_mail(),
        company=_candidate_company(candidate),
    )


def notify_reporting_manager_removed(candidate, employee):
    """
    Emails `employee` (the *previous* Reporting Manager) that they are no
    longer `candidate`'s Reporting Manager, following a reassignment.
    """
    if not employee:
        return
    _send(
        subject=_("Update: You are no longer %(candidate)s's Reporting Manager")
        % {"candidate": candidate.name},
        content_html=REPORTING_MANAGER_REMOVED_CONTENT
        % {
            "recipient": escape(employee.get_full_name()),
            "candidate": escape(candidate.name),
        },
        to_email=employee.get_mail(),
        company=_candidate_company(candidate),
    )
