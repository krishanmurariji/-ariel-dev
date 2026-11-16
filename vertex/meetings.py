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


def generate_call_meeting(call, request):
    """Create (or update) the online meeting of a call and keep its link on the call; never raises."""
    data = {
        "title": call.title,
        "description": _description(call),
        # local wall-clock time, as Recruitment passes it: the providers add the time zone themselves
        "start_time": timezone.localtime(call.scheduled_at).replace(tzinfo=None),
        "duration": call.duration_minutes,
        "attendees": attendee_emails(call),
        "is_online": True,
    }
    try:
        provider = _provider(request)
        result = (_via_teams if provider == TEAMS else _via_google)(call, request, data)
        if result.get("success") and not result.get("url"):
            result = {"error": _t("The provider did not return a meeting link. Try again.")}
    except Exception as error:  # a provider problem must never undo the saved call
        logger.exception("Vertex call meeting failed for call %s", call.pk)
        result = {"error": str(error) or error.__class__.__name__}

    if result.get("success"):
        call.meeting_provider = result["provider"]
        call.meeting_object_id = result["object_id"]
        call.meeting_url = result["url"]
        call.meeting_error = ""
    else:
        call.meeting_error = (result.get("error") or result.get("message") or "")[:300]
    call.save(update_fields=["meeting_provider", "meeting_object_id", "meeting_url", "meeting_error"])
    return result


def report(request, call, result, scheduled=False):
    """Tell the user what happened, in plain words, without ever blocking the saved call."""
    from django.contrib import messages

    if result.get("success"):
        messages.success(
            request,
            _t("Meeting link generated.") if result.get("created") else _t("Meeting updated and attendees notified."),
        )
    elif result.get("needs_auth"):
        prefix = _t("Call scheduled, but the meeting link could not be generated: ") if scheduled else ""
        messages.warning(request, prefix + result["message"] + " " + _t("Use 'Generate link' on the call to try again."))
    else:
        prefix = _t("Call scheduled, but the meeting link could not be generated: ") if scheduled else _t("The meeting link could not be generated: ")
        messages.warning(request, prefix + (result.get("error") or "") + " " + _t("Use 'Generate link' on the call to try again."))
