import uuid
from datetime import timedelta

from django.conf import settings
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from horilla_meet.models import GoogleCloudCredential, GoogleCredential


def cloud_credential_for(request):
    """
    The Google Cloud credential (client id / secret / redirect addresses) that this user's
    "sign in with Google" should use.

    Their own company's credential comes first, then one that is not tied to any company.
    An account that belongs to no company - the admin account usually - uses the credential
    when there is exactly one in the system, so it is not "not found" just because the
    credential was saved against a company. Another company's credential is never used
    for someone who does belong to a company.
    """
    credentials = GoogleCloudCredential.objects.entire()
    company = request.user.employee_get.get_company()
    if company is not None:
        return (
            credentials.filter(company_id=company).first()
            or credentials.filter(company_id__isnull=True).first()
        )
    shared = credentials.filter(company_id__isnull=True).first()
    if shared is not None:
        return shared
    only = list(credentials[:2])
    return only[0] if len(only) == 1 else None


def get_google_credentials(request):
    """Fetches and refreshes Google credentials for the logged-in user."""

    try:
        creds = GoogleCredential.objects.get(employee_id=request.user.employee_get)
    except GoogleCredential.DoesNotExist:
        raise Exception("Google credentials not found for the user.")

    credentials = Credentials(
        token=creds.token,
        refresh_token=creds.refresh_token,
        token_uri=creds.token_uri,
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        scopes=creds.scopes.split(","),
    )

    if credentials.expired:
        try:
            credentials.refresh(Request())
            creds.token = credentials.token
            creds.save()
        except Exception as e:
            raise Exception(f"Failed to refresh Google credentials: {e}")

    return credentials


def create_calendar_event(request, data):
    """Create a Google Calendar event from a given data."""

    credentials = get_google_credentials(request)

    service = build("calendar", "v3", credentials=credentials)
    attendees = data["attendees"]

    event = {
        "summary": data["title"],
        "description": data["description"],
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
        "attendees": [{"email": mail} for mail in attendees],
    }
    if data.get("is_online", True):
        event["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    try:
        created_event = (
            service.events()
            .insert(
                calendarId="primary",
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all",
                sendNotifications=True,
            )
            .execute()
        )
    except Exception as e:
        raise Exception(f"Failed to create calendar event: {e}")

    return created_event


def update_calendar_event(request, data, event_id=None):
    """Updates an existing Google Calendar event."""

    if not event_id:
        raise Exception("Cannot update event: event_id is missing.")

    credentials = get_google_credentials(request)

    service = build("calendar", "v3", credentials=credentials)
    attendees = data["attendees"]

    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except Exception as e:
        raise Exception(f"Failed to retrieve event: {e}")

    event["summary"] = data["title"]
    event["description"] = data["description"]
    event["start"] = {
        "dateTime": data["start_time"].isoformat(),
        "timeZone": settings.TIME_ZONE,
    }
    event["end"] = {
        "dateTime": (
            data["start_time"] + timedelta(minutes=data["duration"])
        ).isoformat(),
        "timeZone": settings.TIME_ZONE,
    }
    event["attendees"] = [{"email": attendee} for attendee in attendees]

    if data.get("is_online", True):
        if "conferenceData" not in event:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
    else:
        event.pop("conferenceData", None)

    try:
        updated_event = (
            service.events()
            .update(
                calendarId="primary",
                eventId=event_id,
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all",
            )
            .execute()
        )
    except Exception as e:
        raise Exception(f"Failed to update calendar event: {e}")

    return updated_event


def delete_calendar_event(request, event_id):
    """
    Cancels an existing Google Calendar event. `sendUpdates="all"` is what
    makes Google Calendar mail attendees its own cancellation notice -
    there's no separate cancellation email to compose.
    """
    if not event_id:
        raise Exception("Cannot cancel event: event_id is missing.")

    credentials = get_google_credentials(request)
    service = build("calendar", "v3", credentials=credentials)

    try:
        service.events().delete(
            calendarId="primary", eventId=event_id, sendUpdates="all"
        ).execute()
    except Exception as e:
        # Already gone (deleted directly in Google Calendar, etc.) - nothing to do.
        if "410" not in str(e) and "404" not in str(e):
            raise Exception(f"Failed to cancel calendar event: {e}")
