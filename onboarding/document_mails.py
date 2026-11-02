"""
onboarding/document_mails.py

Built-in mails for a candidate's documents (no mail template or automation needed):

* the candidate is asked for a document          -> mail to the candidate, with the upload link
* the candidate's document is accepted           -> mail to the candidate
  (a rejection mail already exists: recruitment.views.notify_candidate_of_document_rejection)
* the candidate submits a document               -> mail to the employee who made the request

Each ``notify_*`` is meant to be run through base.mail_helpers.run_in_background; links that
need the request are worked out by the caller and passed in.
"""

import logging
from django.utils.html import escape
from django.utils.translation import gettext as _

from base.mail_helpers import (
    action_button,
    automation_sends,
    greeting,
    info_card,
    paragraph,
    send_branded_mail,
)

logger = logging.getLogger(__name__)

TAG = "Onboarding"
TEAM = "Talent Acquisition Team"


def _company(candidate):
    return getattr(getattr(candidate, "recruitment_id", None), "company_id", None)


def _requirements(doc_request):
    rows = [(_("Document"), doc_request.title)]
    if doc_request.is_mandatory:
        rows.append((_("Required"), _("Yes")))
    rows.append((_("Format"), doc_request.get_format_display()))
    if doc_request.max_size:
        rows.append((_("Maximum size"), f"{doc_request.max_size} MB"))
    if doc_request.description:
        rows.append((_("Note"), doc_request.description))
    return rows


def notify_candidate_document_requested(candidate, doc_request, upload_link=None):
    """
    Tell the candidate which document is wanted. There is no upload link in the mail: the
    candidate signs in to the Candidate Portal and uploads from its Documents section.
    ``upload_link`` is accepted and ignored for older callers.
    """
    if not candidate.email:
        return False
    if automation_sends("recruitment.models.CandidateDocumentRequest"):
        # a switched-on mail automation already sends this mail
        return False
    content = (
        greeting(candidate.name)
        + paragraph(escape(_("We need a document from you to continue with your onboarding.")))
        + info_card(_requirements(doc_request))
        + paragraph(
            escape(
                _(
                    "Please upload it at your earliest convenience: sign in to the Candidate "
                    "Portal with your registered email address and mobile number, then open "
                    "the Documents section."
                )
            )
        )
    )
    return send_branded_mail(
        _("Document requested: %(title)s") % {"title": doc_request.title},
        content,
        candidate.email,
        company=_company(candidate),
        tag_label=_(TAG),
        team_label=_(TEAM),
    )


def mail_newly_requested(instance, pk_set, reverse):
    """
    Called once the request is saved, from the request's many-to-many hook. ``instance`` is the
    request (and ``pk_set`` the candidates just added) - or, when added from the candidate's
    side (``reverse``), the candidate (and ``pk_set`` the requests).
    """
    from base.mail_helpers import run_in_background
    from recruitment.models import Candidate, CandidateDocumentRequest

    if reverse:
        pairs = [(instance, doc_request) for doc_request in CandidateDocumentRequest.objects.filter(pk__in=pk_set)]
    else:
        pairs = [(candidate, instance) for candidate in Candidate.objects.entire().filter(pk__in=pk_set)]
    for candidate, doc_request in pairs:
        if not candidate.email:
            continue
        run_in_background(notify_candidate_document_requested, candidate, doc_request)


def notify_candidate_document_approved(document):
    """Tell the candidate their document was accepted."""
    candidate = document.candidate_id
    if not candidate or not candidate.email:
        return False
    title = document.document_request_id.title if document.document_request_id_id else document.title
    content = (
        greeting(candidate.name)
        + paragraph(escape(_("Your document has been reviewed and accepted.")))
        + info_card([(_("Document"), title), (_("Status"), _("Accepted"))], accent="#22a05a")
        + paragraph(escape(_("Thank you. There is nothing more you need to do for this document.")), size=13, color="#888888")
    )
    return send_branded_mail(
        _("Document accepted: %(title)s") % {"title": title},
        content,
        candidate.email,
        company=_company(candidate),
        tag_label=_(TAG),
        team_label=_(TEAM),
    )


def request_creator_email(doc_request):
    """The email of the employee who made the request, or None."""
    user = getattr(doc_request, "created_by", None)
    employee = getattr(user, "employee_get", None) if user is not None else None
    if employee is None:
        return None, None
    return employee, employee.get_mail()


def notify_document_submitted(document, open_link):
    """Tell whoever made the request that the candidate has submitted the document."""
    if automation_sends("recruitment.models.CandidateDocument"):
        # a switched-on mail automation already sends this mail
        return False
    doc_request = document.document_request_id
    candidate = document.candidate_id
    if doc_request is None or candidate is None:
        return False
    employee, email = request_creator_email(doc_request)
    if not email:
        logger.info("Document %s submitted; the request has no creator with an email.", document.pk)
        return False
    content = (
        greeting(employee.get_full_name())
        + paragraph(
            escape(_("%(name)s has submitted a document you requested.") % {"name": candidate.name})
        )
        + info_card(
            [
                (_("Candidate"), candidate.name),
                (_("Document"), doc_request.title),
                (_("Status"), _("Waiting for your review")),
            ]
        )
        + action_button(open_link, _("Open Onboarding"))
    )
    return send_branded_mail(
        _("Document submitted: %(title)s by %(name)s") % {"title": doc_request.title, "name": candidate.name},
        content,
        email,
        company=_company(candidate),
        tag_label=_(TAG),
        team_label=_(TEAM),
    )
