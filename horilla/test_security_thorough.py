"""
Wider, adversarial coverage for the security work: who may act on whose records, what the exports
reveal, how encrypted values fail, which SVGs and uploads are accepted, and which endpoints can be
reached with what credentials. Each "refused" case has a matching "still works" case so a fix that
simply blocks everything would be caught.
"""

import datetime
import io
import json
import socket
import time
from unittest import mock

from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.http.multipartparser import MultiPartParserError
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path, reverse
from rest_framework_simplejwt.tokens import RefreshToken

from base.models import EmployeeShift, ShiftRequest
from leave.models import LeaveRequest
from employee.models import EmployeeBankDetails
from horilla.crypto_fields import EncryptedJSONField, EncryptedTextField
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee
from horilla.test_manager_scope_bulk import Setup as BulkSetup
from horilla.test_manager_scope_bulk import sheet_text
from horilla.test_review_findings_checks import parse
from horilla.upload_handler import svg_problem

HX = {"HTTP_HX_REQUEST": "true"}


def client_for(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client



def is_spreadsheet(response):
    """A 200 whose body is an .xlsx file (a zip), whatever content type it was labelled with."""
    if response.status_code != 200:
        return False
    body = b"".join(response.streaming_content) if response.streaming else response.content
    return body[:2] == b"PK"


# ---------------------------------------------------------------------------------------------
# 1. who may act on whose records
# ---------------------------------------------------------------------------------------------
class WhoMayActOnWhom(BulkSetup):
    def bank_for(self, employee):
        return EmployeeBankDetails.objects.bulk_create(
            [
                EmployeeBankDetails(
                    employee_id=employee,
                    bank_name="Original Bank",
                    account_number="1111",
                    branch="Main",
                    address="Somewhere",
                    any_other_code1="ORIG0000001",
                    pan_number=f"ABCDE{employee.pk:04d}F",
                    uan_number=f"1001{employee.pk:08d}",
                )
            ]
        )[0]

    def edit_bank(self, client, bank):
        return client.post(
            reverse("employee-bank-details-view-update", args=[bank.pk]),
            {
                "bank_name": "Changed Bank",
                "account_number": "9999",
                "branch": "B",
                "any_other_code1": "CHNG0000001",
                "address": "Elsewhere",
                "pan_number": "ZZZZZ9999Z",
                "uan_number": "999999999999",
            },
            **HX,
        )

    def changed(self, bank):
        bank.refresh_from_db()
        return bank.bank_name == "Changed Bank"

    def test_hr_with_the_permission_can_edit_anyones_bank_details(self):
        hr_user, _hr = make_employee("tt_hr")
        grant(hr_user, "employee", "change_employeebankdetails")
        bank = self.bank_for(self.outsider)
        self.edit_bank(client_for(hr_user), bank)
        self.assertTrue(self.changed(bank))

    def test_a_manager_of_a_manager_can_edit_the_bank_details_down_the_chain(self):
        boss_user, boss = make_employee("tt_boss")
        self.manager.employee_work_info.reporting_manager_id = boss
        self.manager.employee_work_info.save()
        bank = self.bank_for(self.report)
        self.edit_bank(client_for(boss_user), bank)
        self.assertTrue(self.changed(bank))

    def test_a_sibling_manager_cannot_edit_a_teammates_report(self):
        peer_user, peer = make_employee("tt_peer")
        make_employee("tt_peer_report", manager=peer)
        bank = self.bank_for(self.report)
        self.edit_bank(client_for(peer_user), bank)
        self.assertFalse(self.changed(bank))

    def test_someone_who_manages_nobody_cannot_edit_bank_details(self):
        bank = self.bank_for(self.report)
        self.edit_bank(client_for(self.stranger_user), bank)
        self.assertFalse(self.changed(bank))

    def test_a_manager_cannot_delete_a_record_that_has_no_owner_without_the_permission(self):
        group = Group.objects.create(name="tt group")
        self.manager_client.post(reverse("user-group-delete", args=[group.pk]), **HX)
        self.assertTrue(Group.objects.filter(pk=group.pk).exists())

    def test_the_permission_holder_can_still_delete_that_record(self):
        hr_user, _hr = make_employee("tt_hr2")
        grant(hr_user, "auth", "delete_group")
        group = Group.objects.create(name="tt group 2")
        client_for(hr_user).post(reverse("user-group-delete", args=[group.pk]), **HX)
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())

    def test_cancelling_a_strangers_leave_is_refused_and_a_teams_is_allowed(self):
        theirs, mine = self.leave_for(self.outsider), self.leave_for(self.report)
        self.manager_client.post(reverse("request-cancel", args=[theirs.pk]), {"reason": "no"}, **HX)
        self.manager_client.post(reverse("request-cancel", args=[mine.pk]), {"reason": "no"}, **HX)
        theirs.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual(theirs.status, "requested")
        self.assertEqual(mine.status, "rejected")


# ---------------------------------------------------------------------------------------------
# 2. exports
# ---------------------------------------------------------------------------------------------
class ExportScope(BulkSetup):
    def shift_request_for(self, employee, note):
        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift=f"tt shift {note}")])[0]
        day = datetime.date.today() + datetime.timedelta(days=3)
        return ShiftRequest.objects.bulk_create(
            [ShiftRequest(employee_id=employee, shift_id=shift, requested_date=day, requested_till=day, description=note)]
        )[0]

    def export_shifts(self, user):
        return client_for(user).get(reverse("shift-request-info-export"), {"selected_fields": ["description"]})

    def test_shift_request_export_is_limited_to_the_viewers_team(self):
        self.shift_request_for(self.report, "team-note")
        self.shift_request_for(self.outsider, "stranger-note")
        text = sheet_text(self.export_shifts(self.manager_user))
        self.assertIn("team-note", text)
        self.assertNotIn("stranger-note", text)

    def test_a_plain_employee_gets_no_one_elses_shift_requests(self):
        self.shift_request_for(self.outsider, "stranger-note")
        response = self.export_shifts(self.stranger_user)
        # turned away, or a sheet without the stranger's row - never a sheet that has it
        self.assertFalse(is_spreadsheet(response) and "stranger-note" in sheet_text(response))

    def test_someone_with_the_permission_exports_everything(self):
        hr_user, _hr = make_employee("tt_hr3")
        grant(hr_user, "base", "view_shiftrequest")
        self.shift_request_for(self.report, "team-note")
        self.shift_request_for(self.outsider, "stranger-note")
        text = sheet_text(self.export_shifts(hr_user))
        self.assertIn("team-note", text)
        self.assertIn("stranger-note", text)

    def test_attendance_export_for_someone_with_the_permission_includes_everyone(self):
        from attendance.forms import AttendanceExportForm

        hr_user, _hr = make_employee("tt_hr4")
        grant(hr_user, "attendance", "view_attendance")
        self.attendance_for(self.report, day="2031-04-04")
        self.attendance_for(self.outsider, day="2031-05-05")
        fields = [name for name, _label in AttendanceExportForm().fields["selected_fields"].choices]
        text = sheet_text(
            client_for(hr_user).get(
                reverse("attendance-info-export"), {"selected_fields": [f for f in fields if "date" in f][:1]}
            )
        )
        self.assertIn("Apr. 04, 2031", text)
        self.assertIn("May. 05, 2031", text)


# ---------------------------------------------------------------------------------------------
# 3. encrypted values
# ---------------------------------------------------------------------------------------------
KEY_A = "key-one-" + "a" * 40
KEY_B = "key-two-" + "b" * 40


class EncryptedValues(SimpleTestCase):
    def stored_under(self, secret_key, value="bind-password", keys=None):
        with override_settings(SECRET_KEY=secret_key, FIELD_ENCRYPTION_KEYS=keys):
            return EncryptedTextField().get_prep_value(value)

    def read_under(self, secret_key, stored, keys=None, field=None):
        with override_settings(SECRET_KEY=secret_key, FIELD_ENCRYPTION_KEYS=keys):
            return (field or EncryptedTextField()).from_db_value(stored, None, None)

    def test_the_same_key_round_trips(self):
        self.assertEqual(self.read_under(KEY_A, self.stored_under(KEY_A)), "bind-password")

    def test_a_different_key_gives_an_unusable_placeholder_not_the_ciphertext(self):
        from horilla.crypto_fields import UNDECRYPTABLE

        stored = self.stored_under(KEY_A)
        value = self.read_under(KEY_B, stored)
        self.assertEqual(value, UNDECRYPTABLE)
        self.assertNotIn("gAAAA", value)
        self.assertTrue(value, "must stay truthy so 'is a secret set?' checks keep protecting")

    def test_an_old_key_kept_in_the_key_list_still_decrypts(self):
        from horilla.crypto_fields import _legacy_key  # noqa: F401

        with override_settings(SECRET_KEY=KEY_A, FIELD_ENCRYPTION_KEYS=None):
            old = _legacy_key()
        with override_settings(SECRET_KEY=KEY_B, FIELD_ENCRYPTION_KEYS=None):
            new = _legacy_key()
        stored = self.stored_under(KEY_A)
        self.assertEqual(self.read_under(KEY_B, stored, keys=[new, old]), "bind-password")

    def test_plaintext_rows_pass_through_including_one_that_starts_like_a_token(self):
        for plain in ("hunter2", "gAAAAA-short", "a" * 120, "p@ss word!"):
            with self.subTest(plain=plain):
                self.assertEqual(self.read_under(KEY_A, plain), plain)

    def test_empty_and_none_are_untouched(self):
        self.assertIsNone(self.read_under(KEY_A, None))
        self.assertEqual(self.read_under(KEY_A, ""), "")

    def test_json_with_the_wrong_key_is_an_empty_dict(self):
        with override_settings(SECRET_KEY=KEY_A, FIELD_ENCRYPTION_KEYS=None):
            stored = EncryptedJSONField().get_prep_value({"client_secret": "s"})
        self.assertEqual(self.read_under(KEY_B, stored, field=EncryptedJSONField()), {})

    def test_the_failure_is_logged_as_an_error(self):
        stored = self.stored_under(KEY_A)
        with self.assertLogs("horilla.crypto_fields", level="ERROR"):
            self.read_under(KEY_B, stored)


# ---------------------------------------------------------------------------------------------
# 4. SVG uploads
# ---------------------------------------------------------------------------------------------
NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:svg="http://www.w3.org/2000/svg"'


def svg(inner, head=""):
    return f'{head}<svg {NS} viewBox="0 0 10 10">{inner}</svg>'.encode()


GOOD = {
    "plain shapes": svg('<rect width="5" height="5" fill="red"/><circle cx="5" cy="5" r="2"/>'),
    "gradient and style": svg(
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>'
        '<style>.a{fill:url(#g)}</style></defs><rect class="a" width="5" height="5"/>'
    ),
    "use with a local reference": svg('<defs><g id="a"><rect width="1" height="1"/></g></defs><use xlink:href="#a"/>'),
    "text, title and desc": svg("<title>Logo</title><desc>x</desc><text x='1' y='1'>Hi</text>"),
    "xml declaration": svg("<rect/>", head='<?xml version="1.0" encoding="UTF-8"?>\n'),
    "plain doctype line": svg(
        "<rect/>",
        head='<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n',
    ),
    "utf-8 byte order mark": b"\xef\xbb\xbf" + svg("<rect/>"),
    "a link to a web page": svg('<a href="https://example.com/"><text>go</text></a>'),
    "an image from the web": svg('<image href="https://example.com/a.png" width="1" height="1"/>'),
}

BAD = {
    "script element": svg("<script>alert(1)</script>"),
    "script with namespace prefix": svg("<svg:script>alert(1)</svg:script>"),
    "script with another prefix": svg('<x:script xmlns:x="http://www.w3.org/2000/svg">alert(1)</x:script>'),
    "foreignObject": svg("<foreignObject><div/></foreignObject>"),
    "namespaced foreignObject": svg("<svg:foreignObject><div/></svg:foreignObject>"),
    "onload on the root": f'<svg {NS} onload="alert(1)"></svg>'.encode(),
    "onclick on a shape": svg('<rect width="1" height="1" onclick="alert(1)"/>'),
    "javascript link": svg('<a xlink:href="javascript:alert(1)"><text>x</text></a>'),
    "javascript link with spaces": svg('<a href="  java\tscript:alert(1)"><text>x</text></a>'),
    "javascript link with character code": svg('<a xlink:href="&#106;avascript:alert(1)"><text>x</text></a>'),
    "javascript link with hex character code": svg('<a xlink:href="&#x6A;avascript:alert(1)"><text>x</text></a>'),
    "animate that sets a javascript link": svg(
        '<a><animate attributeName="href" values="javascript:alert(1)"/><text>x</text></a>'
    ),
    "set that sets a javascript link": svg('<a><set attributeName="href" to="javascript:alert(1)"/><text>x</text></a>'),
    "data html link": svg('<a href="data:text/html,<script>alert(1)</script>"><text>x</text></a>'),
    "iframe": svg("<iframe/>"),
    "embed": svg("<embed/>"),
    "object": svg("<object/>"),
    "entity definition": svg("<rect/>", head='<!DOCTYPE svg [<!ENTITY x "boom">]>'),
    "entity bomb": (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>'
    ),
    "entity bomb in utf-16": (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>'
    ).encode("utf-16"),
    "an xhtml page saved as .svg": b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>hi</p></body></html>',
    "not xml at all": b"this is not an svg",
    "broken xml": b'<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>',
    "script in utf-16": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'.encode("utf-16"),
}


class SvgUploads(SimpleTestCase):
    def test_genuine_svgs_are_accepted(self):
        for label, body in GOOD.items():
            with self.subTest(label):
                self.assertIsNone(svg_problem(body))
                parse("logo.svg", body)  # the whole upload path, not just the checker

    def test_dangerous_svgs_are_refused(self):
        for label, body in BAD.items():
            with self.subTest(label):
                self.assertIsNotNone(svg_problem(body))
                with self.assertRaises(MultiPartParserError):
                    parse("logo.svg", body)

    def test_an_uppercase_extension_is_checked_too(self):
        with self.assertRaises(MultiPartParserError):
            parse("LOGO.SVG", BAD["script element"])

    def test_the_other_checks_are_unchanged(self):
        with self.assertRaises(MultiPartParserError):
            parse("evil.php", b"<?php ?>")
        with self.assertRaises(MultiPartParserError):
            parse("cv.pdf", b"MZ" + b"0" * 100)
        parse("notes.txt", b"hello there, this is fine")


class SvgHeaders(SimpleTestCase):
    def run_middleware(self, path, content_type="text/html", status=200):
        from horilla.horilla_middlewares import SVGSecurityMiddleware

        def view(request):
            return HttpResponse("x", content_type=content_type, status=status)

        return SVGSecurityMiddleware(view)(RequestFactory().get(path))

    def test_every_way_of_writing_an_svg_path_is_covered(self):
        for suffix in (".svg", ".SVG", ".Svg"):
            with self.subTest(suffix):
                response = self.run_middleware(f"/media/x/{'a' * 32}{suffix}")
                self.assertIn("default-src 'none'", response["Content-Security-Policy"])
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_an_svg_content_type_is_covered_even_without_an_svg_path(self):
        response = self.run_middleware("/media/x/" + "a" * 32, content_type="image/svg+xml; charset=utf-8")
        self.assertIn("Content-Security-Policy", response)

    def test_other_files_are_left_alone(self):
        for path in ("/media/x/photo.png", "/media/x/doc.pdf", "/employee/employee-view/"):
            with self.subTest(path):
                self.assertNotIn("Content-Security-Policy", self.run_middleware(path, "image/png"))

    def test_an_error_response_is_left_alone(self):
        self.assertNotIn("Content-Security-Policy", self.run_middleware("/media/x/a.svg", status=404))


# ---------------------------------------------------------------------------------------------
# 5. uploads over a real connection, sent in tiny pieces
# ---------------------------------------------------------------------------------------------
def _echo_files(request):
    return HttpResponse(",".join(sorted(request.FILES.keys())) or "none")


urlpatterns = [path("echo/", _echo_files)]


class TinyPieceUploads(SimpleTestCase):
    """Sends the body to a real server one small piece at a time."""

    def serve(self):
        from django.test.testcases import LiveServerThread  # noqa: F401

    def test_a_php_file_is_refused_even_when_sent_a_few_bytes_at_a_time(self):
        from django.core.servers.basehttp import WSGIServer, WSGIRequestHandler, get_internal_wsgi_application
        import threading

        with override_settings(
            ROOT_URLCONF=__name__,
            MIDDLEWARE=["django.middleware.common.CommonMiddleware"],
            ALLOWED_HOSTS=["*"],
            DEBUG_PROPAGATE_EXCEPTIONS=False,
        ):
            server = WSGIServer(("127.0.0.1", 0), WSGIRequestHandler)
            application = get_internal_wsgi_application()

            def application_with_traceback(environ, start_response):
                try:
                    return application(environ, start_response)
                except Exception:
                    import traceback

                    traceback.print_exc()
                    raise

            server.set_app(application_with_traceback)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                boundary = "XBOUND"
                body = (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"f\"; filename=\"cv.pdf\"\r\n"
                    "Content-Type: application/pdf\r\n\r\n"
                ).encode() + b"<?php system($_GET['c']); ?>" + b"x" * 300 + f"\r\n--{boundary}--\r\n".encode()
                head = (
                    f"POST /echo/ HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: multipart/form-data; boundary={boundary}\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                ).encode()
                logs = self.enterContext(self.assertLogs("horilla.upload_handler", level="WARNING"))
                sock = socket.create_connection(("127.0.0.1", port), timeout=20)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(head)
                for i in range(0, len(body), 3):  # three bytes per packet
                    sock.sendall(body[i : i + 3])
                    if i < 600:
                        time.sleep(0.002)
                response = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                sock.close()
                status_line = response.split(b"\r\n", 1)[0]
                self.assertNotIn(b" 200 ", status_line, response[:300])  # the view never ran
                self.assertTrue(any("PHP" in line for line in logs.output), logs.output)
            finally:
                server.shutdown()
                server.server_close()


# ---------------------------------------------------------------------------------------------
# 6. endpoints and credentials
# ---------------------------------------------------------------------------------------------
class FaceDetectionCredentials(IsolatedTestCase):
    URL = "/api/facedetection/setup/"

    def test_a_session_cookie_is_not_accepted_even_with_csrf_switched_off(self):
        user, _employee = make_employee("tt_face")
        client = Client()
        client.force_login(user)
        self.assertEqual(client.post(self.URL, {"image": "x"}).status_code, 401)

    def test_a_bearer_token_is_accepted_without_any_csrf_token(self):
        user, _employee = make_employee("tt_face2")
        token = str(RefreshToken.for_user(user).access_token)
        client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        response = client.post(self.URL, {"image": "x"}, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertNotIn(response.status_code, (401, 403))

    def test_a_forged_token_is_refused(self):
        client = Client(raise_request_exception=False)
        response = client.post(self.URL, {"image": "x"}, HTTP_AUTHORIZATION="Bearer not.a.token")
        self.assertEqual(response.status_code, 401)


class BiometricDeviceGate(IsolatedTestCase):
    def test_nobody_without_the_permission_reaches_the_device_form(self):
        user, _employee = make_employee("tt_bio")
        _mgr_user, mgr = make_employee("tt_bio_mgr")
        make_employee("tt_bio_report", manager=mgr)
        for who in (user, _mgr_user):
            with self.subTest(user=who.username):
                response = client_for(who).get(reverse("biometric-device-add"), **HX)
                self.assertNotContains(response, "id_machine_ip")

    def test_someone_with_only_the_view_permission_does_not_get_the_form(self):
        user, _employee = make_employee("tt_bio2")
        grant(user, "biometric", "view_biometricdevices")
        response = client_for(user).get(reverse("biometric-device-add"), **HX)
        self.assertNotContains(response, "id_machine_ip")

    def test_the_permission_holder_can_open_the_form(self):
        user, _employee = make_employee("tt_bio3")
        grant(user, "biometric", "add_biometricdevices")
        response = client_for(user).get(reverse("biometric-device-add"), **HX)
        self.assertContains(response, "id_machine_ip")


# ---------------------------------------------------------------------------------------------
# 7. mail and payroll helpers
# ---------------------------------------------------------------------------------------------
class HelpdeskMailFailures(IsolatedTestCase):
    def test_a_mail_that_cannot_be_sent_is_logged(self):
        from helpdesk.threading import TicketSendThread

        _user, recipient = make_employee("tt_mail")
        thread = object.__new__(TicketSendThread)
        thread.request = None
        thread.host, thread.protocol, thread.ticket = "testserver", "http", None
        with mock.patch("helpdesk.threading.EmailMultiAlternatives.send", side_effect=OSError("smtp down")), mock.patch(
            "helpdesk.threading.messages"
        ) as messages, self.assertLogs("helpdesk.threading", level="ERROR") as logs:
            thread.send_email("Subject", "Body", [recipient])
        self.assertTrue(any("smtp down" in line or "not sent" in line for line in logs.output))
        messages.error.assert_called()


class PayrollContribution(SimpleTestCase):
    def test_entries_without_an_employer_share_do_not_zero_the_rest(self):
        from payroll.views.views import employer_contribution_total

        group = [{"employer_contribution_amount": 5}, {"amount": 3}, {"employer_contribution_amount": 7}]
        self.assertEqual(employer_contribution_total(group), 12)

    def test_a_group_with_none_at_all_is_zero(self):
        from payroll.views.views import employer_contribution_total

        self.assertEqual(employer_contribution_total([{"amount": 3}]), 0)
        self.assertEqual(employer_contribution_total([]), 0)


# ---------------------------------------------------------------------------------------------
# 8. an unreadable secret must never switch a protection off
# ---------------------------------------------------------------------------------------------
class UnreadableWebhookSecret(IsolatedTestCase):
    def make_credential(self):
        from whatsapp.models import WhatsappCredientials

        with override_settings(SECRET_KEY=KEY_A, FIELD_ENCRYPTION_KEYS=None):
            WhatsappCredientials.objects.bulk_create(
                [
                    WhatsappCredientials(
                        meta_token="tok",
                        meta_business_id="1",
                        meta_phone_number_id="2",
                        meta_phone_number="3",
                        meta_webhook_token="hook",
                        meta_app_secret="the-app-secret",
                    )
                ]
            )

    def post(self, header=None):
        from whatsapp.security import verify_meta_signature

        extra = {"HTTP_X_HUB_SIGNATURE_256": header} if header else {}
        request = RequestFactory().post("/whatsapp/", data=b'{"object":"x"}', content_type="application/json", **extra)
        return verify_meta_signature(request)

    def test_with_the_right_key_a_valid_signature_passes_and_an_unsigned_post_does_not(self):
        import hashlib
        import hmac

        self.make_credential()
        body = b'{"object":"x"}'
        good = "sha256=" + hmac.new(b"the-app-secret", body, hashlib.sha256).hexdigest()
        with override_settings(SECRET_KEY=KEY_A, FIELD_ENCRYPTION_KEYS=None):
            self.assertIs(self.post(good), True)
            self.assertIs(self.post(), False)

    def test_if_the_secret_cannot_be_read_unsigned_posts_are_still_refused(self):
        self.make_credential()
        with override_settings(SECRET_KEY=KEY_B, FIELD_ENCRYPTION_KEYS=None):
            self.assertIs(self.post(), False)  # not None: that would mean "nothing to check"

    def test_if_the_secret_cannot_be_read_a_signature_made_with_an_empty_key_is_refused(self):
        import hashlib
        import hmac

        self.make_credential()
        forged = "sha256=" + hmac.new(b"", b'{"object":"x"}', hashlib.sha256).hexdigest()
        with override_settings(SECRET_KEY=KEY_B, FIELD_ENCRYPTION_KEYS=None):
            self.assertIs(self.post(forged), False)


# ---------------------------------------------------------------------------------------------
# 9. repeating a parameter must not hide a row from the check
# ---------------------------------------------------------------------------------------------
class ParameterPollution(BulkSetup):
    def test_a_second_ids_value_cannot_smuggle_in_a_strangers_leave_request(self):
        mine, theirs = self.leave_for(self.report), self.leave_for(self.outsider)
        # the view reads the LAST value; the check used to look only at what it could parse
        body = f"ids={mine.pk}&ids=" + json.dumps([theirs.pk])
        self.manager_client.post(
            reverse("leave-request-bulk-delete"), body, content_type="application/x-www-form-urlencoded", **HX
        )
        self.assertTrue(LeaveRequest.objects.entire().filter(pk=theirs.pk).exists())

    def test_ids_in_the_query_string_are_checked_as_well(self):
        theirs = self.leave_for(self.outsider)
        self.manager_client.post(
            reverse("leave-request-bulk-delete") + "?ids=" + json.dumps([theirs.pk]), {"ids": json.dumps([])}, **HX
        )
        self.assertTrue(LeaveRequest.objects.entire().filter(pk=theirs.pk).exists())

    def test_ids_that_cannot_be_understood_are_refused_for_a_manager(self):
        mine = self.leave_for(self.report)
        for bad in ("[1, [2]]", "[1.5]", '[{"a": 1}]', "[true]", "abc", "[1,"):
            with self.subTest(bad=bad):
                self.manager_client.post(
                    reverse("leave-request-bulk-delete"), {"ids": bad}, **HX
                )
        self.assertTrue(LeaveRequest.objects.entire().filter(pk=mine.pk).exists())

    def test_a_huge_id_does_not_crash_the_check(self):
        response = self.manager_client.post(
            reverse("leave-request-bulk-delete"), {"ids": json.dumps([10**30])}, **HX
        )
        self.assertLess(response.status_code, 500)


# ---------------------------------------------------------------------------------------------
# 10. the class-based edit forms
# ---------------------------------------------------------------------------------------------
class ClassBasedForms(BulkSetup):
    def test_a_manager_cannot_open_the_edit_form_of_a_strangers_attendance(self):
        theirs = self.attendance_for(self.outsider)
        response = self.manager_client.get(reverse("attendance-update", args=[theirs.pk]), **HX)
        self.assertNotContains(response, "attendance_clock_in")

    def test_a_manager_can_open_the_edit_form_of_their_teams_attendance(self):
        mine = self.attendance_for(self.report)
        response = self.manager_client.get(reverse("attendance-update", args=[mine.pk]), **HX)
        self.assertContains(response, "attendance_clock_in")

    def test_the_permission_holder_can_open_any_attendance_edit_form(self):
        hr_user, _hr = make_employee("tt_hr_form")
        grant(hr_user, "attendance", "change_attendance")
        theirs = self.attendance_for(self.outsider)
        response = client_for(hr_user).get(reverse("attendance-update", args=[theirs.pk]), **HX)
        self.assertContains(response, "attendance_clock_in")

    def test_a_manager_cannot_open_the_edit_form_of_a_strangers_leave_request(self):
        theirs = self.leave_for(self.outsider)
        response = self.manager_client.get(reverse("request-update", args=[theirs.pk]), **HX)
        self.assertNotContains(response, "start_date")

    def test_the_decision_itself_for_a_leave_request(self):
        from horilla.decorators import employee_of, manager_may_enter

        mine, theirs = self.leave_for(self.report), self.leave_for(self.outsider)
        resolve = employee_of("leave.LeaveRequest", "pk")
        request = RequestFactory().get("/")
        request.user = self.manager_user
        self.assertTrue(manager_may_enter(request, "leave.change_leaverequest", [resolve(request, {"pk": mine.pk})]))
        self.assertFalse(manager_may_enter(request, "leave.change_leaverequest", [resolve(request, {"pk": theirs.pk})]))
        hr_user, _hr = make_employee("tt_hr_decide")
        grant(hr_user, "leave", "change_leaverequest")
        request.user = hr_user
        self.assertTrue(manager_may_enter(request, "leave.change_leaverequest", [resolve(request, {"pk": theirs.pk})]))


# ---------------------------------------------------------------------------------------------
# 11. the candidate export
# ---------------------------------------------------------------------------------------------
class CandidateExport(IsolatedTestCase):
    def setUp(self):
        from recruitment.factories import CompanyFactory, EmployeeFactory, RecruitmentFactory
        from recruitment.models import Candidate

        self.company = CompanyFactory()
        recruitment = RecruitmentFactory(company_id=self.company)
        Candidate.objects.create(
            name="Export Candidate",
            email="export.candidate@example.com",
            recruitment_id=recruitment,
            job_position_id=recruitment.job_position_id,
        )
        self.plain = EmployeeFactory(company=self.company)
        self.recruiter = EmployeeFactory(company=self.company, perms=["view_candidate"])

    def export_as(self, employee):
        from recruitment.forms import CandidateExportForm

        client = Client(raise_request_exception=False)
        client.login(username=employee.email, password="123qwe")
        session = client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        fields = [name for name, _label in CandidateExportForm().fields["selected_fields"].choices]
        return client.get(reverse("candidate-info-export"), {"selected_fields": [f for f in fields if "email" in f][:1] or fields[:1]})

    def test_a_plain_employee_does_not_get_the_candidate_list(self):
        response = self.export_as(self.plain)
        self.assertFalse(is_spreadsheet(response) and "export.candidate@example.com" in sheet_text(response))

    def test_someone_who_may_view_candidates_still_gets_the_export(self):
        response = self.export_as(self.recruiter)
        self.assertEqual(response.status_code, 200)
        self.assertIn("export.candidate@example.com", sheet_text(response))


# ---------------------------------------------------------------------------------------------
# 12. payroll dashboard data
# ---------------------------------------------------------------------------------------------
class PayrollDashboardData(IsolatedTestCase):
    # literal paths: the url name "dashboard-department-chart" is also used by offboarding
    PATHS = ("/payroll/dashboard-employee-chart/", "/payroll/dashboard-department-chart/")

    def test_a_plain_employee_gets_no_payroll_chart_data(self):
        user, _employee = make_employee("tt_pay_plain")
        for path_ in self.PATHS:
            with self.subTest(path_):
                response = client_for(user).get(path_, **HX)
                self.assertNotEqual(response.get("Content-Type", "").split(";")[0], "application/json")

    def test_a_plain_employee_cannot_export_payroll_from_the_dashboard(self):
        user, _employee = make_employee("tt_pay_plain2")
        response = client_for(user).post(reverse("dashboard-export"), {"start_date": "", "end_date": ""}, **HX)
        self.assertFalse(is_spreadsheet(response))

    def test_someone_who_may_view_the_dashboard_still_gets_the_data(self):
        user, _employee = make_employee("tt_pay_hr")
        grant(user, "payroll", "view_contract")
        for path_ in self.PATHS:
            with self.subTest(path_):
                response = client_for(user).get(path_, **HX)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get("Content-Type", "").split(";")[0], "application/json")


# ---------------------------------------------------------------------------------------------
# 13. the start of an upload, however finely it arrives (driving the handler directly)
# ---------------------------------------------------------------------------------------------
def feed(filename, content, step):
    """Hand the handler ``content`` in pieces of ``step`` bytes, as a file upload would."""
    from horilla.upload_handler import SafeUploadHandler

    handler = SafeUploadHandler(RequestFactory().post("/upload/"))
    handler.new_file("f", filename, "application/octet-stream", len(content))
    for start in range(0, len(content), step):
        handler.receive_data_chunk(content[start : start + step], start)
    handler.file_complete(len(content))


class TinyFirstPiece(SimpleTestCase):
    PDF = b"%PDF-1.4\n" + b"0" * 6000
    PHP = b"<?php system($_GET['c']); ?>" + b"0" * 6000
    NOT_PNG = b"this is not a png at all" + b"0" * 6000
    EXE = b"MZ" + b"\x90" * 6000

    def test_dangerous_content_is_refused_at_every_piece_size(self):
        for step in (1, 2, 3, 5, 7, 8, 9, 64, 1000, 4095, 4096, 4097, 100000):
            for name, body in (("cv.pdf", self.PHP), ("pic.png", self.NOT_PNG), ("doc.pdf", self.EXE)):
                with self.subTest(step=step, name=name):
                    with self.assertRaises(MultiPartParserError):
                        feed(name, body, step)

    def test_a_genuine_file_is_accepted_at_every_piece_size(self):
        for step in (1, 3, 7, 8, 100, 4096, 100000):
            with self.subTest(step=step):
                feed("cv.pdf", self.PDF, step)

    def test_a_short_bad_file_is_refused_when_it_ends(self):
        with self.assertRaises(MultiPartParserError):
            feed("cv.pdf", b"not really a pdf", 1)

    def test_tiny_and_empty_files_are_fine(self):
        feed("empty.txt", b"", 1)
        feed("tiny.txt", b"hi", 1)
