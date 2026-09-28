"""
Regression tests for the login brute-force protection. Each of these guards
something that was found broken by exercising the real thing rather than by
reading the code:

* DRF's stock throttle keyed an anonymous caller by the whole
  client-supplied X-Forwarded-For header, so a different fake value on every
  request dodged the login limit entirely.
* django-axes only reads its IP settings when the optional django-ipware
  package is installed; without it every client behind the proxy was
  recorded as the proxy's own 127.0.0.1, collapsing the (username, IP)
  lockout into one bucket per username.
* A locked-out caller got the same 401 "Invalid credentials" as a wrong
  password.
"""

import json

from axes.models import AccessAttempt
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from employee.models import Employee
from horilla_auth.models import HorillaUser
from horilla.horilla_middlewares import _thread_locals

class IsolatedTestCase(TestCase):
    """
    CompanyMiddleware stores the current request in a context variable that
    is never cleared, so in a full-suite run the *previous* test's request
    (and its user, which no longer exists) is still there when the next
    test saves a model - HorillaModel.save() then stamps modified_by with
    that stale user and SQLite rejects the dangling foreign key when the
    test ends. That made these tests pass alone and fail in the full run.
    Clear it before each test, and again afterwards so this test's own
    requests don't poison the next one either.
    """

    def _pre_setup(self):
        super()._pre_setup()
        _thread_locals.request = None

    def _post_teardown(self):
        _thread_locals.request = None
        super()._post_teardown()


LOGIN = "/api/auth/login/"


def post(client, username, password, real_ip, xff=None):
    extra = {"HTTP_X_REAL_IP": real_ip}
    if xff:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return client.post(
        LOGIN,
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
        **extra,
    )


class LoginThrottleTest(IsolatedTestCase):
    def setUp(self):
        cache.clear()

    def test_fake_x_forwarded_for_does_not_dodge_the_login_throttle(self):
        client = Client(REMOTE_ADDR="127.0.0.1")
        codes = [
            post(
                client, f"spray_{i}", "wrong", "203.0.113.50", xff=f"10.9.8.{i}"
            ).status_code
            for i in range(1, 15)
        ]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertTrue(all(code == 429 for code in codes[10:]), codes)

    def test_a_different_real_address_has_its_own_budget(self):
        client = Client(REMOTE_ADDR="127.0.0.1")
        for i in range(10):
            post(client, f"a_{i}", "wrong", "203.0.113.60")
        self.assertEqual(post(client, "b_1", "wrong", "203.0.113.61").status_code, 401)


@override_settings(AXES_ENABLED=True)
class LoginLockoutTest(IsolatedTestCase):
    def setUp(self):
        cache.clear()
        self.user = HorillaUser.objects.create(
            username="lockout_user",
            password=make_password("CorrectHorse!42"),
            is_active=True,
        )
        Employee.objects.create(
            employee_first_name="Lock",
            employee_last_name="Out",
            employee_user_id=self.user,
            email="lockout_user@example.com",
        )
        self.client = Client(REMOTE_ADDR="127.0.0.1")

    def test_lockout_is_per_username_and_real_client_address(self):
        locked_ip, other_ip = "203.0.113.110", "203.0.113.111"

        codes = [
            post(self.client, "lockout_user", "wrong", locked_ip).status_code
            for _ in range(5)
        ]
        self.assertEqual(codes, [401, 401, 401, 401, 429])

        # Recorded against the address the proxy reported, not the proxy's own.
        recorded = list(
            AccessAttempt.objects.filter(username="lockout_user").values_list(
                "ip_address", flat=True
            )
        )
        self.assertEqual(recorded, [locked_ip])

        # The right password is refused too while locked, and says why.
        locked = post(self.client, "lockout_user", "CorrectHorse!42", locked_ip)
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Too many failed login attempts", locked.json()["error"])

        # Someone else, elsewhere, is unaffected.
        ok = post(self.client, "lockout_user", "CorrectHorse!42", other_ip)
        self.assertEqual(ok.status_code, 200)
        self.assertIn("access", ok.json())
        self.assertIn("refresh", ok.json())

    def test_a_stranger_cannot_lock_out_someone_from_another_address(self):
        for _ in range(5):
            post(self.client, "lockout_user", "wrong", "198.51.100.7")
        legit = post(self.client, "lockout_user", "CorrectHorse!42", "203.0.113.200")
        self.assertEqual(legit.status_code, 200)
