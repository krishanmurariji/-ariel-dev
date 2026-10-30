"""
The profile pop-up in the top bar: Change Username / Change Password are always
listed; My Profile only while the selected company is the user's own (or "All"),
because employee_profile() sends the user away otherwise.
"""

from django.test import Client
from django.urls import reverse

from base.models import Company
from base.test_company_switcher_scoping import _make_company
from employee.models import EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee


class ProfileMenuTests(IsolatedTestCase):
    def setUp(self):
        self.own = _make_company("Menu Own Co")
        self.other = _make_company("Menu Other Co")
        user, employee = make_employee("profile_menu_user")
        EmployeeWorkInformation.objects.update_or_create(
            employee_id=employee, defaults={"company_id": self.own}
        )
        grant(user, "base", "change_company")
        self.client = Client(raise_request_exception=False)
        self.client.force_login(type(user).objects.get(pk=user.pk))

    def page(self, selected):
        session = self.client.session
        session["selected_company"] = selected
        session.save()
        return self.client.get(reverse("dashboard")).content.decode()

    def test_other_company_selected_still_lists_username_and_password(self):
        html = self.page(str(self.other.pk))
        self.assertIn(reverse("change-username"), html)
        self.assertIn(reverse("change-password"), html)
        self.assertNotIn(f'href="{reverse("employee-profile")}"', html)

    def test_own_company_selected_lists_everything(self):
        html = self.page(str(self.own.pk))
        self.assertIn(f'href="{reverse("employee-profile")}"', html)
        self.assertIn(reverse("change-username"), html)
        self.assertIn(reverse("change-password"), html)

    def test_all_companies_lists_everything(self):
        html = self.page("all")
        self.assertIn(f'href="{reverse("employee-profile")}"', html)
        self.assertIn(reverse("change-password"), html)
