"""
offboarding/mailers.py

Emails sent by the exit process. For now: telling an employee's reporting
manager that they have submitted their resignation.
"""

import logging
from datetime import date, datetime

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


def _greeting_name(recipient):
    """Who the mail is addressed to: the recipient's own name."""
    return recipient.get_full_name()


def _requested_on(letter):
    return (letter.created_at or datetime.now()).strftime(_DATE_FORMAT)


def resignation_submitted_content(letter, manager):
    """
    The HTML body of the mail (letterhead wrapping is added when sending).
    `manager` is whoever is receiving it: the reporting manager, or a person
    the employee chose to notify - they all get the same mail.
    """
    employee = letter.employee_id
    lines = [
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">%s <strong>%s</strong>,</p>'
        % (escape(_("Hi")), escape(_greeting_name(manager))),
        '<p style="margin:0 0 20px;font-size:14px;color:#2d2d2d;line-height:1.7;">'
        "<strong>%s</strong> %s</p>"
        % (escape(employee.get_full_name()), escape(_("has requested for resignation"))),
        '<p style="margin:0;font-size:14px;color:#2d2d2d;line-height:1.9;">'
        "%s : %s<br>%s : %s%s</p>"
        % (
            escape(_("Requested Date")),
            escape(_requested_on(letter)),
            escape(_("Reason")),
            escape(letter.get_reason_col()),
            "<br>%s : %s"
            % (escape(_("Note")), _text_block(letter.additional_comment))
            if letter.additional_comment
            else "",
        ),
    ]
    return "".join(lines)


def _plain_text(letter, manager):
    employee = letter.employee_id
    lines = [
        _("Hi %(name)s,") % {"name": _greeting_name(manager)},
        "",
        _("%(employee)s has requested for resignation")
        % {"employee": employee.get_full_name()},
        "",
        _("Requested Date : %(date)s") % {"date": _requested_on(letter)},
        _("Reason : %(reason)s") % {"reason": letter.get_reason_col()},
    ]
    if letter.additional_comment:
        lines.append(_("Note : %(note)s") % {"note": letter.additional_comment})
    return "\n".join(str(line) for line in lines)


def notify_manager_of_resignation(letter):
    """
    Email the employee's reporting manager - and the second manager, when they have
    at least view access to resignations - and anyone the employee chose to
    notify, that `letter` was submitted. Everyone gets the same mail.
    Returns True if a mail went out. Never raises - the resignation itself
    has already been saved by the time this runs, so a mail problem must not
    turn into an error for the employee.
    """
    from base.mail_helpers import automation_sends
    from horilla.access import VIEW, managers_for

    if automation_sends("offboarding.models.ResignationLetter"):
        # a switched-on mail automation already sends this mail
        return False
    sent = False
    recipients = list(managers_for(letter.employee_id, "resignation", VIEW))
    chosen = list(letter.notify_employees.all()) if letter.pk else []
    for person in chosen:
        if all(person.pk != known.pk for known in recipients):
            recipients.append(person)
    for recipient in recipients:
        sent = _notify_one_manager(letter, recipient) or sent
    if not recipients:
        logger.info(
            "Resignation of employee %s submitted; no reporting manager or chosen "
            "person on record, so no mail was sent.",
            letter.employee_id.pk,
        )
    return sent


def _notify_one_manager(letter, manager):
    employee = letter.employee_id
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


# --------------------------------------------------------------------------
# an exit initiated for an employee (by HR or a manager)
# --------------------------------------------------------------------------


def _exit_status_text(letter):
    return {
        "requested": _("Submitted for review"),
        "approved": _("Accepted - the exit process has started"),
    }.get(letter.status, letter.get_status_display())


def _detail_lines(rows):
    """Plain 'Label : value' lines in the same look as the resignation mail."""
    return "<br>".join("%s : %s" % (escape(label), value_html) for label, value_html in rows)


def exit_initiated_content(letter, recipient, initiator, for_employee):
    """
    HTML body of the mail sent when an exit is initiated *for* an employee.
    The employee gets the facts that concern them; managers also see the
    reason. The discussion summary and rehire notes stay internal.
    """
    employee = letter.employee_id
    kind = letter.get_exit_type_display().lower()
    who = initiator.get_full_name() if initiator else _("HR")
    rows = [
        (_("Exit type"), escape(letter.get_exit_type_display())),
    ]
    if letter.notice_date:
        rows.append((_("Notice date"), escape(letter.notice_date.strftime(_DATE_FORMAT))))
    if letter.planned_to_leave_on:
        rows.append(
            (_("Last working day"), escape(letter.planned_to_leave_on.strftime(_DATE_FORMAT)))
        )
    if not for_employee:
        rows.append((_("Reason"), escape(letter.get_reason_col())))
        rows.append((_("Status"), escape(_exit_status_text(letter))))

    if for_employee:
        intro = _("%(who)s has initiated your %(kind)s process.") % {"who": who, "kind": kind}
        closing = _("HR will be in touch with the next steps.")
    else:
        intro = _("%(who)s has initiated the %(kind)s of %(employee)s.") % {
            "who": who,
            "kind": kind,
            "employee": employee.get_full_name(),
        }
        closing = _("Please open Exit Process in the HR portal for the details.")
    return (
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">%(hi)s <strong>%(name)s</strong>,</p>'
        '<p style="margin:0 0 20px;font-size:14px;color:#2d2d2d;line-height:1.7;">%(intro)s</p>'
        '<p style="margin:0 0 20px;font-size:14px;color:#2d2d2d;line-height:1.9;">%(rows)s</p>'
        '<p style="margin:0;font-size:14px;color:#444444;line-height:1.7;">%(closing)s</p>'
    ) % {
        "hi": escape(_("Hi")),
        "name": escape(recipient.get_full_name()),
        "intro": escape(intro),
        "rows": _detail_lines(rows),
        "closing": escape(closing),
    }


def _exit_initiated_plain(letter, recipient, initiator, for_employee):
    employee = letter.employee_id
    kind = letter.get_exit_type_display().lower()
    who = initiator.get_full_name() if initiator else _("HR")
    lines = [_("Hi %(name)s,") % {"name": recipient.get_full_name()}, ""]
    if for_employee:
        lines.append(_("%(who)s has initiated your %(kind)s process.") % {"who": who, "kind": kind})
    else:
        lines.append(
            _("%(who)s has initiated the %(kind)s of %(employee)s.")
            % {"who": who, "kind": kind, "employee": employee.get_full_name()}
        )
    lines += ["", _("Exit type : %(v)s") % {"v": letter.get_exit_type_display()}]
    if letter.notice_date:
        lines.append(_("Notice date : %(v)s") % {"v": letter.notice_date.strftime(_DATE_FORMAT)})
    if letter.planned_to_leave_on:
        lines.append(
            _("Last working day : %(v)s") % {"v": letter.planned_to_leave_on.strftime(_DATE_FORMAT)}
        )
    if not for_employee:
        lines.append(_("Reason : %(v)s") % {"v": letter.get_reason_col()})
        lines.append(_("Status : %(v)s") % {"v": _exit_status_text(letter)})
    lines += [
        "",
        _("HR will be in touch with the next steps.")
        if for_employee
        else _("Please open Exit Process in the HR portal for the details."),
    ]
    return "\n".join(str(line) for line in lines)


def exit_initiated_recipients(letter, initiator):
    """
    Who is told when an exit is initiated: the employee, their reporting manager,
    any second managers with view access, and the manager of manager (when that
    is switched on in Settings > Approvals). The person who initiated it already
    knows, so they are left out. Each person appears once.
    """
    from horilla.access import VIEW, managers_for

    employee = letter.employee_id
    people = [(employee, True)]
    for manager in managers_for(employee, "resignation", VIEW):
        people.append((manager, False))
    seen = set()
    result = []
    for person, for_employee in people:
        if person is None or person.pk in seen:
            continue
        seen.add(person.pk)
        if initiator is not None and person.pk == initiator.pk:
            continue
        result.append((person, for_employee))
    return result


def notify_exit_initiated(letter, initiator):
    """
    Email everyone affected that an exit was initiated for ``letter.employee_id``.
    Returns how many mails went out. Never raises - the exit is already saved,
    so a mail problem must not turn into an error for whoever clicked.
    """
    from base.mail_helpers import automation_sends

    if automation_sends("offboarding.models.OffboardingEmployee"):
        # a switched-on mail automation already sends this mail
        return 0
    sent = 0
    try:
        recipients = exit_initiated_recipients(letter, initiator)
    except Exception as exc:
        logger.error("Could not work out who to tell about exit %s: %s", letter.pk, exc)
        return 0
    for person, for_employee in recipients:
        to_email = person.get_mail()
        if not to_email:
            logger.info("No email on record for %s; exit mail skipped.", person.pk)
            continue
        employee = letter.employee_id
        try:
            subject = (
                _("Your %(kind)s process has been initiated")
                % {"kind": letter.get_exit_type_display().lower()}
                if for_employee
                else _("Exit initiated: %(employee)s") % {"employee": employee.get_full_name()}
            )
            message = build_letterhead_email(
                subject=subject,
                content_html=exit_initiated_content(letter, person, initiator, for_employee),
                to=to_email,
                tag_label=_("Offboarding"),
                team_label=_("HR Team"),
                employee=employee,
                from_email=ConfiguredEmailBackend().dynamic_from_email_with_display_name,
                reply_to=None,
                plain_text=_exit_initiated_plain(letter, person, initiator, for_employee),
            )
            sent += 1 if message.send() else 0
        except Exception as exc:
            logger.error(
                "Exit-initiated mail for employee %s not sent to %s: %s", employee.pk, to_email, exc
            )
    return sent


def review_reminder_content(letter, reviewer, sender):
    employee = letter.employee_id
    waiting = (date.today() - letter.created_at.date()).days if letter.created_at else 0
    rows = [
        _row(_("Employee"), "<strong>%s</strong>" % escape(employee.get_full_name())),
        _row(_("Type"), escape(letter.get_exit_type_display())),
        _row(_("Reason"), escape(letter.get_reason_col())),
        _row(_("Requested last day"), escape(letter.planned_to_leave_on.strftime(_DATE_FORMAT))),
        _row(_("Waiting for"), escape(_("%(days)s day(s)") % {"days": waiting})),
    ]
    if sender is not None:
        rows.append(_row(_("Reminder sent by"), escape(sender.get_full_name())))
    return (
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">%(hi)s '
        "<strong>%(name)s</strong>,</p>"
        '<p style="margin:0 0 20px;font-size:14px;color:#444444;line-height:1.7;">%(intro)s</p>'
        '<div style="margin:0 0 24px;padding:8px 20px;background:#f7f9fc;'
        'border-left:4px solid #3b6fd4;border-radius:8px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">%(rows)s</table></div>'
        '<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">%(next)s</p>'
        '<p style="margin:0;font-size:13px;color:#888888;">%(footer)s</p>'
    ) % {
        "hi": escape(_("Hi")),
        "name": escape(reviewer.get_full_name()),
        "intro": escape(
            _("%(employee)s's %(kind)s is still waiting for your review.")
            % {"employee": employee.get_full_name(), "kind": letter.get_exit_type_display().lower()}
        ),
        "rows": "".join(rows),
        "next": escape(_("Please open Exit Process in the HR portal and accept, reject or retain it.")),
        "footer": escape(_("This is an automated message from the HR portal.")),
    }


def remind_reviewer(letter, reviewer, sender=None):
    """
    Email the person who has to review `letter`. Returns True if a mail went
    out. Never raises: the in-app reminder has already been sent.
    """
    to_email = reviewer.get_mail() if reviewer else None
    if not to_email:
        logger.info("No email on record for the reviewer of request %s.", letter.pk)
        return False
    try:
        employee = letter.employee_id
        message = build_letterhead_email(
            subject=_("Reminder: %(employee)s's %(kind)s is waiting for your review")
            % {"employee": employee.get_full_name(), "kind": letter.get_exit_type_display().lower()},
            content_html=review_reminder_content(letter, reviewer, sender),
            to=to_email,
            tag_label=_("Offboarding"),
            team_label=_("HR Team"),
            employee=employee,
            from_email=ConfiguredEmailBackend().dynamic_from_email_with_display_name,
            reply_to=None,
            plain_text=_("%(employee)s's %(kind)s is waiting for your review. Please open Exit Process in the HR portal.")
            % {"employee": employee.get_full_name(), "kind": letter.get_exit_type_display().lower()},
        )
        return bool(message.send())
    except Exception as exc:
        logger.error("Reminder mail for request %s not sent to %s: %s", letter.pk, to_email, exc)
        return False


def exit_survey_content(case, survey, reminder):
    employee = case.employee_id
    rows = []
    if case.last_working_day:
        rows.append(_row(_("Last working day"), escape(case.last_working_day.strftime(_DATE_FORMAT))))
    rows.append(_row(_("Questions"), escape(str(len(survey.questions)))))
    rows.append(_row(_("Where to find it"), escape(_("HR portal, Offboarding, My Exit Survey"))))
    intro = (
        _("This is a reminder to fill in your exit survey. It only takes a few minutes.")
        if reminder
        else _("Please take a few minutes to fill in your exit survey. Your honest feedback helps us improve.")
    )
    return (
        '<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">%(hi)s '
        "<strong>%(name)s</strong>,</p>"
        '<p style="margin:0 0 20px;font-size:14px;color:#444444;line-height:1.7;">%(intro)s</p>'
        '<div style="margin:0 0 24px;padding:8px 20px;background:#f7f9fc;'
        'border-left:4px solid #3b6fd4;border-radius:8px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">%(rows)s</table></div>'
        '<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">%(next)s</p>'
        '<p style="margin:0;font-size:13px;color:#888888;">%(footer)s</p>'
    ) % {
        "hi": escape(_("Hi")),
        "name": escape(employee.get_full_name()),
        "intro": escape(intro),
        "rows": "".join(rows),
        "next": escape(_("Only HR can read your answers.")),
        "footer": escape(_("This is an automated message from the HR portal.")),
    }


def send_exit_survey_mail(case, survey, reminder=False):
    """
    Email the leaver that their exit survey is waiting. Returns True if a
    mail went out. Never raises: the in-app notification is already sent.
    """
    employee = case.employee_id
    to_email = employee.get_mail()
    if not to_email:
        logger.info("No email on record for employee %s; exit survey mail not sent.", employee.pk)
        return False
    try:
        subject = (
            _("Reminder: please fill in your exit survey")
            if reminder
            else _("Please fill in your exit survey")
        )
        message = build_letterhead_email(
            subject=subject,
            content_html=exit_survey_content(case, survey, reminder),
            to=to_email,
            tag_label=_("Offboarding"),
            team_label=_("HR Team"),
            employee=employee,
            from_email=ConfiguredEmailBackend().dynamic_from_email_with_display_name,
            reply_to=None,
            plain_text=_("Please fill in your exit survey. You will find it in the HR portal under Offboarding, My Exit Survey."),
        )
        return bool(message.send())
    except Exception as exc:
        logger.error("Exit survey mail for employee %s not sent to %s: %s", employee.pk, to_email, exc)
        return False
