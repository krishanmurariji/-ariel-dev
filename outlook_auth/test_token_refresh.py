"""
Outlook connection: what happens when Microsoft will not renew the login.

Production log: "AADSTS50076 ... you must use multi-factor authentication".
Microsoft (a tenant/conditional-access rule) refuses to renew the stored login
without a person signing in again - the app cannot change that, but it must
(1) know it is in that state, (2) say so clearly instead of a generic error,
(3) stop retrying every 50 minutes, and (4) recover by itself once fixed.
All calls to Microsoft here are mocked.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from base.models import Company
from employee.models import Employee
from horilla.isolated_testcase import IsolatedTestCase
from horilla_auth.models import HorillaUser
from outlook_auth import views
from outlook_auth.mailer import send_via_outlook
from outlook_auth.models import AzureApi
from outlook_auth.scheduler import refresh_outlook_auth_token
from outlook_auth.views import OutlookReauthRequired, refresh_outlook_token, send_outlook_email

MFA_ERROR = {
    "error": "interaction_required",
    "error_description": (
        "AADSTS50076: Due to a configuration change made by your administrator, or because "
        "you moved to a new location, you must use multi-factor authentication to access "
        "'00000003-0000-0000-c000-000000000000'.\r\nTrace ID: 1234-abcd\r\nCorrelation ID: 9999"
    ),
}


def reply(status, payload):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.text = str(payload)
    return response


_company_counter = [0]


def make_api(age=timedelta(days=2), **extra):
    _company_counter[0] += 1
    company = Company.objects.create(
        company=f"Acme {_company_counter[0]}", address="a", country="c", state="s", city="c", zip="1"
    )
    return AzureApi.objects.create(
        outlook_client_id="client-id",
        outlook_client_secret="client-secret",
        outlook_tenant_id="tenant",
        outlook_email="mailbox@example.com",
        outlook_display_name="Mailbox",
        outlook_redirect_uri="https://example.com/cb",
        company=company,
        token={"access_token": "old-access", "refresh_token": "r1", "expires_in": 3600},
        last_refreshed=timezone.now() - age,
        **extra,
    )


class ExpiryTests(IsolatedTestCase):
    def test_an_old_token_is_seen_as_expired_after_a_day_or_more(self):
        # .seconds wrapped every 24h: a token exactly 2 days old looked 0 seconds old.
        api = make_api(age=timedelta(days=2))
        self.assertTrue(api.is_token_expired())
        self.assertIn("Expired", str(api.token_expire()))

    def test_a_fresh_token_is_not_expired(self):
        api = make_api(age=timedelta(minutes=5))
        self.assertFalse(api.is_token_expired())
        self.assertEqual(api.token_expire(), "00:05/01:00")

    def test_expires_at_is_used_when_present(self):
        api = make_api(age=timedelta(minutes=5))
        api.token = {**api.token, "expires_at": timezone.now().timestamp() + 30}
        self.assertTrue(api.is_token_expired())  # inside the 2-minute safety margin

    def test_a_missing_expires_in_does_not_crash(self):
        api = make_api(age=timedelta(minutes=5))
        api.token = {"access_token": "a", "refresh_token": "r"}
        api.token_expire()
        api.is_token_expired()

    def test_reconnect_needed_is_shown_in_the_list_column(self):
        api = make_api(reauth_required=True)
        self.assertIn("Reconnect", str(api.token_expire()))


class RefreshTests(IsolatedTestCase):
    def test_success_stores_the_new_token_and_clears_any_flag(self):
        api = make_api(reauth_required=True, last_error="old problem")
        new = {"access_token": "new-access", "refresh_token": "r2", "expires_in": 3599, "token_type": "Bearer"}
        with patch("outlook_auth.views.requests.post", return_value=reply(200, new)) as post:
            refresh_outlook_token(api)
        api.refresh_from_db()
        self.assertEqual(api.token["access_token"], "new-access")
        self.assertEqual(api.token["refresh_token"], "r2")
        self.assertGreater(api.token["expires_at"], timezone.now().timestamp())
        self.assertFalse(api.reauth_required)
        self.assertEqual(api.last_error, "")
        sent = post.call_args.kwargs["data"]
        self.assertEqual(sent["grant_type"], "refresh_token")
        self.assertEqual(sent["refresh_token"], "r1")
        self.assertEqual(sent["client_id"], "client-id")

    def test_the_old_refresh_token_is_kept_when_none_is_returned(self):
        api = make_api()
        with patch("outlook_auth.views.requests.post", return_value=reply(200, {"access_token": "n", "expires_in": 3600})):
            refresh_outlook_token(api)
        api.refresh_from_db()
        self.assertEqual(api.token["refresh_token"], "r1")

    def test_mfa_required_is_recognised_kept_and_reported_clearly(self):
        api = make_api()
        with patch("outlook_auth.views.requests.post", return_value=reply(400, MFA_ERROR)):
            with self.assertRaises(OutlookReauthRequired) as caught:
                refresh_outlook_token(api)
        self.assertIn("AADSTS50076", str(caught.exception))
        api.refresh_from_db()
        self.assertTrue(api.reauth_required)
        self.assertIn("AADSTS50076", api.last_error)
        self.assertNotIn("Trace ID", api.last_error)
        self.assertIsNotNone(api.last_error_at)
        self.assertEqual(api.token["access_token"], "old-access")  # untouched

    def test_a_temporary_microsoft_error_does_not_demand_a_new_sign_in(self):
        api = make_api()
        payload = {"error": "temporarily_unavailable", "error_description": "Try again later."}
        with patch("outlook_auth.views.requests.post", return_value=reply(503, payload)):
            with self.assertRaises(RuntimeError):
                refresh_outlook_token(api)
        api.refresh_from_db()
        self.assertFalse(api.reauth_required)

    def test_no_stored_refresh_token_means_sign_in_again(self):
        api = make_api()
        api.token = {"access_token": "a"}
        with patch("outlook_auth.views.requests.post") as post:
            with self.assertRaises(OutlookReauthRequired):
                refresh_outlook_token(api)
        post.assert_not_called()


class RetryPolicyTests(IsolatedTestCase):
    def test_a_recently_failed_account_is_not_hammered(self):
        api = make_api(reauth_required=True, last_error="AADSTS50076: mfa", last_error_at=timezone.now())
        with patch("outlook_auth.views.requests.post") as post:
            with self.assertRaises(OutlookReauthRequired):
                views.get_valid_oauth_session(api)
        post.assert_not_called()

    def test_it_tries_again_after_a_while_so_it_can_recover_by_itself(self):
        api = make_api(
            reauth_required=True, last_error="AADSTS50076: mfa",
            last_error_at=timezone.now() - timedelta(minutes=31),
        )
        new = {"access_token": "back", "refresh_token": "r9", "expires_in": 3600}
        with patch("outlook_auth.views.requests.post", return_value=reply(200, new)) as post:
            views.get_valid_oauth_session(api)
        post.assert_called_once()
        api.refresh_from_db()
        self.assertFalse(api.reauth_required)

    def test_the_scheduled_job_skips_a_recently_failed_account(self):
        waiting = make_api(reauth_required=True, last_error="x", last_error_at=timezone.now())
        healthy = make_api(age=timedelta(minutes=40))
        seen = []
        with patch("outlook_auth.views.refresh_outlook_token", side_effect=lambda a: seen.append(a.pk)):
            refresh_outlook_auth_token.__wrapped__()
        self.assertIn(healthy.pk, seen)
        self.assertNotIn(waiting.pk, seen)

    def test_the_scheduled_job_survives_a_failing_account(self):
        make_api()
        with patch("outlook_auth.views.refresh_outlook_token", side_effect=OutlookReauthRequired("mfa")):
            with self.assertLogs("outlook_auth.scheduler", "WARNING"):
                refresh_outlook_auth_token.__wrapped__()


class SendingTests(IsolatedTestCase):
    DATA = {"message": {"subject": "Hi"}}

    def test_no_connection_returns_a_clean_failure_instead_of_crashing(self):
        # e.g. mail sent from a background thread: there is no request at all
        self.assertEqual(send_outlook_email(None, self.DATA), (None, self.DATA))
        api = make_api()
        api.token = {}
        api.save()
        self.assertEqual(send_outlook_email(None, self.DATA), (None, self.DATA))

    def test_needing_a_new_sign_in_returns_a_clean_failure(self):
        make_api()
        with patch("outlook_auth.views.get_valid_oauth_session", side_effect=OutlookReauthRequired("mfa")):
            self.assertEqual(send_outlook_email(None, self.DATA), (None, self.DATA))

    def test_success_returns_the_response_even_without_a_request(self):
        make_api()
        oauth = MagicMock()
        oauth.post.return_value.raise_for_status.return_value = None
        with patch("outlook_auth.views.get_valid_oauth_session", return_value=oauth):
            response, data = send_outlook_email(None, self.DATA)
        self.assertIs(response, oauth.post.return_value)

    def test_the_mailer_reports_the_reason_and_what_to_do(self):
        api = make_api()
        message = MagicMock()
        with patch("outlook_auth.mailer.get_valid_oauth_session", side_effect=OutlookReauthRequired("Microsoft: AADSTS50076")):
            self.assertEqual(send_via_outlook([message], company=api.company, fail_silently=True), 0)
            with self.assertRaises(RuntimeError) as caught:
                send_via_outlook([message], company=api.company)
        self.assertIn("AADSTS50076", str(caught.exception))
        self.assertIn("Login", str(caught.exception))


class SignInPageTests(IsolatedTestCase):
    def setUp(self):
        admin = HorillaUser.objects.create(username="ol_admin", is_active=True, is_staff=True, is_superuser=True)
        Employee.objects.create(employee_first_name="A", employee_last_name="B", employee_user_id=admin, email="ol@example.com")
        self.client = Client()
        self.client.force_login(admin)

    def test_login_with_no_account_configured_redirects_instead_of_crashing(self):
        response = self.client.get(reverse("outlook_login"))
        self.assertEqual(response.status_code, 302)

    def test_callback_with_no_account_configured_redirects_instead_of_crashing(self):
        response = self.client.get(reverse("outlook_callback"))
        self.assertEqual(response.status_code, 302)

    def test_manual_refresh_button_reports_mfa_and_does_not_crash(self):
        api = make_api()
        with patch("outlook_auth.views.requests.post", return_value=reply(400, MFA_ERROR)):
            response = self.client.get(reverse("refresh_outlook_token", args=[api.pk]))
        self.assertEqual(response.status_code, 302)
        api.refresh_from_db()
        self.assertTrue(api.reauth_required)
