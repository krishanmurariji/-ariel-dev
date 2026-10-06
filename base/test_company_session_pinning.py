"""
A regular employee's session could stay pinned to "All Company" (written at
first login while their work information had no company yet) even after a
company was assigned to them, so the sidebar kept saying "All Company" until
they logged out and in again. Only someone holding base.change_company can
ever have *chosen* a company other than their own, so for everyone else the
middleware now keeps the session on their own company.
"""

import itertools

from base.models import Company
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase

_counter = itertools.count()


def _company(name):
    return Company.objects.create(
        company=name, address="a", country="c", state="s", city="c", zip="1"
    )


def _login(client, company, **user_extra):
    user = make_user(f"pin_user_{next(_counter)}", **user_extra)
    employee = Employee.objects.get(employee_user_id=user)
    EmployeeWorkInformation.objects.update_or_create(
        employee_id=employee, defaults={"company_id": company}
    )
    client.force_login(user)
    return user, employee


def _pin(client, value):
    session = client.session
    session["selected_company"] = value
    session.save()


class RegularEmployeeSessionTests(IsolatedTestCase):
    def test_a_session_stuck_on_all_company_moves_to_their_own_company(self):
        x = _company("Co X")
        _login(self.client, x)
        _pin(self.client, "all")

        self.client.get("/")

        session = self.client.session
        self.assertEqual(session["selected_company"], str(x.id))
        self.assertEqual(session["selected_company_instance"]["id"], x.id)
        self.assertNotEqual(session["selected_company_instance"]["text"], "All companies")

    def test_company_assigned_after_login_takes_effect_on_the_next_request(self):
        x = _company("Co X")
        _, employee = _login(self.client, None)
        self.client.get("/")
        self.assertEqual(self.client.session["selected_company"], "all")

        EmployeeWorkInformation.objects.filter(employee_id=employee).update(
            company_id=x
        )
        self.client.get("/")

        self.assertEqual(self.client.session["selected_company"], str(x.id))

    def test_a_session_on_someone_elses_company_is_corrected(self):
        x = _company("Co X")
        y = _company("Co Y")
        _login(self.client, x)
        _pin(self.client, str(y.id))

        self.client.get("/")

        self.assertEqual(self.client.session["selected_company"], str(x.id))

    def test_an_employee_with_no_company_still_falls_back_to_all(self):
        _login(self.client, None)

        self.client.get("/")

        self.assertEqual(self.client.session["selected_company"], "all")


class PrivilegedUserKeepsTheirChoiceTests(IsolatedTestCase):
    def test_all_company_choice_is_kept(self):
        x = _company("Co X")
        _login(self.client, x, is_staff=True, is_superuser=True)
        _pin(self.client, "all")

        self.client.get("/")

        self.assertEqual(self.client.session["selected_company"], "all")

    def test_choice_of_another_company_is_kept(self):
        x = _company("Co X")
        y = _company("Co Y")
        _login(self.client, x, is_staff=True, is_superuser=True)
        _pin(self.client, str(y.id))

        self.client.get("/")

        self.assertEqual(self.client.session["selected_company"], str(y.id))
