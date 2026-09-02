from datetime import datetime

from django.apps import apps
from django.db import models
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from horilla.config import logger
from horilla.horilla_middlewares import _thread_locals
from horilla_meet.methods import create_calendar_event, update_calendar_event
from horilla_meet.models import GoogleMeeting


@receiver(post_save, sender=GoogleMeeting)
def handle_google_meeting_save(sender, instance, created, **kwargs):
    """
    Handles creation and updates of GoogleMeeting (excluding attendees changes).
    """
    request = getattr(_thread_locals, "request")

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
            created_event = create_calendar_event(request, data)
        else:
            created_event = update_calendar_event(request, data, instance.event_id)
    except Exception:
        logger.exception(
            f"Error {'creating' if created else 'updating'} Google Calendar "
            f"event for GoogleMeeting {instance.id}"
        )
        raise

    if not created_event:
        # Google accepted the request but didn't hand back the event body
        # (seen as a transient hiccup - a retry normally succeeds). Don't
        # let it crash the caller, and don't wipe out the meet_url/event_id
        # we already had from a previous successful save.
        logger.warning(
            f"Google Calendar API returned an empty response for "
            f"GoogleMeeting {instance.id}; keeping existing meet_url/event_id."
        )
        return

    meet_link = created_event.get("hangoutLink", instance.meet_url)
    event_id = created_event.get("id") or instance.event_id
    GoogleMeeting.objects.filter(id=instance.id).update(
        meet_url=meet_link, event_id=event_id
    )
