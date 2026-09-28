"""
Regression tests for two security fixes that previously had no automated test:

* Two-factor login. The original `user_otp == otp` let a blank POST verify
  (None == None) whenever no code had been issued yet, and there was no
  limit on wrong guesses.
* The quick-export endpoint, which let any logged-in user read any model
  (including password hashes) for any company. It is disabled.
"""

import time
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.urls import reverse

from base.methods import generate_otp
from employee.models import Employee
from horilla.isolated_testcase import IsolatedTestCase
from horilla_auth.models import HorillaUser
from horilla_views.generic.cbv.views import HorillaListView


def make_user(username, **extra):
    user = HorillaUser.objects.create(
        username=username,
        password=make_password("Passw0rd!x"),
        is_active=True,
        **extra,
    )
    Employee.objects.create(
        employee_first_name="Test",
        employee_last_name=username,
        employee_user_id=user,
        email=f"{username}@example.com",
    )
    return user


class TwoFactorTests(IsolatedTestCase):
    URL = "/two-factor/"

    def setUp(self):
        cache.clear()
        self.user = make_user("tf_user")
        self.client.force_login(self.user)

    def _issue_code(self, code="123456", age_seconds=0):
        session = self.client.session
        session["otp_code"] = code
        session["otp_code_timestamp"] = time.time() - age_seconds
        session["otp_code_verified"] = False
        session.save()

    def _verified(self):
        return bool(self.client.session.get("otp_code_verified"))

    def test_blank_post_does_not_verify_when_no_code_was_ever_issued(self):
        # The original bypass: nothing issued (None) == nothing submitted (None).
        for data in ({}, {"otp": ""}, {"otp": "   "}):
            with self.subTest(data=data):
                self.client.post(self.URL, data)
                self.assertFalse(self._verified())

    def test_missing_field_does_not_verify_when_a_code_is_pending(self):
        self._issue_code()
        self.client.post(self.URL, {})
        self.assertFalse(self._verified())

    def test_wrong_code_is_rejected(self):
        self._issue_code("123456")
        self.client.post(self.URL, {"otp": "654321"})
        self.assertFalse(self._verified())

    def test_correct_code_verifies_and_redirects(self):
        self._issue_code("123456")
        response = self.client.post(self.URL, {"otp": "123456"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")
        self.assertTrue(self._verified())
        self.assertIsNone(self.client.session.get("otp_code"))  # single use

    def test_correct_code_with_surrounding_spaces_still_verifies(self):
        self._issue_code("123456")
        self.client.post(self.URL, {"otp": " 123456 "})
        self.assertTrue(self._verified())

    def test_an_expired_code_is_refused_even_if_it_matches(self):
        self._issue_code("123456", age_seconds=601)
        self.client.post(self.URL, {"otp": "123456"})
        self.assertFalse(self._verified())

    def test_three_wrong_guesses_lock_out_even_the_right_code(self):
        self._issue_code("123456")
        for _ in range(3):
            self.client.post(self.URL, {"otp": "000000"})
        self.client.post(self.URL, {"otp": "123456"})
        self.assertFalse(self._verified())

    def test_two_wrong_guesses_do_not_lock_out(self):
        self._issue_code("123456")
        for _ in range(2):
            self.client.post(self.URL, {"otp": "000000"})
        self.client.post(self.URL, {"otp": "123456"})
        self.assertTrue(self._verified())


class OtpGenerationTests(IsolatedTestCase):
    def test_codes_are_six_digits(self):
        for _ in range(500):
            code = generate_otp()
            self.assertRegex(code, r"^[1-9][0-9]{5}$")

    def test_codes_are_not_all_the_same(self):
        self.assertGreater(len({generate_otp() for _ in range(50)}), 25)

    def test_uses_a_cryptographic_source_not_the_random_module(self):
        with patch("base.methods.secrets.randbelow", return_value=0) as secure, patch(
            "base.methods.random.randint", side_effect=AssertionError("random used")
        ):
            self.assertEqual(generate_otp(), "100000")
        secure.assert_called_once_with(900000)


class ExportDisabledTests(IsolatedTestCase):
    def setUp(self):
        self.user = make_user("exp_admin", is_superuser=True, is_staff=True)
        self.client.force_login(self.user)

    def _post(self, model, format_="csv"):
        url = reverse("export-list", kwargs={"short_id": "abc"}) + f"?model={model}"
        return self.client.post(
            url,
            {
                "ids": f"[{self.user.pk}]",
                "columns": '[["Password","password"],["Name","username"]]',
                "format": format_,
                "export_file_name": "x",
            },
        )

    def test_endpoint_refuses_and_returns_no_data_for_any_model(self):
        for model in (
            "employee.Employee",
            "horilla_auth.HorillaUser",
            "payroll.Payslip",
            "not_an_app.Nothing",
        ):
            for format_ in ("csv", "xlsx", "pdf"):
                with self.subTest(model=model, format=format_):
                    response = self._post(model, format_)
                    self.assertEqual(response.status_code, 503)
                    body = response.content.decode("utf-8", "ignore")
                    self.assertNotIn("pbkdf2", body)  # password hash
                    self.assertNotIn("exp_admin", body)
                    self.assertNotIn("attachment", response.get("Content-Disposition", ""))

    def test_the_quick_export_button_is_off_unless_a_view_opts_in(self):
        self.assertIs(HorillaListView.quick_export, False)

    def test_the_url_name_still_resolves(self):
        # Every list page builds this URL unconditionally; if it stopped
        # resolving, every list page in the app would crash.
        self.assertTrue(reverse("export-list", kwargs={"short_id": "abc"}))
