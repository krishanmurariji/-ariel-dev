"""
horilla_documents/mails.py

Built-in mails for an employee's documents (no mail template or automation needed):

* HR asks employees for a document          -> mail to each employee asked
* an employee uploads the document          -> mail to the employee who made the request
* the document is accepted or rejected      -> mail to the employee, with the reason if rejected

Each ``notify_*`` is meant to be run through base.mail_helpers.run_in_background; links that
need the request are worked out by the caller and passed in.
"""

import logging

from django.utils.html import escape
from django.utils.translation import gettext as _

from base.mail_helpers import (
    action_button,
    greeting,
    info_card,
    paragraph,
    send_branded_mail,
)

logger = logging.getLogger(__name__)

TAG = "Documents"
TEAM = "HR Team"


def _company(employee):
    return employee.get_company() if hasattr(employee, "get_company") else None


def notify_documents_requested(doc_request, upload_link):
    """Mail every employee the request was made to."""
    sent = 0
    rows = [(_("Document"), doc_request.title), (_("Format"), doc_request.get_format_display())]
    if doc_request.max_size:
        rows.append((_("Maximum size"), f"{doc_request.max_size} MB"))
    if doc_request.description:
        rows.append((_("Note"), doc_request.description))
    for employee in doc_request.employee_id.all():
        email = employee.get_mail()
        if not email:
            continue
        content = (
            greeting(employee.get_full_name())
            + paragraph(escape(_("HR has asked you to provide a document.")))
            + info_card(rows)
            + paragraph(escape(_("Please upload it from the Documents tab of your profile.")))
            + action_button(upload_link, _("Open my profile"))
        )
        sent += bool(
            send_branded_mail(
                _("Document requested: %(title)s") % {"title": doc_request.title},
                content,
                email,
                company=_company(employee),
                tag_label=_(TAG),
                team_label=_(TEAM),
            )
        )
    return sent


def request_creator(doc_request):
    """(employee, email) of whoever made the request, or (None, None)."""
    user = getattr(doc_request, "created_by", None)
    employee = getattr(user, "employee_get", None) if user is not None else None
    if employee is None:
        return None, None
    return employee, employee.get_mail()


def notify_document_submitted(document, open_link):
    """Tell whoever made the request that the employee has uploaded the document."""
    doc_request = document.document_request_id
    employee = document.employee_id
    if doc_request is None or employee is None:
        return False
    creator, email = request_creator(doc_request)
    if not email:
        logger.info("Document %s uploaded; the request has no creator with an email.", document.pk)
        return False
    content = (
        greeting(creator.get_full_name())
        + paragraph(escape(_("%(name)s has uploaded a document you requested.") % {"name": employee.get_full_name()}))
        + info_card(
            [
                (_("Employee"), employee.get_full_name()),
                (_("Document"), doc_request.title),
                (_("Status"), _("Waiting for your review")),
            ]
        )
        + action_button(open_link, _("Review the document"))
    )
    return send_branded_mail(
        _("Document uploaded: %(title)s by %(name)s")
        % {"title": doc_request.title, "name": employee.get_full_name()},
        content,
        email,
        company=_company(creator),
        tag_label=_(TAG),
        team_label=_(TEAM),
    )


def notify_document_reviewed(document, approved):
    """Tell the employee their document was accepted, or rejected with the reason."""
    employee = document.employee_id
    email = employee.get_mail() if employee is not None else None
    if not email:
        return False
    title = document.document_request_id.title if document.document_request_id_id else document.title
    if approved:
        subject = _("Document accepted: %(title)s") % {"title": title}
        content = (
            greeting(employee.get_full_name())
            + paragraph(escape(_("Your document has been reviewed and accepted.")))
            + info_card([(_("Document"), title), (_("Status"), _("Accepted"))], accent="#22a05a")
        )
    else:
        subject = _("Action needed: %(title)s was not accepted") % {"title": title}
        content = (
            greeting(employee.get_full_name())
            + paragraph(escape(_("Your document was reviewed but could not be accepted.")))
            + info_card(
                [(_("Document"), title), (_("Reason"), document.reject_reason or _("Not specified"))],
                accent="#d93636",
            )
            + paragraph(escape(_("Please upload a corrected document from the Documents tab of your profile.")), size=13, color="#888888")
        )
    return send_branded_mail(
        subject, content, email, company=_company(employee), tag_label=_(TAG), team_label=_(TEAM)
    )
