"""
Tests for the second hardening batch:

* one-time codes / reset links no longer stored in the mail log
* two-factor: middleware fails closed, API login asks for the emailed code
* logout needs a POST
* shared list/table templates no longer print stored text as raw HTML
* every upload passes the safe-upload handler
* scheduled jobs are started by one worker only
"""

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import EmailMessage
from django.http.multipartparser import MultiPartParserError
from django.template import Context, Template
from django.test import Client, RequestFactory, SimpleTestCase, override_settings

from base.backends import ConfiguredEmailBackend
from base.email_log_safety import HIDDEN_BODY, body_for_log, looks_sensitive
from base.middleware import TwoFactorAuthMiddleware
from base.models import EmailLog
from base.test_security_regressions import make_user
from horilla.isolated_testcase import IsolatedTestCase
from horilla.scheduler_guard import _try_lock
from horilla_views.templatetags.generic_template_filters import (
    cell_value,
    format_attrs,
    safe_cell,
)

BASE_DIR = Path(settings.BASE_DIR)


# --------------------------------------------------------------------------
# mail log
# --------------------------------------------------------------------------
class EmailLogSafetyTests(IsolatedTestCase):
    def test_otp_and_reset_mails_are_recognised(self):
        self.assertTrue(looks_sensitive("Your OTP Code", "code"))
        self.assertTrue(looks_sensitive("Password reset on Horilla", ""))
        self.assertTrue(looks_sensitive("Hello", "open http://x.in/reset/Mg/cd3-4a5b6c/ now"))
        self.assertFalse(looks_sensitive("Your payslip for June", "Dear employee"))

    def test_body_for_log_hides_only_sensitive_messages(self):
        plain = EmailMessage("Hello", "some text", to=["a@example.com"])
        self.assertEqual(body_for_log(plain), "some text")
        flagged = EmailMessage("Hello", "secret text", to=["a@example.com"])
        flagged.sensitive = True
        self.assertEqual(body_for_log(flagged), HIDDEN_BODY)

    def test_backend_stores_a_hidden_body_for_sensitive_mail(self):
        otp = EmailMessage("Your OTP Code", "Your OTP code is 481516", "a@example.com", ["b@example.com"])
        normal = EmailMessage("Payslip", "hello there", "a@example.com", ["b@example.com"])
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1):
            ConfiguredEmailBackend().send_messages([otp, normal])
        bodies = {log.subject: log.body for log in EmailLog.objects.all()}
        self.assertEqual(bodies["Your OTP Code"], HIDDEN_BODY)
        self.assertEqual(bodies["Payslip"], "hello there")
        self.assertNotIn("481516", "".join(bodies.values()))

    def test_password_reset_mail_is_flagged(self):
        from base.forms import PassWordResetForm

        context = {
            "email": "b@example.com", "domain": "example.com", "site_name": "Ariel",
            "uid": "Mg", "token": "abc-123456", "protocol": "https",
            "user": SimpleNamespace(get_username=lambda: "bob", username="bob"),
        }
        PassWordResetForm().send_mail(
            "registration/password_reset_subject.txt",
            "registration/password_reset_email.html",
            context, "a@example.com", "b@example.com",
        )
        self.assertTrue(mail.outbox[-1].sensitive)

    def test_otp_mail_is_flagged(self):
        from base import otp_mail

        class Inline:
            def __init__(self, target):
                self.target = target

            def start(self):
                self.target()

        built = EmailMessage("Your OTP Code", "code 111222", "a@example.com", ["b@example.com"])
        employee = SimpleNamespace(get_mail=lambda: "b@example.com")
        with patch.object(otp_mail, "threading", SimpleNamespace(Thread=lambda target: Inline(target))), \
             patch.object(otp_mail, "ConfiguredEmailBackend", return_value=SimpleNamespace(dynamic_from_email_with_display_name="A <a@example.com>")), \
             patch.object(otp_mail, "build_letterhead_email", return_value=built):
            otp_mail.send_otp_email(employee, "111222")
        self.assertTrue(built.sensitive)
        self.assertEqual(len(mail.outbox), 1)

    def test_migration_scrubs_codes_already_stored(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module("base.migrations.0017_scrub_sensitive_email_logs")
        for subject, body in [
            ("Your OTP Code", "Your OTP code is 999888"),
            ("Password reset on Ariel", "click https://x/reset/Mg/abc-123/"),
            ("Payslip", "keep this text"),
        ]:
            EmailLog.objects.create(subject=subject, body=body, from_email="a@example.com", to="b@example.com", status="sent")
        migration.scrub(apps, None)
        bodies = {log.subject: log.body for log in EmailLog.objects.all()}
        self.assertEqual(bodies["Your OTP Code"], HIDDEN_BODY)
        self.assertEqual(bodies["Password reset on Ariel"], HIDDEN_BODY)
        self.assertEqual(bodies["Payslip"], "keep this text")


# --------------------------------------------------------------------------
# two-factor
# --------------------------------------------------------------------------
class TwoFactorMiddlewareTests(IsolatedTestCase):
    def setUp(self):
        self.user = make_user("mw_user")
        self.middleware = TwoFactorAuthMiddleware(lambda request: "PASSED")

    def _request(self, verified=False):
        request = RequestFactory().get("/employee/")
        request.user = self.user
        request.session = {"otp_code_verified": True} if verified else {}
        return request

    def _run(self, backend, verified=False):
        with override_settings(TWO_FACTORS_AUTHENTICATION=True), patch("base.middleware.ConfiguredEmailBackend", backend):
            return self.middleware(self._request(verified))

    def test_an_error_reading_the_mail_setup_no_longer_switches_2fa_off(self):
        response = self._run(MagicMock(side_effect=Exception("database hiccup")))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/two-factor")

    def test_configured_mail_enforces_the_code(self):
        response = self._run(MagicMock(return_value=SimpleNamespace(configuration=object())))
        self.assertEqual(response.status_code, 302)

    def test_verified_session_passes(self):
        self.assertEqual(self._run(MagicMock(side_effect=Exception("x")), verified=True), "PASSED")

    def test_no_mail_server_configured_skips_with_a_warning(self):
        with self.assertLogs("base.middleware", "WARNING"):
            result = self._run(MagicMock(return_value=SimpleNamespace(configuration=None)))
        self.assertEqual(result, "PASSED")

    def test_2fa_off_changes_nothing(self):
        request = self._request()
        with override_settings(TWO_FACTORS_AUTHENTICATION=False):
            self.assertEqual(self.middleware(request), "PASSED")


class ApiLoginTwoFactorTests(IsolatedTestCase):
    LOGIN = "/api/auth/login/"

    def setUp(self):
        cache.clear()
        self.user = make_user("api2fa")
        self.client = Client()
        self.sent = MagicMock()
        # ExitStack, not a manual enable()/disable()-or-start()/stop() loop:
        # that manual version left override_settings' internal wrapped-
        # settings stack unbalanced under some failure orderings, so
        # TWO_FACTORS_AUTHENTICATION (and DEBUG, pulled in along with it)
        # stayed corrupted for every test that ran afterwards in the same
        # process - a later, unrelated plain GET would then 400. addCleanup
        # runs stack.close() once, in the correct (reverse) order, always.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(override_settings(TWO_FACTORS_AUTHENTICATION=True))
        stack.enter_context(
            patch(
                "horilla_api.api_views.auth.views.ConfiguredEmailBackend",
                MagicMock(return_value=SimpleNamespace(configuration=object())),
            )
        )
        stack.enter_context(patch("horilla_api.api_views.auth.views.send_otp_email", self.sent))

    def _post(self, ip, otp=None, password="Passw0rd!x"):
        body = {"username": "api2fa", "password": password}
        if otp is not None:
            body["otp"] = otp
        return self.client.post(self.LOGIN, data=json.dumps(body), content_type="application/json", HTTP_X_REAL_IP=ip)

    def _code(self):
        return cache.get(f"api_otp_code_{self.user.pk}")

    def test_password_alone_gives_no_tokens_and_emails_a_code(self):
        response = self._post("198.51.100.1")
        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertTrue(body["otp_required"])
        self.assertNotIn("access", body)
        self.assertNotIn("refresh", body)
        self.assertEqual(self.sent.call_count, 1)
        self.assertTrue(self._code())

    def test_asking_again_does_not_email_again_straight_away(self):
        self._post("198.51.100.2")
        self._post("198.51.100.2")
        self.assertEqual(self.sent.call_count, 1)

    def test_correct_code_gives_tokens_and_is_single_use(self):
        self._post("198.51.100.3")
        code = self._code()
        response = self._post("198.51.100.3", otp=code)
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())
        self.assertIsNone(self._code())
        # the same code cannot be used again
        self.assertEqual(self._post("198.51.100.3", otp=code).status_code, 401)

    def test_wrong_code_is_refused(self):
        self._post("198.51.100.4")
        response = self._post("198.51.100.4", otp="000000")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.json())

    def test_three_wrong_codes_lock_even_the_right_one_out(self):
        self._post("198.51.100.5")
        code = self._code()
        for _ in range(3):
            self.assertEqual(self._post("198.51.100.5", otp="000000").status_code, 401)
        self.assertEqual(self._post("198.51.100.5", otp=code).status_code, 429)

    def test_wrong_password_never_reaches_the_code_step(self):
        response = self._post("198.51.100.6", password="nope")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("otp_required", response.json())
        self.assertEqual(self.sent.call_count, 0)

    @override_settings(TWO_FACTORS_AUTHENTICATION=False)
    def test_with_2fa_off_login_is_unchanged(self):
        response = self._post("198.51.100.7")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())
        self.assertEqual(self.sent.call_count, 0)


# --------------------------------------------------------------------------
# logout
# --------------------------------------------------------------------------
# KNOWN ISSUE: LogoutTests fails with a bare 400 (Django's own generic page,
# not went_wrong.html - no KeyError, no exception on the got_request_exception
# signal either) when it runs in the same process right after
# ApiLoginTwoFactorTests. Passes alone and in the full default `manage.py
# test` run order; only reproduces with that specific pairing. Root cause
# not yet found - ruled out MissingParameterMiddleware's KeyError handling
# and any exception at all reaching Django's exception machinery. Since the
# trigger is test-only tooling (override_settings/cache.clear()/patch(), none
# of which exist in a real request), this does not indicate a production bug
# in the logout fix itself.
class LogoutTests(IsolatedTestCase):
    def setUp(self):
        self.user = make_user("lo_user")

    def _logged_in(self, client):
        return "_auth_user_id" in client.session

    def test_a_plain_get_does_not_sign_you_out(self):
        self.client.force_login(self.user)
        response = self.client.get("/logout/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "base/auth/logout_confirm.html")
        self.assertTrue(self._logged_in(self.client))

    def test_get_when_not_logged_in_goes_to_login(self):
        response = self.client.get("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/login/")

    def test_post_signs_out(self):
        self.client.force_login(self.user)
        response = self.client.post("/logout/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/login/", response.content.decode())
        self.assertFalse(self._logged_in(self.client))

    def test_post_from_another_site_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post("/logout/").status_code, 403)
        self.assertTrue(self._logged_in(client))

    def test_confirm_page_form_works_with_its_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        page = client.get("/logout/")
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        self.assertEqual(client.post("/logout/", {"csrfmiddlewaretoken": token}).status_code, 200)
        self.assertFalse(self._logged_in(client))

    def test_menu_items_are_forms_not_links(self):
        for relative in (
            "horilla_theme/templates/base/navbar_components/profile_section.html",
            "employee/templates/navbar_component/profile_section.html",
        ):
            source = (BASE_DIR / relative).read_text(encoding="utf-8")
            self.assertRegex(source, r'<form method="post" action="[^"]*logout')
            self.assertNotRegex(source, r'<a[^>]*href="[^"]*logout')


# --------------------------------------------------------------------------
# shared table templates
# --------------------------------------------------------------------------
class Row:
    name = "Ann <img src=x onerror=alert(1)><script>alert(2)</script>"
    note = "<b>bold</b> and 5 < 6 & Tom"
    pk = 7
    tricky = '" onmouseover="alert(1)'

    def status_col(self):
        return '<select onchange="save(this)" hx-post="/x/"><option>Open</option></select>'

    def get_full_name(self):
        return "Ann Lee"


class SharedTableFilterTests(IsolatedTestCase):
    def _cell(self, attribute):
        return str(safe_cell(cell_value(Row(), attribute)))

    def test_stored_text_is_cleaned_but_formatting_survives(self):
        out = self._cell("name")
        self.assertIn("Ann", out)
        self.assertNotIn("onerror", out)
        self.assertNotIn("<script", out)
        note = self._cell("note")
        self.assertIn("<b>bold</b>", note)
        self.assertIn("5 &lt; 6 &amp; Tom", note)

    def test_code_built_column_html_is_left_alone(self):
        out = self._cell("status_col")
        self.assertIn('onchange="save(this)"', out)
        self.assertIn('hx-post="/x/"', out)

    def test_plain_method_text_and_other_types_display_normally(self):
        self.assertEqual(self._cell("get_full_name"), "Ann Lee")
        self.assertEqual(str(safe_cell(5)), "5")
        self.assertEqual(str(safe_cell(None)), "None")

    def test_the_template_chain_matches(self):
        html = Template(
            "{% load generic_template_filters %}{{ row|cell_value:'name'|safe_cell }}"
        ).render(Context({"row": Row()}))
        self.assertNotIn("onerror", html)

    def test_attribute_strings_cannot_be_broken_out_of(self):
        out = format_attrs('hx-get="/e/{pk}/" data-n="{tricky}"', Row())
        self.assertIn('hx-get="/e/7/"', out)
        self.assertNotIn('data-n="" onmouseover', out)
        self.assertIn("&quot;", out)

    def test_method_values_in_attribute_strings_stay_unescaped(self):
        self.assertIn("<select", format_attrs("{status_col}", Row()))

    def test_no_template_prints_data_cells_or_formatted_attrs_with_plain_safe(self):
        bad = []
        pattern = re.compile(
            r"getattribute:(attribute|col\.1)[^}]*\|\s*safe\b|\|\s*format:[^}|]*(\|selected_format:[^|}]+)?\|\s*safe\b"
        )
        for path in BASE_DIR.rglob("*.html"):
            if any(part in path.parts for part in ("venv", "staticfiles", "node_modules")):
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                bad.append(str(path.relative_to(BASE_DIR)))
        self.assertEqual(bad, [])

    def test_rich_text_pages_use_the_sanitizer(self):
        expectations = {
            "offboarding/templates/offboarding/resignation/request_single_view.html": "letter.description|sanitize_html",
            "pms/templates/meetings/meeting_single_view.html": "meeting.response|default:\"\"|sanitize_html",
            "employee/templates/policies/view_policy.html": "policy.body|sanitize_html",
            "employee/templates/tabs/mail_log.html": "log.body|sanitize_html",
            "recruitment/templates/recruitment/recruitment_details.html": "recruitment.description|sanitize_html",
            "onboarding/templates/onboarding/roles_responsibility.html": "roles_responsibilities|sanitize_html",
        }
        for relative, needle in expectations.items():
            with self.subTest(relative):
                source = (BASE_DIR / relative).read_text(encoding="utf-8")
                self.assertIn(needle, source)
                self.assertNotRegex(source, r"(description|response|body|roles_responsibilities)\s*(\|[^}|]*)*\|\s*safe\b")

    def test_org_chart_names_cannot_close_the_script_block(self):
        node = {"name": "</script><script>alert(1)</script>", "title": "x", "children": []}
        html = Template('{{ data|json_script:"org-chart-data" }}').render(Context({"data": node}))
        inner = re.search(r"<script[^>]*>(.*)</script>", html, re.S).group(1)
        self.assertNotIn("</script", inner.lower())
        self.assertEqual(json.loads(inner), node)
        source = (BASE_DIR / "employee/templates/organisation_chart/chart.html").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"act_datasource\s*\|\s*safe")

    def test_a_list_page_does_not_run_a_stored_name(self):
        from base.models import Company
        from horilla_auth.models import HorillaUser
        from outlook_auth.models import AzureApi

        company = Company.objects.create(company="Acme", address="a", country="c", state="s", city="c", zip="1")
        AzureApi.objects.create(
            outlook_client_id="id", outlook_client_secret="s", outlook_tenant_id="t",
            outlook_email="a@example.com", outlook_display_name="<svg onload=alert(1)>",
            outlook_redirect_uri="https://example.com/cb", company=company,
        )
        admin = HorillaUser.objects.create(username="lst_admin", is_active=True, is_staff=True, is_superuser=True)
        from employee.models import Employee

        Employee.objects.create(employee_first_name="A", employee_last_name="B", employee_user_id=admin, email="lst@example.com")
        client = Client()
        client.force_login(admin)
        from django.urls import reverse

        response = client.get(reverse("outlook_server_list"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("onload", response.content.decode())


# --------------------------------------------------------------------------
# uploads
# --------------------------------------------------------------------------
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff\xe0" + b"0" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64
ZIP = b"PK\x03\x04" + b"0" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 32
CLEAN_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'


def upload(name, content):
    request = RequestFactory().post("/upload/", {"f": SimpleUploadedFile(name, content)})
    return request.FILES


class UploadHandlerTests(SimpleTestCase):
    def test_genuine_files_are_accepted(self):
        cases = {
            "photo.png": PNG, "photo.jpg": JPG, "photo.jpeg": JPG, "scan.PDF": PDF,
            "cv.docx": ZIP, "sheet.xlsx": ZIP, "slides.pptx": ZIP, "pic.webp": WEBP,
            "notes.txt": b"hello world, this is fine", "data.csv": b"a,b\n1,2\n",
            "logo.svg": CLEAN_SVG, "old.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 32,
            "tiny.txt": b"hi",
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.assertIn("f", upload(name, content))

    def test_blocked_types_are_refused_even_with_a_second_extension(self):
        for name in ["run.exe", "shell.php", "invoice.php.jpg", "page.HTML", "x.htm", "a.js", "a.py", "a.sh", "a.bat", "a.jar"]:
            with self.subTest(name):
                with self.assertRaises(MultiPartParserError):
                    upload(name, PNG)

    def test_a_program_renamed_to_a_document_is_refused(self):
        for name in ["report.pdf", "photo.png", "cv.docx", "notes.txt"]:
            with self.subTest(name):
                with self.assertRaises(MultiPartParserError):
                    upload(name, b"MZ\x90\x00" + b"0" * 64)
        with self.assertRaises(MultiPartParserError):
            upload("tool.txt", b"\x7fELF" + b"0" * 64)

    def test_content_that_does_not_match_the_extension_is_refused(self):
        cases = {
            "fake.pdf": b"this is just some text, not a pdf at all",
            "fake.png": b"this is just some text, not an image",
            "fake.jpg": PDF,
            "fake.docx": b"plain text pretending to be word",
        }
        for name, content in cases.items():
            with self.subTest(name):
                with self.assertRaises(MultiPartParserError):
                    upload(name, content)

    def test_php_code_hidden_in_an_image_is_refused(self):
        with self.assertRaises(MultiPartParserError):
            upload("avatar.jpg", JPG + b"<?php system($_GET['c']); ?>")

    def test_svg_with_script_is_refused(self):
        for body in [
            b"<svg><script>alert(1)</script></svg>",
            b'<svg onload="alert(1)"></svg>',
            b'<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
            b"<svg><foreignObject><iframe src=x></iframe></foreignObject></svg>",
        ]:
            with self.subTest(body):
                with self.assertRaises(MultiPartParserError):
                    upload("logo.svg", body)

    @override_settings(UPLOAD_MAX_BYTES=1000)
    def test_oversized_files_are_refused(self):
        with self.assertRaises(MultiPartParserError):
            upload("big.txt", b"a" * 5000)
        self.assertIn("f", upload("small.txt", b"a" * 500))

    def test_the_handler_is_first_in_line(self):
        self.assertEqual(settings.FILE_UPLOAD_HANDLERS[0], "horilla.upload_handler.SafeUploadHandler")


# --------------------------------------------------------------------------
# scheduled jobs run in one worker only
# --------------------------------------------------------------------------
def _run_python(code, *args):
    env = {**os.environ, "PYTHONPATH": str(BASE_DIR), "DJANGO_SETTINGS_MODULE": "horilla.settings"}
    result = subprocess.run(
        [sys.executable, "-c", code, *args], capture_output=True, text=True,
        cwd=str(BASE_DIR), env=env, timeout=120,
    )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-500:]


class SchedulerLockTests(SimpleTestCase):
    def test_only_one_process_can_hold_the_lock_and_it_passes_on(self):
        code = "import sys; from horilla.scheduler_guard import _try_lock; print(_try_lock(open(sys.argv[1], 'a+')))"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "lock")
            first = open(path, "a+")
            self.assertTrue(_try_lock(first))
            self.assertEqual(_run_python(code, path), "False")  # a second worker is refused
            first.close()  # the owner exits
            self.assertEqual(_run_python(code, path), "True")  # the next one takes over

    def test_a_scheduler_started_while_booting_is_skipped_unless_we_own_the_lock(self):
        code = """
import sys
import horilla.scheduler_guard as g
from apscheduler.schedulers.background import BackgroundScheduler
g.install()
g.owns_scheduler_lock = lambda: sys.argv[1] == "owner"
s = BackgroundScheduler()
s.start()
print(s.running)
if s.running:
    s.shutdown(wait=False)
"""
        self.assertEqual(_run_python(code, "other"), "False")
        self.assertEqual(_run_python(code, "owner"), "True")

    def test_server_entry_points_install_it(self):
        for relative in ("horilla/asgi.py", "horilla/wsgi.py"):
            self.assertIn("run_scheduled_jobs_once()", (BASE_DIR / relative).read_text(encoding="utf-8"))
