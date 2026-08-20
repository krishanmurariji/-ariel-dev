from django.db.models.signals import post_save
from django.dispatch import receiver

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
    }

    if created:
        created_event = create_teams_calendar_event(api, data)
    else:
        created_event = update_teams_calendar_event(api, data, instance.event_id)

    join_url = created_event.get("onlineMeeting", {}).get(
        "joinUrl", instance.join_url
    )
    event_id = created_event.get("id")
    OutlookMeeting.objects.filter(id=instance.id).update(
        join_url=join_url, event_id=event_id
    )
