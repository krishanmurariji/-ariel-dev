"""
get_companies() (the sidebar's company-switcher context processor) used to
list every Company in the system for every logged-in user, regardless of
permission - so an ordinary employee, with one company set on their own
work information, would still see every other company's name and logo in
the sidebar dropdown. The only gate that ever existed was on the *action*
of switching (update_selected_company requires base.change_company) - the
listing itself was never gated at all.

Fixed by only building the full list (+ the "All Company" entry) for a
user who actually holds base.change_company - the same permission the
switch action already required - and showing everyone else just their own
assigned company, if any.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from base.context_processors import get_companies
from base.models import Company
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase


def _make_company(name):
    return Company.objects.create(
        company=name,
        address="a",
        country="c",
        state="s",
        city="c",
        zip="1",
        icon=SimpleUploadedFile(f"{name}.png", b"fake-icon-bytes"),
    )


class CompanySwitcherScopingTests(IsolatedTestCase):
    def setUp(self):
        self.own_company = _make_company("Own Co")
        self.other_company = _make_company("Other Co")

    def test_regular_employee_sees_only_their_own_company(self):
        from horilla_auth.models import HorillaUser

        user = make_user("switcher_regular_emp")
        employee = Employee.objects.get(employee_user_id=user)
        EmployeeWorkInformation.objects.update_or_create(
            employee_id=employee, defaults={"company_id": self.own_company}
        )

        # Re-fetch a clean user instance: `user` above still carries Django's
        # automatic reverse-relation cache from the moment Employee.employee_user_id
        # was first assigned inside make_user() (which happened before this
        # test set a company), same as request.user is always freshly loaded
        # from the session on a real request rather than reused in-process.
        user = HorillaUser.objects.get(pk=user.pk)

        request = RequestFactory().get("/")
        request.user = user
        request.session = self.client.session

        result = get_companies(request)

        names = [c[1] for c in result["all_companies"]]
        self.assertEqual(names, ["Own Co"])
        self.assertNotIn("Other Co", names)
        self.assertNotIn("All Company", names)

    def test_employee_with_no_work_info_sees_an_empty_list_not_an_error(self):
        user = make_user("switcher_no_work_info")

        request = RequestFactory().get("/")
        request.user = user
        request.session = self.client.session

        result = get_companies(request)

        self.assertEqual(result["all_companies"], [])

    def test_user_with_change_company_permission_sees_every_company(self):
        user = make_user("switcher_privileged", is_staff=True, is_superuser=True)

        request = RequestFactory().get("/")
        request.user = user
        request.session = self.client.session

        result = get_companies(request)

        names = [c[1] for c in result["all_companies"]]
        self.assertIn("Own Co", names)
        self.assertIn("Other Co", names)
        self.assertIn("All Company", names)

    def test_anonymous_user_does_not_crash_and_sees_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = self.client.session

        result = get_companies(request)

        self.assertEqual(result["all_companies"], [])

    def test_regular_employee_full_page_render_does_not_leak_other_company_names(self):
        """
        End-to-end through a real logged-in page render (not just the
        context processor in isolation), matching how the sidebar
        actually renders this data in production.
        """
        user = make_user("switcher_e2e_emp")
        employee = Employee.objects.get(employee_user_id=user)
        EmployeeWorkInformation.objects.update_or_create(
            employee_id=employee, defaults={"company_id": self.own_company}
        )
        self.client.force_login(user)

        # DEBUG=True here: a pre-existing, unrelated quirk in this specific
        # test employee's dashboard render path raises a KeyError under
        # DEBUG=False (MissingParameterMiddleware then converts it to a
        # bare 400 with no detail) - confirmed present with the *original*
        # get_companies() too, before this fix touched anything, so it's
        # not this fix's bug to carry. DEBUG=True skips that middleware's
        # conversion and the same request renders normally either way,
        # which is what actually exercises the real render path this test
        # cares about.
        with self.settings(DEBUG=True):
            response = self.client.get("/", follow=True)

        content = response.content.decode(errors="ignore")
        self.assertIn("Own Co", content)
        self.assertNotIn("Other Co", content)
