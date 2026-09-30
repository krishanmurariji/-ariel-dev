"""
M-5: CompanyMiddleware unconditionally assigned into request.session on
every single authenticated request - Django marks a session modified (and
so writes a row to the session store) the instant anything is assigned
into it, even the exact value that was already there. It also ran the same
"does this company exist" query twice in a row for the same id (an
.exists() check, then .first() again a few lines later for that same id
inside _get_company_obj). Both fixed with no change to the values ending
up in the session or in request.selected_company_instance.
"""

from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from base.middleware import CompanyMiddleware
from base.models import Company
from base.test_security_regressions import make_user


class SessionOnlyWrittenWhenChangedTests(TestCase):
    def setUp(self):
        self.user = make_user("company_mw_user")
        self.client = Client()
        self.client.force_login(self.user)
        self.company = Company.objects.create(
            company="Acme", address="a", country="c", state="s", city="c", zip="1"
        )
        session = self.client.session
        session["selected_company"] = str(self.company.id)
        session.save()

    def test_the_second_of_two_identical_calls_does_not_assign_into_the_session(self):
        """
        Unit-level check on _set_company_session() itself (Client-level
        requests also pass through other middleware that may touch the
        session for unrelated reasons, which would make this flaky if
        checked indirectly through a real HTTP round trip).
        """
        from unittest.mock import patch

        request = RequestFactory().get("/")
        request.user = self.user
        request.session = SessionStore()
        request.session.create()

        middleware = CompanyMiddleware(lambda r: None)
        writes = []
        real_setitem = SessionStore.__setitem__

        def spy_setitem(session_self, key, value):
            writes.append(key)
            return real_setitem(session_self, key, value)

        with patch.object(SessionStore, "__setitem__", spy_setitem):
            middleware._set_company_session(request, self.company)
            first_call_writes = list(writes)
            writes.clear()
            middleware._set_company_session(request, self.company)
            second_call_writes = list(writes)

        self.assertIn("selected_company", first_call_writes)
        self.assertIn("selected_company_instance", first_call_writes)
        self.assertEqual(second_call_writes, [])

    def test_the_session_content_is_unchanged_by_not_rewriting_it(self):
        self.client.get("/")
        session = self.client.session
        self.assertEqual(session["selected_company"], str(self.company.id))
        self.assertEqual(session["selected_company_instance"]["id"], self.company.id)

    def test_switching_company_does_update_the_session(self):
        other = Company.objects.create(
            company="Other", address="a", country="c", state="s", city="c", zip="1"
        )
        self.client.get("/")
        session = self.client.session
        session["selected_company"] = str(other.id)
        session.save()
        self.client.get("/")
        session = self.client.session
        self.assertEqual(session["selected_company"], str(other.id))
        self.assertEqual(session["selected_company_instance"]["id"], other.id)


class DuplicateCompanyQueryRemovedTests(TestCase):
    def setUp(self):
        self.user = make_user("company_mw_query_user")
        self.client = Client()
        self.client.force_login(self.user)
        self.company = Company.objects.create(
            company="Acme2", address="a", country="c", state="s", city="c", zip="1"
        )
        session = self.client.session
        session["selected_company"] = str(self.company.id)
        session.save()

    def test_a_selected_company_is_looked_up_only_once_per_request(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/")
        company_lookups = [
            q for q in ctx.captured_queries
            if "base_company" in q["sql"] and str(self.company.id) in q["sql"]
        ]
        self.assertLessEqual(len(company_lookups), 1, ctx.captured_queries)


class DeletedSelectedCompanyFallsBackToTheUsersOwnTests(TestCase):
    """
    The session still points at a company id that no longer exists (it was
    deleted elsewhere) - CompanyMiddleware falls back to the user's own
    company in that case. Confirms that fallback still fires the same way
    after removing the duplicate query.
    """

    def test_falls_back_instead_of_crashing(self):
        from employee.models import Employee, EmployeeWorkInformation

        user = make_user("company_mw_deleted")
        own_company = Company.objects.create(
            company="Own", address="a", country="c", state="s", city="c", zip="1"
        )
        employee = Employee.objects.get(employee_user_id=user)
        EmployeeWorkInformation.objects.update_or_create(
            employee_id=employee, defaults={"company_id": own_company}
        )

        gone_id = 999999
        self.assertFalse(Company.objects.filter(id=gone_id).exists())

        client = Client()
        client.force_login(user)
        session = client.session
        session["selected_company"] = str(gone_id)
        session.save()

        response = client.get("/")
        self.assertNotEqual(response.status_code, 500)
        session = client.session
        self.assertEqual(session["selected_company_instance"]["id"], own_company.id)
