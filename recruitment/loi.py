"""
loi.py

"Send LOI" (Letter of Intent) - a dedicated, single-purpose mail action
on a candidate's pipeline card: unlike the generic Send Mail flow (pick
any template from a dropdown), this always uses one fixed "Letter of
Intent" template and only lets the sender write their own message -
plus the public, no-login Accept/Decline links a candidate uses to
respond straight from the email, no portal account needed.

The "Letter of Intent" HorillaMailTemplate already contains
{{ loi_response_buttons }} in its body - Django's own template engine
fills that in with real, candidate-specific Accept/Decline buttons at
send time, the same way {{ instance.get_full_name }} etc. already work
in every other mail template in this app.
"""

import secrets

from django import template as django_template
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from base.mail_letterhead import attach_letterhead_images, render_letterhead_content
from base.models import HorillaMailTemplate
from horilla.decorators import hx_request_required, login_required, manager_can_enter
from horilla.http import HorillaRedirect
from recruitment.models import Candidate, CandidateLOIResponse

LOI_TEMPLATE_TITLE = "Letter of Intent"

LOI_TEMPLATE_BODY = """<p>Dear {{ instance.name }},</p>
<p>[Write your message here]</p>
{{ loi_response_buttons }}
<p>Regards,<br>{{ self.get_full_name }}</p>"""

ACCENT = "#0B5FCE"


def _get_loi_template():
    """
    Seeded once, then editable like any other Mail Template - Send LOI
    always uses whatever this template currently holds, so future
    wording changes don't need a code change.
    """
    loi_template, _created = HorillaMailTemplate.objects.get_or_create(
        title=LOI_TEMPLATE_TITLE, defaults={"body": LOI_TEMPLATE_BODY}
    )
    return loi_template


def _build_response_buttons_html(request, candidate):
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
    accept_url = request.build_absolute_uri(
        reverse("loi-response", kwargs={"token": loi_response.token, "action": "accept"})
    )
    decline_url = request.build_absolute_uri(
        reverse("loi-response", kwargs={"token": loi_response.token, "action": "decline"})
    )
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


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_candidate")
def send_loi_form(request, cand_id):
    """
    Renders the compose modal - Subject is fixed to the template's own
    title, Body is pre-filled with the Letter of Intent template so the
    sender only has to replace the "[Write your message here]" line.
    """
    candidate = get_object_or_404(Candidate, pk=cand_id)
    loi_template = _get_loi_template()
    return render(
        request,
        "pipeline/pipeline_components/send_loi.html",
        {"candidate": candidate, "loi_body": loi_template.body},
    )


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def send_loi(request, cand_id):
    """
    Sends the LOI. Unlike the generic Send Mail action, there's no
    template picker or attachments here by design - just the one fixed
    template and the sender's own message. Mirrors send_acknowledgement
    (recruitment/views/views.py) - the form closes itself on submit via
    hx-swap="none", so the response body here is never actually shown;
    only the queued message matters, same as the existing Send Mail flow.
    """
    candidate = get_object_or_404(Candidate, pk=cand_id)
    if request.method != "POST":
        return redirect("send-loi-form", cand_id=cand_id)

    body = request.POST.get("body", "")
    response_buttons_html = _build_response_buttons_html(request, candidate)

    template_bdy = django_template.Template(body)
    context = django_template.Context(
        {
            "instance": candidate,
            "self": request.user.employee_get,
            "request": request,
            "loi_response_buttons": mark_safe(response_buttons_html),
        }
    )
    render_bdy = template_bdy.render(context)
    letterhead_bdy, company, _has_logo = render_letterhead_content(
        render_bdy,
        tag_label=_("Recruitment"),
        team_label=_("Talent Acquisition Team"),
        company=getattr(candidate.recruitment_id, "company_id", None),
    )

    email = EmailMultiAlternatives(
        subject=LOI_TEMPLATE_TITLE,
        body=letterhead_bdy,
        to=[candidate.email],
    )
    email.attach_alternative(letterhead_bdy, "text/html")
    attach_letterhead_images(email, company)
    try:
        email.send()
        messages.success(request, _("Letter of Intent sent to candidate"))
    except Exception as e:
        messages.error(request, _("Something went wrong: %(error)s") % {"error": e})
    return HorillaRedirect(request)


def loi_response(request, token, action):
    """
    Public, no-login page a candidate lands on from the Accept/Decline
    button in their email - deliberately not @login_required, since a
    candidate answering an LOI has no portal account to log into.
    """
    loi_resp = get_object_or_404(CandidateLOIResponse, token=token)
    if loi_resp.status == "pending" and action in ("accept", "decline"):
        loi_resp.status = "accepted" if action == "accept" else "declined"
        loi_resp.responded_at = timezone.now()
        loi_resp.save(update_fields=["status", "responded_at"])
    return render(
        request,
        "pipeline/pipeline_components/loi_response_result.html",
        {"loi_response": loi_resp},
    )
