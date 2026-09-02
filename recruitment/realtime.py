from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_new_message(candidate_id):
    """
    Tells everyone connected to this candidate's chat (recruitment/consumers.py)
    that a new StageNote exists, so they refetch instead of waiting on the
    10s poll. Called right after a chat message is saved in add_note,
    create_note, and candidate_add_notes (recruitment/views/views.py).
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        f"chat_candidate_{candidate_id}", {"type": "chat_message"}
    )
