from django.db.models.signals import post_save
from django.dispatch import receiver

from horilla.config import logger
from horilla.horilla_middlewares import _thread_locals
from outlook_auth.methods import (
    create_teams_calendar_event,
    get_azure_api,
    update_teams_calendar_event,
)
from outlook_auth.models import OutlookMeeting


@receiver(post_save, sender=OutlookMeeting)
def handle_outlook_meeting_save(sender, instance, created, **kwargs):
    """
    Handles creation and updates of OutlookMeeting. Mirrors
    horilla_meet.signals.handle_google_meeting_save.
    """
    request = getattr(_thread_locals, "request", None)
    api = get_azure_api(request)
    if not api:
        return

    data = {
        "title": instance.title,
        "description": instance.description,
        "start_time": instance.start_time,
        "duration": instance.duration,
        "attendees": instance.attendees,
        "is_online": instance.is_online,
    }

    try:
        if created:
            created_event = create_teams_calendar_event(api, data)
        else:
            created_event = update_teams_calendar_event(api, data, instance.event_id)
    except Exception:
        logger.exception(
            f"Error {'creating' if created else 'updating'} Teams calendar "
            f"event for OutlookMeeting {instance.id}"
        )
        raise

    if not created_event:
        # Graph accepted the request but didn't hand back the event body
        # (seen as a transient hiccup - a retry normally succeeds). Don't
        # let it crash the caller, and don't wipe out the join_url/event_id
        # we already had from a previous successful save.
        logger.warning(
            f"Teams calendar API returned an empty response for "
            f"OutlookMeeting {instance.id}; keeping existing join_url/event_id."
        )
        return

    # Graph returns "onlineMeeting": null (not a missing key) for an
    # offline event, so `.get("onlineMeeting", {})` isn't enough - the key
    # is present with a None value, which the `or {}` below catches.
    join_url = (created_event.get("onlineMeeting") or {}).get(
        "joinUrl", instance.join_url
    )
    event_id = created_event.get("id") or instance.event_id
    OutlookMeeting.objects.filter(id=instance.id).update(
        join_url=join_url, event_id=event_id
    )
