"""
loi.py

"Send LOI" (Letter of Intent) - a dedicated, single-purpose mail action
on a candidate's pipeline card: unlike the generic Send Mail flow (pick
any template from a dropdown), this always sends the same fixed
greeting and only lets the sender write their own message - plus the
public, no-login Accept/Decline links a candidate uses to respond
straight from the email, no portal account needed.

The greeting and Accept/Decline buttons are composed here in Python
rather than as an editable HorillaMailTemplate - the sender never sees
mail-merge syntax like {{ instance.name }}, just a plain message box,
since most people sending an LOI aren't technical. There's no separate
"Regards, <sender>" signature - the shared letterhead footer (company
name + "Talent Acquisition Team") already signs every mail this app
sends, so one here too would just be a duplicate.
"""

import secrets

from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import gettext_lazy as _

from base.mail_letterhead import (
    attach_letterhead_images,
    get_gradient_bar_bytes,
    get_watermark_bytes,
    render_letterhead_content,
)
from base.models import Company
from horilla.decorators import hx_request_required, login_required, manager_can_enter
from horilla.http import HorillaRedirect
from recruitment.models import Candidate, CandidateLOIResponse

LOI_SUBJECT = "Letter of Intent"
LOI_TAG_LABEL = _("Recruitment")
LOI_TEAM_LABEL = _("Talent Acquisition Team")

ACCENT = "#0B5FCE"


def _sender_name(request):
    sender = getattr(request.user, "employee_get", None)
    return sender.get_full_name() if sender else request.user.get_full_name()


def _response_buttons_markup(accept_url, decline_url):
    return (
        f'<p style="margin:24px 0;">'
        f'<a href="{accept_url}" '
        f'style="background:{ACCENT};color:#ffffff;padding:10px 22px;border-radius:6px;'
        f'text-decoration:none;display:inline-block;margin-right:12px;font-weight:600;">'
        f"{_('Accept')}</a>"
        f'<a href="{decline_url}" '
        f'style="background:#ffffff;color:{ACCENT};padding:10px 22px;border:1px solid {ACCENT};'
        f'border-radius:6px;text-decoration:none;display:inline-block;font-weight:600;">'
        f"{_('Decline')}</a>"
        f"</p>"
    )


def _get_or_create_pending_response(candidate):
    """
    One pending response row per candidate is reused across resends
    (get_or_create on status="pending") so an earlier, unclicked email's
    link keeps working rather than silently going stale the moment a
    follow-up LOI goes out.
    """
    loi_response, _created = CandidateLOIResponse.objects.get_or_create(
        candidate_id=candidate,
        status="pending",
        defaults={"token": secrets.token_urlsafe(32)},
    )
    return loi_response


def _response_buttons_html_for(request, loi_response):
    accept_url = request.build_absolute_uri(
        reverse("loi-response", kwargs={"token": loi_response.token, "action": "accept"})
    )
    decline_url = request.build_absolute_uri(
        reverse("loi-response", kwargs={"token": loi_response.token, "action": "decline"})
    )
    return _response_buttons_markup(accept_url, decline_url)


def _compose_body_html(candidate, message_html, buttons_html):
    return (
        f"<p>{_('Dear')} {escape(candidate.name)},</p>"
        f"<p>{message_html}</p>"
        f"{buttons_html}"
    )


def _prepare_preview_html(html, company):
    """
    Substitutes the cid: image references a real mail client resolves
    from MIME attachments for plain browser-viewable URLs - a cid: only
    resolves inside an actual email client. Used for any browser-shown
    (non-email-client) rendering of a letterhead mail: the live compose
    preview and the frozen sent-mail snapshot alike.
    """
    preview_html = html.replace("cid:letterhead_gradient", reverse("loi-preview-gradient"))
    if company and company.icon:
        preview_html = preview_html.replace("cid:letterhead_logo", company.icon.url)
        preview_html = preview_html.replace(
            "cid:letterhead_watermark",
            reverse("loi-preview-watermark", kwargs={"company_id": company.pk}),
        )
    return preview_html


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_candidate")
def send_loi_form(request, cand_id):
    """
    Renders the compose modal - the greeting is shown as plain,
    already-resolved text; the sender only ever types their own message
    in a plain textbox. The right-hand side live-previews the actual
    letterhead mail via loi_preview below.
    """
    candidate = get_object_or_404(Candidate, pk=cand_id)
    return render(
        request,
        "pipeline/pipeline_components/send_loi.html",
        {"candidate": candidate},
    )


def _loi_company(candidate):
    return getattr(candidate.recruitment_id, "company_id", None)


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def send_loi(request, cand_id):
    """
    Sends the LOI. Unlike the generic Send Mail action, there's no
    template picker or attachments here by design - just a fixed
    greeting and the sender's own message. Mirrors send_acknowledgement
    (recruitment/views/views.py) - the form closes itself on submit via
    hx-swap="none", so the response body here is never actually shown;
    only the queued message matters, same as the existing Send Mail flow.
    """
    candidate = get_object_or_404(Candidate, pk=cand_id)
    if request.method != "POST":
        return redirect("send-loi-form", cand_id=cand_id)

    message = request.POST.get("message", "").strip()
    message_html = escape(message).replace("\n", "<br>")
    loi_response_row = _get_or_create_pending_response(candidate)
    buttons_html = _response_buttons_html_for(request, loi_response_row)

    render_bdy = _compose_body_html(candidate, message_html, buttons_html)
    letterhead_bdy, company, _has_logo = render_letterhead_content(
        render_bdy,
        tag_label=LOI_TAG_LABEL,
        team_label=LOI_TEAM_LABEL,
        company=_loi_company(candidate),
    )

    email = EmailMultiAlternatives(
        subject=LOI_SUBJECT,
        body=letterhead_bdy,
        to=[candidate.email],
    )
    email.attach_alternative(letterhead_bdy, "text/html")
    attach_letterhead_images(email, company)
    try:
        email.send()
        loi_response_row.message = message
        loi_response_row.rendered_body = letterhead_bdy
        loi_response_row.company = company
        loi_response_row.last_sent_at = timezone.now()
        loi_response_row.sent_by = getattr(request.user, "employee_get", None)
        loi_response_row.save(
            update_fields=[
                "message",
                "rendered_body",
                "company",
                "last_sent_at",
                "sent_by",
            ]
        )
        Candidate.objects.filter(pk=candidate.pk).update(offer_letter_status="sent")
        messages.success(request, _("Letter of Intent sent to candidate"))
    except Exception as e:
        messages.error(request, _("Something went wrong: %(error)s") % {"error": e})
    return HorillaRedirect(request)


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def loi_preview(request, cand_id):
    """
    Renders the exact letterhead mail the candidate would receive, for
    the live preview panel on the compose modal - same content/markup
    as send_loi above, but with placeholder (non-functional) response
    links instead of real ones, so previewing never creates a real
    CandidateLOIResponse row, and with the mail's cid: image
    attachments swapped for plain browser-viewable URLs (a cid: only
    resolves inside an actual email client).
    """
    candidate = get_object_or_404(Candidate, pk=cand_id)
    message = request.POST.get("message", "").strip()
    if message:
        message_html = escape(message).replace("\n", "<br>")
    else:
        placeholder = _("Write your message to the candidate here...")
        message_html = f'<span style="color:#aaaaaa;">{escape(placeholder)}</span>'

    buttons_html = _response_buttons_markup("javascript:void(0)", "javascript:void(0)")
    render_bdy = _compose_body_html(candidate, message_html, buttons_html)
    letterhead_bdy, company, _has_logo = render_letterhead_content(
        render_bdy,
        tag_label=LOI_TAG_LABEL,
        team_label=LOI_TEAM_LABEL,
        company=_loi_company(candidate),
    )
    return HttpResponse(_prepare_preview_html(letterhead_bdy, company))


@login_required
def loi_preview_gradient(request):
    """Browser-viewable copy of the letterhead's header/footer gradient bar - see loi_preview."""
    return HttpResponse(get_gradient_bar_bytes() or b"", content_type="image/png")


@login_required
def loi_preview_watermark(request, company_id):
    """Browser-viewable copy of a company's letterhead watermark - see loi_preview."""
    company = get_object_or_404(Company, pk=company_id)
    return HttpResponse(get_watermark_bytes(company) or b"", content_type="image/png")


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def loi_sent_preview(request, response_id):
    """
    Read-only playback of exactly what was emailed for one LOI send - the
    frozen rendered_body snapshot saved by send_loi above, with cid:
    image refs substituted the same way as the live compose preview.
    Used by the onboarding "Offer, Hiring & Closure" step to show HR the
    exact last-sent LOI.
    """
    loi_resp = get_object_or_404(CandidateLOIResponse, pk=response_id)
    return HttpResponse(_prepare_preview_html(loi_resp.rendered_body, loi_resp.company))


def _notify_sender_of_response(request, loi_resp):
    """
    Tells whoever clicked Send that the candidate has responded - best
    effort, never blocks the candidate's own response page on a mail
    failure (they've already been recorded as accepted/declined by the
    time this runs).
    """
    sender = loi_resp.sent_by
    if not sender:
        return
    to_email = sender.get_mail() if hasattr(sender, "get_mail") else None
    if not to_email:
        return

    candidate = loi_resp.candidate_id
    accepted = loi_resp.status == "accepted"
    verb = _("accepted") if accepted else _("declined")
    profile_url = request.build_absolute_uri(candidate.get_profile_url())
    body_html = (
        f"<p>{_('Hi')} {escape(sender.get_full_name())},</p>"
        f"<p>{escape(candidate.name)} has just <b>{verb}</b> the Letter of "
        f"Intent you sent on {loi_resp.last_sent_at or loi_resp.sent_at}.</p>"
        f'<p><a href="{profile_url}" '
        f'style="background:{ACCENT};color:#ffffff;padding:10px 18px;'
        f'border-radius:6px;text-decoration:none;display:inline-block;">'
        f"{_('View Candidate')}</a></p>"
    )
    html_body, company, _has_logo = render_letterhead_content(
        body_html,
        tag_label=LOI_TAG_LABEL,
        team_label=LOI_TEAM_LABEL,
        company=_loi_company(candidate),
    )
    subject = (
        _("Letter of Intent Accepted: %(name)s")
        if accepted
        else _("Letter of Intent Declined: %(name)s")
    ) % {"name": candidate.name}
    email = EmailMultiAlternatives(subject=subject, body=html_body, to=[to_email])
    email.attach_alternative(html_body, "text/html")
    attach_letterhead_images(email, company)
    try:
        email.send()
    except Exception:
        pass


def loi_response(request, token, action):
    """
    Public, no-login page a candidate lands on from the Accept/Decline
    button in their email - deliberately not @login_required, since a
    candidate answering an LOI has no portal account to log into.

    A response is recorded at most once - status only ever moves out of
    "pending", never between "accepted" and "declined" or back to
    "pending". Locked with select_for_update so two near-simultaneous
    clicks (e.g. a mail client prefetching both links) can't both slip
    through the "still pending" check and double-fire the sender
    notification. Once responded, re-clicking either link (the same one
    again, or the other one from the same mail) just redisplays the
    original recorded response - it never gets overwritten. Sending a
    fresh LOI after a decline creates a new token/row, so only that new,
    genuinely pending link can be responded to again.
    """
    with transaction.atomic():
        loi_resp = get_object_or_404(
            CandidateLOIResponse.objects.select_for_update(), token=token
        )
        if loi_resp.status == "pending" and action in ("accept", "decline"):
            loi_resp.status = "accepted" if action == "accept" else "declined"
            loi_resp.responded_at = timezone.now()
            loi_resp.save(update_fields=["status", "responded_at"])
            Candidate.objects.filter(pk=loi_resp.candidate_id_id).update(
                offer_letter_status="accepted" if action == "accept" else "rejected"
            )
            just_responded = True
        else:
            just_responded = False

    if just_responded:
        _notify_sender_of_response(request, loi_resp)

    return render(
        request,
        "pipeline/pipeline_components/loi_response_result.html",
        {"loi_response": loi_resp},
    )
