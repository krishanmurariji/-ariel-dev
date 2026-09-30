"""
M-4: ACCESSIBILITY_CACHE_USER_KEYS was a module-level Python dict tracking
every session-based accessibility cache key ever seen, appended to on every
cache miss and never trimmed - unbounded process memory growth, and (since
the DatabaseCache it tracked keys for is DB-backed and survives a worker
restart, but this plain dict does not) a real staleness bug too: after a
restart the dict was empty, so DefaultAccessibility changes could no longer
find the still-live cached rows from before the restart to invalidate them.

Replaced with one cache key per employee (accessibility_cache_key()) -
check_is_accessible()'s answer only ever depends on the employee, never on
which session/browser asked, so there was never a reason to key by session
in the first place. No side index needed: the key to invalidate is always
derivable directly from the employee id.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accessibility.middlewares import accessibility_cache_key, update_accessibility_cache
from accessibility.models import ACCESSBILITY_FEATURE, DefaultAccessibility
from accessibility.signals import (
    _clear_accessibility_cache_for_all_default_accessibility_employees,
)
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation


class Repro(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("access_mw_user")
        self.employee = Employee.objects.get(employee_user_id=self.user)
        self.client = Client()
        self.client.force_login(self.user)

    def test_visiting_a_page_populates_one_key_for_the_employee(self):
        self.client.get("/")
        self.assertIsNotNone(cache.get(accessibility_cache_key(self.employee.id)))

    def test_two_sessions_for_the_same_employee_share_one_cache_entry(self):
        other_client = Client()
        other_client.force_login(self.user)
        self.client.get("/")
        other_client.get("/")
        # Only one key exists for this employee at all - nothing session-
        # specific to even collide on.
        self.assertIsNotNone(cache.get(accessibility_cache_key(self.employee.id)))

    def test_a_work_information_save_clears_that_employees_cache_only(self):
        other_user = make_user("access_mw_other")
        other_employee = Employee.objects.get(employee_user_id=other_user)
        cache.set(accessibility_cache_key(self.employee.id), {"x": True})
        cache.set(accessibility_cache_key(other_employee.id), {"x": True})

        EmployeeWorkInformation.objects.update_or_create(employee_id=self.employee)

        self.assertIsNone(cache.get(accessibility_cache_key(self.employee.id)))
        self.assertIsNotNone(cache.get(accessibility_cache_key(other_employee.id)))

    def test_survives_a_process_restart_unlike_the_old_side_dict(self):
        # The old ACCESSIBILITY_CACHE_USER_KEYS dict would be empty here (a
        # fresh process never populated it), yet a stale row could still
        # exist in the DB-backed cache from "before". The new scheme needs
        # no dict at all - the key is derived, not looked up - so
        # invalidation still finds it.
        update_accessibility_cache(accessibility_cache_key(self.employee.id), self.employee)
        self.assertIsNotNone(cache.get(accessibility_cache_key(self.employee.id)))
        EmployeeWorkInformation.objects.update_or_create(employee_id=self.employee)
        self.assertIsNone(cache.get(accessibility_cache_key(self.employee.id)))


class DefaultAccessibilityChangeClearsEveryEmployeesCacheTests(TransactionTestCase):
    """
    TransactionTestCase, not IsolatedTestCase/TestCase: an earlier version
    of this test genuinely ran the clearing function in a real background
    thread, which needs real commits (TestCase's rollback-per-test isolation
    hides a test's own data from any connection but its own). That is no
    longer what either test below does (see each one's own docstring for
    why), but the commit-based isolation is harmless to keep. What *is*
    still needed from IsolatedTestCase's own docstring applies here too:
    _thread_locals.request lingers across tests, so it is reset the same
    way, by hand, since this class cannot also extend IsolatedTestCase
    (that one is TestCase-based).
    """

    def setUp(self):
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None

    def tearDown(self):
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None

    def test_the_clearing_function_itself_clears_every_employees_cache(self):
        """
        The function the signal hands to threading.Thread, called directly
        (proven separately, in test_the_signal_hands_off_to_that_function_in_a_thread
        below, to be what the signal actually uses). Called directly here
        rather than through a real background thread: this project's
        throwaway in-memory SQLite test database (see test_settings_sqlite's
        own docstring - used only because the local Postgres user lacks
        CREATEDB) uses SQLite's shared-cache mode, under which a second,
        independently-opened connection - exactly what a real OS thread
        gets - reproducibly cannot see the schema at all
        ("no such table: employee_employee"), confirmed by direct
        experiment. Real production runs on Postgres, where a second
        connection is entirely ordinary; nothing about that limitation is
        specific to this function or to real threads in general - it is
        specific to this one throwaway settings module's in-memory SQLite.
        """
        employee = Employee.objects.get(
            employee_user_id=make_user("access_mw_txn_user")
        )
        other_employee = Employee.objects.get(
            employee_user_id=make_user("access_mw_txn_other")
        )
        cache.set(accessibility_cache_key(employee.id), {"x": True})
        cache.set(accessibility_cache_key(other_employee.id), {"x": True})

        _clear_accessibility_cache_for_all_default_accessibility_employees()

        self.assertIsNone(cache.get(accessibility_cache_key(employee.id)))
        self.assertIsNone(cache.get(accessibility_cache_key(other_employee.id)))

    def test_the_signal_hands_off_to_that_function_in_a_thread(self):
        with patch("accessibility.signals.threading.Thread") as thread_cls:
            DefaultAccessibility.objects.create(
                feature=ACCESSBILITY_FEATURE[0][0], is_enabled=True, filter={}
            )
        self.assertEqual(
            thread_cls.call_args.kwargs["target"],
            _clear_accessibility_cache_for_all_default_accessibility_employees,
        )
        thread_cls.return_value.start.assert_called_once()


class ProfileEditAccessViewTests(TestCase):
    def test_toggling_access_refreshes_that_employees_cache_directly(self):
        cache.clear()
        admin = make_user("access_mw_admin", is_staff=True, is_superuser=True)
        target_user = make_user("access_mw_target")
        target_employee = Employee.objects.get(employee_user_id=target_user)

        feature = ACCESSBILITY_FEATURE[0][0]
        DefaultAccessibility.objects.create(feature=feature, is_enabled=True, filter={})
        cache.set(accessibility_cache_key(target_employee.id), {"stale": True})

        client = Client()
        client.force_login(admin)
        response = client.get(
            reverse("profile-edit-access", args=[target_employee.id]),
            {"feature": feature},
        )
        self.assertNotEqual(response.status_code, 500)
        cached = cache.get(accessibility_cache_key(target_employee.id))
        self.assertIsNotNone(cached)
        self.assertNotIn("stale", cached)
