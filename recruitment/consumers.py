from channels.generic.websocket import JsonWebsocketConsumer

from employee.models import Employee
from horilla.background_utils import with_db_cleanup


class ChatConsumer(JsonWebsocketConsumer):
    """
    Real-time companion to the existing StageNote chat views (add_note,
    create_note, candidate_add_notes in recruitment/views/views.py) - it
    does not send or persist chat messages itself. Those endpoints keep
    doing all the actual work (validation, permissions, file uploads);
    this consumer only tells connected clients "a new message arrived,
    refetch" (see broadcast_new_message, recruitment/realtime.py) and
    relays typing events, so delivery doesn't wait on the 10s poll.

    Auth mirrors candidate_login_required (recruitment/decorators.py) -
    either an authenticated employee, or a session carrying this exact
    candidate's id (tighter than the decorator's own "any candidate_id in
    session" check, since a websocket group is scoped to one candidate).
    """

    def group_name(self):
        return f"chat_candidate_{self.candidate_id}"

    @with_db_cleanup
    def connect(self):
        self.candidate_id = self.scope["url_route"]["kwargs"]["candidate_id"]

        user = self.scope["user"]
        session = self.scope["session"]
        is_employee = getattr(user, "is_authenticated", False)
        is_this_candidate = session.get("candidate_id") == self.candidate_id

        if not (is_employee or is_this_candidate):
            self.close()
            return

        self.employee_name = None
        if is_employee:
            employee = Employee.objects.filter(employee_user_id=user).first()
            self.employee_name = employee.get_full_name() if employee else None

        from asgiref.sync import async_to_sync

        async_to_sync(self.channel_layer.group_add)(self.group_name(), self.channel_name)
        self.accept()

    def disconnect(self, close_code):
        if hasattr(self, "candidate_id"):
            from asgiref.sync import async_to_sync

            async_to_sync(self.channel_layer.group_discard)(
                self.group_name(), self.channel_name
            )

    def receive_json(self, content, **kwargs):
        if content.get("type") == "typing":
            from asgiref.sync import async_to_sync

            async_to_sync(self.channel_layer.group_send)(
                self.group_name(),
                {
                    "type": "user_typing",
                    # Client-generated id (see the WS client script in each
                    # chat template), echoed back to every connection in
                    # the group including the sender's own. The client -
                    # not this server-side channel_name - is the one that
                    # decides whether an incoming typing event is its own
                    # echo, since it's the single source of truth for "is
                    # this me" and doesn't depend on inferring identity
                    # from the connection.
                    "client_id": content.get("client_id"),
                    "sender_name": self.employee_name,
                },
            )

    # Group event handlers - "type" above (chat_message/user_typing) maps
    # to the method Channels calls for that event.
    def chat_message(self, event):
        self.send_json({"type": "new_message"})

    def user_typing(self, event):
        self.send_json(
            {
                "type": "typing",
                "client_id": event["client_id"],
                "sender_name": event["sender_name"],
            }
        )
