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

DOCUMENT_REMINDER_CONTENT = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>%(candidate)s</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">This is a friendly reminder that we're still waiting on one document to move your onboarding forward:</p>
<div style="margin:24px 0;padding:20px;background:#fff9f0;border-left:4px solid #f59f00;border-radius:8px;">
  <p style="margin:0 0 8px;font-size:15px;color:#2d2d2d;"><strong>%(title)s</strong>%(mandatory_badge)s</p>
  <p style="margin:0;font-size:13px;color:#555555;">Accepted format: %(format)s%(max_size)s</p>
</div>
<p style="margin:0 0 24px;font-size:14px;color:#444444;line-height:1.7;">Please upload it at your earliest convenience using the button below.</p>
<div style="text-align:center;margin:0 0 24px;">
  <a href="%(portal_url)s" style="display:inline-block;padding:12px 28px;background:#0f6e5c;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;border-radius:6px;">Upload Document</a>
</div>
<p style="margin:0;font-size:13px;color:#888888;">If you've already sent this across or have any trouble uploading it, please reach out to us and we'll be happy to help.</p>"""


def _send(subject, content_html, to_email, company):
    if not to_email:
        logger.warning("Onboarding notification '%s' not sent: the person has no email address.", subject)
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


def notify_candidate_document_reminder(candidate, document, portal_url):
    """
    Emails the candidate a reminder to upload one specific document that's
    still pending (requested but no file submitted yet), with a direct
    link back into their onboarding portal.
    """
    if not candidate.email:
        return
    request_obj = document.document_request_id
    display_title = request_obj.title if request_obj else document.title
    mandatory_badge = (
        ' <span style="font-size:11px;font-weight:600;color:#b02a37;'
        'background:#fbe4e6;padding:2px 8px;border-radius:10px;'
        'vertical-align:middle;">REQUIRED</span>'
        if request_obj and request_obj.is_mandatory
        else ""
    )
    max_size = (
        f" (max {request_obj.max_size} MB)" if request_obj and request_obj.max_size else ""
    )
    _send(
        subject=_("Reminder: please upload %(title)s") % {"title": display_title},
        content_html=DOCUMENT_REMINDER_CONTENT
        % {
            "candidate": escape(candidate.name),
            "title": escape(display_title),
            "mandatory_badge": mandatory_badge,
            "format": escape(request_obj.get_format_display() if request_obj else "Any"),
            "max_size": escape(max_size),
            "portal_url": portal_url,
        },
        to_email=candidate.email,
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


PROBATION_CYCLE_STARTED_CONTENT = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>%(recipient)s</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">A monthly Probation Feedback Cycle has been set up for <strong>%(candidate)s</strong>.%(role_line)s</p>
<div style="margin:24px 0;padding:20px;background:#f4f8f6;border-left:4px solid #0f6e5c;border-radius:8px;">
  <p style="margin:0 0 10px;font-size:13px;color:#2d2d2d;font-weight:600;">Review schedule (%(total_months)s month%(plural)s)</p>
  %(schedule_rows)s
</div>
<p style="margin:0;font-size:13px;color:#888888;">You'll receive a separate email with the actual review form each time one opens up.</p>"""

_SCHEDULE_ROW = (
    '<p style="margin:0 0 6px;font-size:13px;color:#555555;">'
    '<strong>Month %(month)s</strong> — %(date)s%(tag)s</p>'
)


def _schedule_rows_html(cycle_dates):
    rows = []
    for index, cycle_date in enumerate(cycle_dates, start=1):
        tag = (
            ' <span style="font-size:11px;color:#0f6e5c;">(includes buddy/colleague)</span>'
            if index == 1
            else ""
        )
        rows.append(
            _SCHEDULE_ROW % {"month": index, "date": cycle_date.strftime("%b %d, %Y"), "tag": tag}
        )
    return "".join(rows)


def notify_probation_feedback_cycle_started(
    candidate, cycle, reporting_manager, buddy=None, colleague=None
):
    """
    Emails everyone involved in a newly-started Probation Feedback Cycle -
    the candidate themself, the reporting manager, and (only relevant for
    month 1) the buddy and colleague if one was picked - each told the
    full month-by-month schedule up front, so nobody is caught off guard
    by the first review request.
    """
    company = _candidate_company(candidate)
    cycle_dates = cycle.cycle_dates()
    total_months = len(cycle_dates)
    schedule_html = _schedule_rows_html(cycle_dates)

    def send_to(recipient_name, recipient_email, role_line):
        _send(
            subject=_("Probation feedback cycle started for %(candidate)s")
            % {"candidate": candidate.name},
            content_html=PROBATION_CYCLE_STARTED_CONTENT
            % {
                "recipient": escape(recipient_name),
                "candidate": escape(candidate.name),
                "role_line": escape(role_line),
                "total_months": total_months,
                "plural": "" if total_months == 1 else "s",
                "schedule_rows": schedule_html,
            },
            to_email=recipient_email,
            company=company,
        )

    if candidate.email:
        send_to(
            candidate.name,
            candidate.email,
            _(
                " You'll be asked for a short self-assessment each month - "
                "we'll email you when each one is ready."
            ),
        )
    if reporting_manager:
        send_to(
            reporting_manager.get_full_name(),
            reporting_manager.get_mail(),
            _(" You're the reviewing Reporting Manager for every month of this cycle."),
        )
    if buddy:
        send_to(
            buddy.get_full_name(),
            buddy.get_mail(),
            _(" You've been added as their Buddy for the first month's review."),
        )
    if colleague:
        send_to(
            colleague.get_full_name(),
            colleague.get_mail(),
            _(" You've been added as a peer reviewer for the first month's review."),
        )
