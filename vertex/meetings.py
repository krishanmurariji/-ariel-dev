"""
vertex/meetings.py

Meeting links for project calls, made by the same integration Recruitment uses for interviews: the
company's default meeting provider (Recruitment settings) decides between Google Meet and Microsoft
Teams, and the existing ``horilla_meet`` / ``outlook_auth`` adapters create the calendar event.

Like the interview feature, generating a link never fails the call itself: the call is saved first,
and if the provider is not connected or errors out, the reason is kept on the call and the user can
generate the link again with one click.

``generate_call_meeting`` returns one of:
    {"success": True, "created": bool}
    {"needs_auth": True, "message": str, "redirect": str | None}
    {"error": str}
"""

import logging

from django.apps import apps as django_apps
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _t

logger = logging.getLogger(__name__)

TEAMS = "teams"
GOOGLE = "google_meet"


def attendee_emails(call):
    return [mail.strip() for mail in call.attendees.split(",") if mail.strip()]


def _provider(request):
    from recruitment.models import RecruitmentGeneralSetting

    selected = request.session.get("selected_company") if request is not None else None
    company_id = None if not selected or selected == "all" else selected
    setting = RecruitmentGeneralSetting.objects.filter(company_id_id=company_id).first()
    return setting.default_meeting_provider if setting else GOOGLE


def _description(call):
    parts = [f"{_t('Project')}: {call.project.name}"]
    if call.agenda:
        parts.append(call.agenda)
    return "\n\n".join(parts)


def _via_teams(call, request, data):
    if not django_apps.is_installed("outlook_auth"):
        return {"error": _t("Teams integration is not installed.")}
    from outlook_auth.methods import get_azure_api
    from outlook_auth.models import OutlookMeeting

    api = get_azure_api(request)
    if not api or not api.token:
        return {
            "needs_auth": True,
            "message": _t("Outlook is not connected yet. Configure and authenticate the Outlook Mail integration first."),
            "redirect": None,
        }
    reuse = call.meeting_object_id if call.meeting_provider == TEAMS else None
    meeting, created = OutlookMeeting.objects.update_or_create(
        id=reuse, defaults={"employee_id": request.user.employee_get, **data}
    )
    meeting.refresh_from_db()
    return {"success": True, "created": created, "provider": TEAMS, "object_id": meeting.pk, "url": meeting.join_url}


def _via_google(call, request, data):
    if not django_apps.is_installed("horilla_meet"):
        return {"error": _t("Google Meet integration is not installed.")}
    from base.models import IntegrationApps
    from horilla_meet.models import GoogleCredential, GoogleMeeting

    if not IntegrationApps.objects.filter(app_label="horilla_meet", is_enabled=True).exists():
        return {"error": _t("Google Meet integration is disabled.")}
    if not GoogleCredential.objects.filter(employee_id=request.user.employee_get).exists():
        return {
            "needs_auth": True,
            "message": _t("Your Google account is not connected yet. Connect it, then generate the link again."),
            "redirect": reverse("authenticate-gmeet"),
        }
    reuse = call.meeting_object_id if call.meeting_provider == GOOGLE else None
    meeting, created = GoogleMeeting.objects.update_or_create(id=reuse, defaults=data)
    meeting.refresh_from_db()
    return {"success": True, "created": created, "provider": GOOGLE, "object_id": meeting.pk, "url": meeting.meet_url}


def _generate(obj, request, data, what):
    """Create (or update) the online meeting of ``obj`` (a call or a review meeting) and keep its link on it.

    ``obj`` needs meeting_provider, meeting_object_id, meeting_url and meeting_error. Never raises: a
    provider problem must never undo the saved record."""
    try:
        provider = _provider(request)
        result = (_via_teams if provider == TEAMS else _via_google)(obj, request, data)
        if result.get("success") and not result.get("url"):
            result = {"error": _t("The provider did not return a meeting link. Try again.")}
    except Exception as error:
        logger.exception("Vertex %s meeting failed for %s", what, obj.pk)
        result = {"error": str(error) or error.__class__.__name__}

    if result.get("success"):
        obj.meeting_provider = result["provider"]
        obj.meeting_object_id = result["object_id"]
        obj.meeting_url = result["url"]
        obj.meeting_error = ""
    else:
        obj.meeting_error = (result.get("error") or result.get("message") or "")[:300]
    obj.save(update_fields=["meeting_provider", "meeting_object_id", "meeting_url", "meeting_error"])
    return result


def _start(moment):
    # local wall-clock time, as Recruitment passes it: the providers add the time zone themselves
    return timezone.localtime(moment).replace(tzinfo=None)


def generate_call_meeting(call, request):
    """The meeting link of a project call."""
    data = {
        "title": call.title,
        "description": _description(call),
        "start_time": _start(call.scheduled_at),
        "duration": call.duration_minutes,
        "attendees": attendee_emails(call),
        "is_online": True,
    }
    return _generate(call, request, data, "call")


def generate_review_meeting(meeting, request):
    """The meeting link of a review: invites the person reviewed, the reviewer and the scheduler."""
    review = meeting.review_request
    lines = [
        f"{_t('Project')}: {review.project.name}",
        f"{_t('Review')}: {review.get_review_type_display()} - {review.subject.get_full_name()}",
    ]
    if meeting.description:
        lines.append(meeting.description)
    data = {
        "title": meeting.title,
        "description": (chr(10) * 2).join(lines),
        "start_time": _start(meeting.scheduled_at),
        "duration": meeting.duration_minutes,
        "attendees": attendee_emails(meeting),
        "is_online": True,
    }
    return _generate(meeting, request, data, "review")


def share_meeting(source, others):
    """Group reviews share ONE calendar event: copy its link to the other reviews' meetings."""
    for other in others:
        other.meeting_provider = source.meeting_provider
        other.meeting_object_id = source.meeting_object_id
        other.meeting_url = source.meeting_url
        other.meeting_error = source.meeting_error
        other.save(update_fields=["meeting_provider", "meeting_object_id", "meeting_url", "meeting_error"])


def report(request, obj, result, scheduled=False, what="call"):
    """Tell the user what happened, in plain words, without ever blocking the saved record."""
    from django.contrib import messages

    noun = _t(what)
    retry = _t("Use 'Generate link' on the %(what)s to try again.") % {"what": noun}
    if result.get("success"):
        messages.success(
            request,
            _t("Meeting link generated.") if result.get("created") else _t("Meeting updated and attendees notified."),
        )
        return
    reason = result.get("message") if result.get("needs_auth") else (result.get("error") or "")
    if scheduled:
        prefix = _t("Scheduled, but the meeting link could not be generated: ")
    else:
        prefix = "" if result.get("needs_auth") else _t("The meeting link could not be generated: ")
    messages.warning(request, f"{prefix}{reason} {retry}")
