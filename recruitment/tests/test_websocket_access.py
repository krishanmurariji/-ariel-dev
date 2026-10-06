"""
Two gaps in the candidate chat websocket:

* No origin check, so a page on another site could open an authenticated
  socket using a visitor's session cookie (cross-site WebSocket hijacking).
* Any logged-in employee could join any candidate's chat, although the pages
  that show the same notes require a permission or a manager role.
"""

import pathlib

from channels.security.websocket import AllowedHostsOriginValidator
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import override_settings

from employee.models import Employee
from horilla.isolated_testcase import IsolatedTestCase
from horilla_auth.models import HorillaUser
from recruitment.access import staff_can_use_chat
from recruitment.models import Recruitment


def make_user(username):
    user = HorillaUser.objects.create(username=username, is_active=True)
    employee = Employee.objects.create(
        employee_first_name=username,
        employee_last_name="Test",
        employee_user_id=user,
        email=f"{username}@example.com",
    )
    return user, employee


class StaffChatAccessTests(IsolatedTestCase):
    def test_anonymous_and_ordinary_employee_are_refused(self):
        self.assertFalse(staff_can_use_chat(AnonymousUser()))
        user, _ = make_user("ws_plain")
        self.assertFalse(staff_can_use_chat(user))

    def test_permission_holder_is_allowed(self):
        user, _ = make_user("ws_perm")
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="recruitment", codename="view_stagenote"
            )
        )
        self.assertTrue(staff_can_use_chat(HorillaUser.objects.get(pk=user.pk)))

    def test_recruitment_manager_is_allowed(self):
        user, employee = make_user("ws_manager")
        recruitment = Recruitment.objects.create(
            title="Backend", description="x", vacancy=1
        )
        recruitment.recruitment_managers.add(employee)
        self.assertTrue(staff_can_use_chat(user))


async def _accepting_app(scope, receive, send):
    await receive()
    await send({"type": "websocket.accept"})


class OriginCheckTests(IsolatedTestCase):
    @override_settings(ALLOWED_HOSTS=["erp.example.com"], DEBUG=False)
    async def test_other_sites_cannot_open_a_socket(self):
        app = AllowedHostsOriginValidator(_accepting_app)
        evil = WebsocketCommunicator(
            app, "/ws/chat/1/", headers=[(b"origin", b"https://evil.example")]
        )
        connected, _ = await evil.connect()
        self.assertFalse(connected)

        own = WebsocketCommunicator(
            app, "/ws/chat/1/", headers=[(b"origin", b"https://erp.example.com")]
        )
        connected, _ = await own.connect()
        self.assertTrue(connected)
        await own.disconnect()

    def test_asgi_routing_is_wrapped_in_the_origin_validator(self):
        # horilla/asgi.py cannot be imported in a test (it starts the
        # scheduler), so pin the wiring at source level.
        source = (pathlib.Path(__file__).resolve().parents[2] / "horilla" / "asgi.py").read_text()
        self.assertIn("AllowedHostsOriginValidator(", source)
        self.assertLess(
            source.index("AllowedHostsOriginValidator("),
            source.index("AuthMiddlewareStack(URLRouter"),
        )
