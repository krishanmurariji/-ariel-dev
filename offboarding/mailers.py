"""
offboarding/mailers.py

Emails sent by the exit process. For now: telling an employee's reporting
manager that they have submitted their resignation.
"""

import logging

from django.utils.html import escape
from django.utils.translation import gettext as _

from base.backends import ConfiguredEmailBackend
from base.mail_letterhead import build_letterhead_email

logger = logging.getLogger(__name__)

_DATE_FORMAT = "%d %b %Y"


def _row(label, value_html):
    return (
        '<tr><td style="padding:10px 16px 10px 0;font-size:13px;color:#777777;'
        'vertical-align:top;white-space:nowrap;">%s</td>'
        '<td style="padding:10px 0;font-size:14px;color:#2d2d2d;vertical-align:top;'
        'line-height:1.6;">%s</td></tr>' % (escape(label), value_html)
    )


def _text_block(text):
    # Plain <br> line breaks rather than <p> paragraphs - a paragraph's own
    # margin pushes the text out of line with its label in the details table.
    return escape(text).replace("\r\n", "\n").replace("\n", "<br>")


def resignation_submitted_content(letter, manager):
    """The HTML body of the mail (letterhead wrapping is added when sending)."""
    employee = letter.employee_id
    position = " / ".join(
        str(part)
        for part in (employee.get_job_position(), employee.get_department())
        if part
    )
    rows = [
        _row(
            _("Employee"),
            "<strong>%s</strong>%s"
            % (
                escape(employee.get_full_name()),
                " (%s)" % escape(employee.badge_id) if employee.badge_id else "",
            ),
        ),
    ]
    if position:
        rows.append(_row(_("Position"), escape(position)))
    rows.append(_row(_("Reason for leaving"), escape(letter.get_reason_col())))

    if letter.discussed_with_manager is not None:
        discussed = (
            _("Yes - they have discussed this with you")
            if letter.discussed_with_manager
            else _("No - they have not discussed this with you yet")
        )
        rows.append(_row(_("Discussed with you"), escape(discussed)))
    if letter.discussion_summary:
        rows.append(
            _row(_("Summary of discussion"), _text_block(letter.discussion_summary))
        )
    if letter.notice_period_days and letter.notice_period_ends:
        rows.append(
            _row(
                _("Notice period"),
                escape(
                    _("%(days)s days, ending %(date)s")
                    % {
                        "days": letter.notice_period_days,
                        "date": letter.notice_period_ends.strftime(_DATE_FORMAT),
                    }
                ),
            )
        )
    if letter.preferred_last_working_day:
        rows.append(
            _row(
                _("Preferred last working day"),
                "%s<br><span style=\"font-size:12px;color:#888888;\">%s</span>"
                % (
                    escape(letter.preferred_last_working_day.strftime(_DATE_FORMAT)),
                    escape(_("Subject to mutual agreement with the employer")),
                ),
            )
        )
    rows.append(
        _row(_("Submitted on"), escape(letter.created_at.strftime(_DATE_FORMAT)))
        if letter.created_at
        else ""
    )

    comment_block = ""
    if letter.additional_comment:
        comment_block = (
            '<p style="margin:0 0 6px;font-size:13px;color:#777777;">%s</p>'
            '<div style="margin:0 0 24px;padding:14px 16px;background:#fafafa;'
            'border:1px solid #ececec;border-radius:6px;font-size:14px;'
            'color:#2d2d2d;line-height:1.6;">%s</div>'
            % (escape(_("Additional comment")), _text_block(letter.additional_comment))
        )

    return (
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">%(hi)s '
        "<strong>%(manager)s</strong>,</p>"
        '<p style="margin:0 0 20px;font-size:14px;color:#444444;line-height:1.7;">'
        "%(intro)s</p>"
        '<div style="margin:0 0 24px;padding:8px 20px;background:#f7f9fc;'
        'border-left:4px solid #3b6fd4;border-radius:8px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">'
        "%(rows)s</table></div>"
        "%(comment)s"
        '<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">'
        "%(next)s</p>"
        '<p style="margin:0;font-size:13px;color:#888888;">%(footer)s</p>'
    ) % {
        "hi": escape(_("Hi")),
        "manager": escape(manager.get_full_name()),
        "intro": escape(
            _(
                "%(employee)s from your team has submitted their resignation "
                "through the HR portal. A summary of the request is below."
            )
            % {"employee": employee.get_full_name()}
        ),
        "rows": "".join(rows),
        "comment": comment_block,
        "next": escape(
            _(
                "Please connect with %(first_name)s at the earliest to discuss "
                "this. HR will follow up with the next steps of the exit "
                "process once the request has been reviewed."
            )
            % {"first_name": employee.employee_first_name}
        ),
        "footer": escape(
            _("This is an automated message from the HR portal.")
        ),
    }


def _plain_text(letter, manager):
    employee = letter.employee_id
    lines = [
        _("Hi %(manager)s,") % {"manager": manager.get_full_name()},
        "",
        _("%(employee)s has submitted their resignation.")
        % {"employee": employee.get_full_name()},
        _("Reason: %(reason)s") % {"reason": letter.get_reason_col()},
    ]
    if letter.notice_period_ends:
        lines.append(
            _("Notice period ends: %(date)s")
            % {"date": letter.notice_period_ends.strftime(_DATE_FORMAT)}
        )
    if letter.preferred_last_working_day:
        lines.append(
            _("Preferred last working day: %(date)s (subject to mutual agreement)")
            % {"date": letter.preferred_last_working_day.strftime(_DATE_FORMAT)}
        )
    lines += ["", _("HR will follow up with the next steps.")]
    return "\n".join(str(line) for line in lines)


def notify_manager_of_resignation(letter):
    """
    Email the employee's reporting manager that `letter` was submitted.
    Returns True if a mail went out. Never raises - the resignation itself
    has already been saved by the time this runs, so a mail problem must not
    turn into an error for the employee.
    """
    employee = letter.employee_id
    manager = employee.get_reporting_manager()
    to_email = manager.get_mail() if manager else None
    if not to_email:
        logger.info(
            "Resignation of employee %s submitted; no reporting manager email "
            "on record, so no mail was sent.",
            employee.pk,
        )
        return False
    try:
        display_email_name = ConfiguredEmailBackend().dynamic_from_email_with_display_name
        employee_mail = employee.get_mail()
        message = build_letterhead_email(
            subject=_("Resignation submitted: %(employee)s")
            % {"employee": employee.get_full_name()},
            content_html=resignation_submitted_content(letter, manager),
            to=to_email,
            tag_label=_("Offboarding"),
            team_label=_("HR Team"),
            employee=employee,
            from_email=display_email_name,
            reply_to=[employee_mail] if employee_mail else None,
            plain_text=_plain_text(letter, manager),
        )
        return bool(message.send())
    except Exception as exc:
        logger.error(
            "Resignation mail for employee %s not sent to %s: %s",
            employee.pk,
            to_email,
            exc,
        )
        return False
