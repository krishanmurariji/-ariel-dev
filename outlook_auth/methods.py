"""
outlook_auth/methods.py
"""

from datetime import timedelta

from django.conf import settings


def sec_to_hm(seconds):
    """
    this method is used to formate seconds to H:M and return it
    args:
        seconds : seconds
    """

    hour = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int((seconds % 3600) % 60)
    return f"{hour:02d}:{minutes:02d}"


def get_azure_api(request):
    """
    Returns the AzureApi row that should be used for the currently
    selected company (falling back to whichever is marked primary),
    mirroring the lookup already used by outlook_login/send_outlook_email.
    """
    from outlook_auth.models import AzureApi

    selected_company = request.session.get("selected_company") if request else None
    if not selected_company or selected_company == "all":
        return AzureApi.objects.filter(is_primary=True).first()
    return AzureApi.objects.filter(company=selected_company).first()


def _teams_event_body(data):
    attendees = data["attendees"]
    return {
        "subject": data["title"],
        "body": {"contentType": "HTML", "content": data["description"]},
        "start": {
            "dateTime": data["start_time"].isoformat(),
            "timeZone": settings.TIME_ZONE,
        },
        "end": {
            "dateTime": (
                data["start_time"] + timedelta(minutes=data["duration"])
            ).isoformat(),
            "timeZone": settings.TIME_ZONE,
        },
        "attendees": [
            {"emailAddress": {"address": mail}, "type": "required"}
            for mail in attendees
        ],
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
    }


def create_teams_calendar_event(api, data):
    """
    Create an Outlook calendar event with a Teams meeting attached, via
    Microsoft Graph. Requires the Calendars.ReadWrite scope to have been
    consented to on `api`. Mirrors horilla_meet.methods.create_calendar_event.
    """
    from outlook_auth.views import get_valid_oauth_session

    oauth = get_valid_oauth_session(api)
    response = oauth.post(
        f"{api.outlook_api_endpoint}/me/events", json=_teams_event_body(data)
    )
    response.raise_for_status()
    return response.json()


def update_teams_calendar_event(api, data, event_id=None):
    """
    Updates an existing Teams calendar event. Mirrors
    horilla_meet.methods.update_calendar_event.
    """
    if not event_id:
        raise Exception("Cannot update event: event_id is missing.")

    from outlook_auth.views import get_valid_oauth_session

    oauth = get_valid_oauth_session(api)
    response = oauth.patch(
        f"{api.outlook_api_endpoint}/me/events/{event_id}",
        json=_teams_event_body(data),
    )
    response.raise_for_status()
    return response.json()
