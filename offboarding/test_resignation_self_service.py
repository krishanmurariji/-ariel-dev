"""
An ordinary employee (no offboarding permissions at all) must be able to open
the Resignation tab on their own profile and file their own resignation
whenever the feature is on for them - and only ever see their own letters.

Before this was fixed, three different feature-flag lookups disagreed about
whether the feature was on (the tab said yes, the Create form said no
whenever the session was on "All Company" or the setting was saved under a
company), and the tab's list/detail URLs served any employee's resignation
letter to any logged-in user.
"""

import datetime
import itertools

from django.contrib.auth.models import Permission
from django.urls import reverse

from base.models import Company
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase
from offboarding.models import OffboardingGeneralSetting, ResignationLetter

HX = {"HTTP_HX_REQUEST": "true"}
DENIED = "You Dont Have Permission"
_counter = itertools.count()


def _company(name):
    return Company.objects.create(
        company=name, address="a", country="c", state="s", city="c", zip="1"
    )


def _employee(company=None, **user_extra):
    user = make_user(f"resign_emp_{next(_counter)}", **user_extra)
    employee = Employee.objects.get(employee_user_id=user)
    EmployeeWorkInformation.objects.entire().update_or_create(
        employee_id=employee, defaults={"company_id": company}
    )
    return user, employee


def _set_session_company(client, value):
    session = client.session
    session["selected_company"] = value
    session.save()


def _setting(company, enabled):
    return OffboardingGeneralSetting.objects.create(
        resignation_request=enabled, company_id=company
    )


class CanFileOwnResignationTests(IsolatedTestCase):
    def _attempt(self, user, employee):
        """Open the tab, open the Create form, submit it. Returns
        (tab_denied, create_denied, letters_created)."""
        self.client.force_login(user)
        tab = self.client.get(
            reverse("individual-resignation-tab-list", kwargs={"pk": employee.pk}),
            **HX,
        )
        create = self.client.get(reverse("resignation-requests-create"), **HX)
        tab_denied = DENIED in tab.content.decode(errors="ignore")
        create_denied = DENIED in create.content.decode(errors="ignore")
        if not create_denied:
            self.client.post(
                reverse("resignation-requests-create"),
                {
                    "employee_id": employee.pk,
                    "title": "Resigning",
                    "description": "<p>Moving on</p>",
                    "planned_to_leave_on": "2030-01-31",
                },
                **HX,
            )
        return (
            tab_denied,
            create_denied,
            ResignationLetter.objects.filter(employee_id=employee).count(),
        )

    def test_global_setting_works_even_when_session_says_all_company(self):
        x = _company("Co X")
        _setting(None, True)
        user, employee = _employee(x)
        self.client.force_login(user)
        _set_session_company(self.client, "all")
        self.assertEqual(self._attempt(user, employee), (False, False, 1))

    def test_company_setting_works_even_when_session_says_all_company(self):
        # The reported case: tab shows, Create was refused.
        x = _company("Co X")
        _setting(x, True)
        user, employee = _employee(x)
        self.client.force_login(user)
        _set_session_company(self.client, "all")
        self.assertEqual(self._attempt(user, employee), (False, False, 1))

    def test_company_setting_with_session_on_that_company(self):
        x = _company("Co X")
        _setting(x, True)
        user, employee = _employee(x)
        self.client.force_login(user)
        _set_session_company(self.client, str(x.id))
        self.assertEqual(self._attempt(user, employee), (False, False, 1))

    def test_company_setting_with_fresh_session(self):
        x = _company("Co X")
        _setting(x, True)
        user, employee = _employee(x)
        self.assertEqual(self._attempt(user, employee), (False, False, 1))

    def test_global_setting_applies_to_an_employee_whose_company_has_no_row(self):
        x = _company("Co X")
        _setting(None, True)
        user, employee = _employee(x)
        self.assertEqual(self._attempt(user, employee), (False, False, 1))

    def test_a_companys_own_off_switch_beats_the_global_on(self):
        x = _company("Co X")
        _setting(None, True)
        _setting(x, False)
        user, employee = _employee(x)
        tab_denied, create_denied, created = self._attempt(user, employee)
        self.assertTrue(create_denied)
        self.assertEqual(created, 0)

    def test_company_less_employee_is_governed_by_the_global_setting_only(self):
        x = _company("Co X")
        _setting(x, True)  # enabled for X only
        user, employee = _employee(None)
        tab_denied, create_denied, created = self._attempt(user, employee)
        self.assertTrue(create_denied)
        self.assertEqual(created, 0)

        _setting(None, True)  # now enabled globally
        self.assertEqual(self._attempt(user, employee), (False, False, 1))

    def test_feature_off_denies_everything(self):
        x = _company("Co X")
        _setting(None, False)
        user, employee = _employee(x)
        tab_denied, create_denied, created = self._attempt(user, employee)
        self.assertTrue(tab_denied)
        self.assertTrue(create_denied)
        self.assertEqual(created, 0)


class ResignationTabPrivacyTests(IsolatedTestCase):
    def setUp(self):
        _setting(None, True)
        self.victim_user, self.victim = _employee()
        self.letter = ResignationLetter.objects.create(
            employee_id=self.victim,
            title="SECRET-VICTIM-TITLE",
            description="SECRET-VICTIM-BODY",
            planned_to_leave_on=datetime.date(2030, 1, 31),
        )
        self.list_url = reverse(
            "individual-resignation-tab-list", kwargs={"pk": self.victim.pk}
        )
        self.detail_url = reverse(
            "tab-resignation-requests-detail-view", kwargs={"pk": self.letter.pk}
        )

    def test_another_employee_cannot_read_the_list_or_the_detail(self):
        attacker, _ = _employee()
        self.client.force_login(attacker)

        listing = self.client.get(self.list_url, **HX).content.decode(errors="ignore")
        detail = self.client.get(self.detail_url, **HX).content.decode(errors="ignore")

        self.assertNotIn("SECRET-VICTIM", listing)
        self.assertNotIn("SECRET-VICTIM", detail)
        self.assertIn(DENIED, listing)
        self.assertIn(DENIED, detail)

    def test_the_employee_can_read_their_own(self):
        self.client.force_login(self.victim_user)

        listing = self.client.get(self.list_url, **HX).content.decode(errors="ignore")
        detail = self.client.get(self.detail_url, **HX).content.decode(errors="ignore")

        self.assertIn("SECRET-VICTIM-TITLE", listing)
        self.assertNotIn(DENIED, detail)

    def test_someone_with_the_view_permission_can_still_read_it(self):
        hr, _ = _employee()
        hr.user_permissions.add(
            Permission.objects.get(
                codename="view_resignationletter",
                content_type__app_label="offboarding",
            )
        )
        self.client.force_login(hr)

        listing = self.client.get(self.list_url, **HX).content.decode(errors="ignore")

        self.assertIn("SECRET-VICTIM-TITLE", listing)


class ResignationToggleTests(IsolatedTestCase):
    def _toggle(self, session_value, on):
        admin, _ = _employee(is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        _set_session_company(self.client, session_value)
        query = "?resignation_request=on" if on else ""
        self.client.get(reverse("enable-resignation-request") + query, **HX)

    def test_in_all_company_mode_the_toggle_edits_only_the_global_row(self):
        x = _company("Co X")
        company_row = _setting(x, False)

        self._toggle("all", on=True)

        company_row.refresh_from_db()
        self.assertFalse(company_row.resignation_request)
        global_row = OffboardingGeneralSetting.objects.entire().get(
            company_id__isnull=True
        )
        self.assertTrue(global_row.resignation_request)

    def test_in_company_mode_the_toggle_edits_that_companys_row(self):
        x = _company("Co X")
        _setting(None, False)

        self._toggle(str(x.id), on=True)

        self.assertTrue(
            OffboardingGeneralSetting.objects.entire()
            .get(company_id=x)
            .resignation_request
        )
        self.assertFalse(
            OffboardingGeneralSetting.objects.entire()
            .get(company_id__isnull=True)
            .resignation_request
        )
