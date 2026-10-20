"""
The Exit Process page (Under Review / Exits in Progress / Exited), its windows,
and the Exit Settings page.
"""

import datetime
import itertools

from django.contrib.auth.models import Permission
from django.test import override_settings
from django.urls import reverse

from base.models import Company
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase
from offboarding import exit_service
from offboarding.models import (
    EmployeeNoticePolicy,
    ExitActivity,
    ExitReasonOption,
    NoticePeriodPolicy,
    OffboardingEmployee,
    OffboardingGeneralSetting,
    ResignationLetter,
)

HX = {"HTTP_HX_REQUEST": "true"}
_n = itertools.count()

# Full-layout pages (index.html / settings.html) return a bare 400 for the
# minimal users these tests create unless DEBUG is on - the same pre-existing
# quirk found on the dashboard (MissingParameterMiddleware turns a KeyError
# in the shared layout into a 400 when DEBUG=False). The pages themselves are
# what is under test, so render them with DEBUG on.
def _full_page(test, url):
    with test.settings(DEBUG=True):
        return test.client.get(url)


# The themed 404 page can't resolve its static image under the test storage.
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
TODAY = datetime.date.today()


def _person(company=None, manager=None, perms=(), **user_extra):
    user = make_user(f"xpage_{next(_n):04d}", **user_extra)
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    employee = Employee.objects.get(employee_user_id=user)
    EmployeeWorkInformation.objects.entire().update_or_create(
        employee_id=employee,
        defaults={"company_id": company, "reporting_manager_id": manager},
    )
    return user, Employee.objects.get(pk=employee.pk)


def _letter(employee, **kw):
    data = dict(
        employee_id=employee,
        title="Resignation",
        description="<p>bye</p>",
        planned_to_leave_on=TODAY + datetime.timedelta(days=30),
        reason="Relocating",
        initiated_by=employee,
    )
    data.update(kw)
    return ResignationLetter.objects.create(**data)


class PageBase(IsolatedTestCase):
    def setUp(self):
        self.hr_user, self.hr = _person(is_staff=True, is_superuser=True)
        self.mgr_user, self.mgr = _person()
        self.emp_user, self.emp = _person(manager=self.mgr)
        self.other_user, self.other = _person()  # reports to nobody
        self.letter = _letter(self.emp)

    def tab(self, user, tab, **params):
        self.client.force_login(user)
        return self.client.get(reverse("exit-process-tab", args=[tab]), params, **HX)

    def accepted_case(self, employee=None, days=20):
        employee = employee or self.emp
        letter = self.letter if employee == self.emp else _letter(employee)
        return exit_service.review_request(
            letter, self.hr, "accept", "ok",
            last_working_day=TODAY + datetime.timedelta(days=days),
        )


class AccessTests(PageBase):
    def test_who_can_open_the_page(self):
        for user, ok in ((self.hr_user, True), (self.mgr_user, True), (self.emp_user, False)):
            self.client.force_login(user)
            response = _full_page(self, reverse("exit-process"))
            self.assertEqual(response.status_code == 200, ok, user)

    def test_page_shows_the_counts(self):
        self.client.force_login(self.hr_user)
        html = _full_page(self, reverse("exit-process")).content.decode()
        self.assertIn("Under Review", html)
        self.assertIn("Exits in Progress", html)
        self.assertIn("Exited Employees", html)

    def test_hr_sees_every_request_a_manager_only_their_reports(self):
        _letter(self.other)
        hr_html = self.tab(self.hr_user, "review").content.decode()
        mgr_html = self.tab(self.mgr_user, "review").content.decode()
        self.assertIn(self.emp.get_full_name(), hr_html)
        self.assertIn(self.other.get_full_name(), hr_html)
        self.assertIn(self.emp.get_full_name(), mgr_html)
        self.assertNotIn(self.other.get_full_name(), mgr_html)

    def test_an_employee_cannot_read_the_tab_fragments(self):
        response = self.tab(self.emp_user, "review")
        self.assertNotIn(self.emp.get_full_name(), response.content.decode())
        self.assertEqual(response.status_code, 302)

    def test_search_and_type_filters(self):
        _letter(self.other, exit_type="termination")
        by_name = self.tab(self.hr_user, "review", q=self.other.employee_last_name).content.decode()
        self.assertIn(self.other.get_full_name(), by_name)
        self.assertNotIn(self.emp.get_full_name(), by_name)
        terminations = self.tab(self.hr_user, "review", type="termination").content.decode()
        self.assertIn(self.other.get_full_name(), terminations)
        self.assertNotIn(self.emp.get_full_name(), terminations)

    def test_each_tab_lists_the_right_people(self):
        case = self.accepted_case()
        self.assertNotIn(self.emp.get_full_name(), self.tab(self.hr_user, "review").content.decode())
        self.assertIn(self.emp.get_full_name(), self.tab(self.hr_user, "progress").content.decode())
        self.assertNotIn(self.emp.get_full_name(), self.tab(self.hr_user, "exited").content.decode())
        exit_service.mark_exited(case)
        self.assertNotIn(self.emp.get_full_name(), self.tab(self.hr_user, "progress").content.decode())
        self.assertIn(self.emp.get_full_name(), self.tab(self.hr_user, "exited").content.decode())

    def test_unknown_tab_is_a_404(self):
        self.assertEqual(self.tab(self.hr_user, "nonsense").status_code, 404)


class ReviewWindowTests(PageBase):
    def url(self):
        return reverse("exit-review", args=[self.letter.pk])

    def post(self, user, **data):
        self.client.force_login(user)
        payload = {"decision": "accept", "last_working_day": (TODAY + datetime.timedelta(days=25)).isoformat(),
                   "rehire_eligible": "yes", "comment": "Looks fine"}
        payload.update(data)
        return self.client.post(self.url(), payload, **HX)

    def test_window_opens_for_hr_and_for_the_reporting_manager_only(self):
        for user, ok in ((self.hr_user, True), (self.mgr_user, True), (self.other_user, False)):
            self.client.force_login(user)
            body = self.client.get(self.url(), **HX).content.decode()
            self.assertEqual("Submit review" in body, ok, user)

    def test_accepting_refreshes_the_page_and_starts_the_exit(self):
        response = self.post(self.mgr_user)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Refresh"], "true")
        case = OffboardingEmployee.objects.entire().get(employee_id=self.emp)
        self.assertEqual(case.status, "in_progress")
        self.assertTrue(case.rehire_eligible)
        self.assertEqual(ExitActivity.objects.entire().get(action="accepted").performed_by, self.mgr)

    def test_a_comment_is_required_and_shown_as_an_error(self):
        response = self.post(self.hr_user, comment="")
        self.assertEqual(response.status_code, 200)
        self.assertIn("A comment is required", response.content.decode())
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "requested")

    def test_accepting_needs_a_date(self):
        response = self.post(self.hr_user, last_working_day="")
        self.assertIn("Please choose the last working day", response.content.decode())

    def test_reject_and_retain(self):
        self.post(self.hr_user, decision="reject")
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "rejected")
        second = _letter(self.emp)
        self.client.post(
            reverse("exit-review", args=[second.pk]),
            {"decision": "retain", "comment": "Counter offer"}, **HX,
        )
        second.refresh_from_db()
        self.assertEqual(second.status, "retained")

    def test_a_stranger_cannot_review_by_posting(self):
        response = self.post(self.other_user)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "requested")
        self.assertNotEqual(response.status_code, 204)

    def test_reviewing_twice_shows_a_message(self):
        self.post(self.hr_user)
        response = self.post(self.hr_user)
        self.assertIn("already been reviewed", response.content.decode())


class InitiateWindowTests(PageBase):
    def payload(self, employee, **kw):
        data = {
            "employee": employee.pk, "exit_type": "termination", "reason": "Misconduct",
            "discussed": "yes", "discussion_summary": "Spoke to them.",
            "notice_date": TODAY.isoformat(), "last_working_day": "",
            "rehire_eligible": "no", "rehire_comment": "",
        }
        data.update(kw)
        return data

    def test_hr_initiating_starts_the_exit_at_once_using_the_notice_period(self):
        self.client.force_login(self.hr_user)
        response = self.client.post(reverse("exit-initiate"), self.payload(self.other), **HX)
        self.assertEqual(response.status_code, 204)
        case = OffboardingEmployee.objects.entire().get(employee_id=self.other)
        self.assertEqual(case.status, "in_progress")
        self.assertEqual(case.exit_type, "termination")
        self.assertEqual(case.last_working_day, TODAY + datetime.timedelta(days=30))
        self.assertFalse(case.rehire_eligible)
        self.assertTrue(ExitActivity.objects.entire().filter(action="initiated").exists())

    def test_a_manager_initiating_goes_under_review(self):
        _, report = _person(manager=self.mgr)
        self.client.force_login(self.mgr_user)
        response = self.client.post(
            reverse("exit-initiate"),
            self.payload(report, exit_type="resignation", reason="Relocating"),
            **HX,
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            ResignationLetter.objects.entire().filter(employee_id=report, status="requested").count(), 1
        )
        self.assertFalse(OffboardingEmployee.objects.entire().filter(employee_id=report).exists())

    def test_a_manager_can_only_pick_their_own_reports(self):
        self.client.force_login(self.mgr_user)
        response = self.client.post(reverse("exit-initiate"), self.payload(self.other), **HX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ResignationLetter.objects.entire().filter(employee_id=self.other).exists())

    def test_the_reason_must_belong_to_the_chosen_type(self):
        self.client.force_login(self.hr_user)
        response = self.client.post(reverse("exit-initiate"), self.payload(self.other, reason="Relocating"), **HX)
        self.assertIn("Please select a reason from the list", response.content.decode())

    def test_script_like_text_is_refused(self):
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("exit-initiate"), self.payload(self.other, discussion_summary="<script>alert(1)</script>"), **HX
        )
        self.assertIn("not allowed", response.content.decode())
        self.assertFalse(OffboardingEmployee.objects.entire().exists())

    def test_someone_already_leaving_is_not_offered(self):
        self.accepted_case()
        self.client.force_login(self.hr_user)
        response = self.client.post(reverse("exit-initiate"), self.payload(self.emp), **HX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OffboardingEmployee.objects.entire().filter(employee_id=self.emp).count(), 1)


class RunningExitWindowTests(PageBase):
    def setUp(self):
        super().setUp()
        self.case = self.accepted_case()

    def test_summary_shows_history_to_hr_and_the_manager_not_to_others(self):
        for user, ok in ((self.hr_user, True), (self.mgr_user, True), (self.other_user, False)):
            self.client.force_login(user)
            body = self.client.get(reverse("exit-summary", args=[self.case.pk]), **HX).content.decode()
            self.assertEqual("History" in body, ok, user)

    def test_changing_the_date_needs_hr_and_a_reason(self):
        url = reverse("exit-change-date", args=[self.case.pk])
        new = (TODAY + datetime.timedelta(days=40)).isoformat()
        self.client.force_login(self.mgr_user)
        self.client.post(url, {"last_working_day": new, "reason": "x"}, **HX)
        self.case.refresh_from_db()
        self.assertNotEqual(self.case.last_working_day.isoformat(), new)

        self.client.force_login(self.hr_user)
        bad = self.client.post(url, {"last_working_day": new, "reason": ""}, **HX)
        self.assertIn("A reason is required", bad.content.decode())
        ok = self.client.post(url, {"last_working_day": new, "reason": "Handover"}, **HX)
        self.assertEqual(ok.status_code, 204)
        self.case.refresh_from_db()
        self.assertEqual(self.case.last_working_day.isoformat(), new)

    def test_cancelling(self):
        url = reverse("exit-cancel", args=[self.case.pk])
        self.client.force_login(self.hr_user)
        self.assertIn("A reason is required", self.client.post(url, {"reason": " "}, **HX).content.decode())
        self.assertEqual(self.client.post(url, {"reason": "Stays"}, **HX).status_code, 204)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, "cancelled")

    def test_deactivate_is_hr_only_and_only_once_exited(self):
        url = reverse("exit-deactivate", args=[self.case.pk])
        self.client.force_login(self.hr_user)
        self.client.post(url)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.is_active)  # still in progress -> refused

        exit_service.mark_exited(self.case)
        self.client.force_login(self.mgr_user)
        self.client.post(url)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.is_active)  # a manager may not

        self.client.force_login(self.hr_user)
        self.client.post(url)
        self.emp.refresh_from_db()
        self.emp_user.refresh_from_db()
        self.assertFalse(self.emp.is_active)
        self.assertFalse(self.emp_user.is_active)


class ReminderAndWithdrawTests(PageBase):
    def test_hr_can_remind_the_reviewer(self):
        self.client.force_login(self.hr_user)
        self.client.post(reverse("exit-remind", args=[self.letter.pk]))
        self.assertTrue(ExitActivity.objects.entire().filter(action="reminder").exists())

    def test_the_reminder_is_also_emailed_to_the_reviewer(self):
        from django.core import mail

        self.mgr.email = "reviewer@example.com"
        self.mgr.save()
        self.client.force_login(self.hr_user)
        response = self.client.post(reverse("exit-remind", args=[self.letter.pk]), follow=True)
        sent = [m for m in mail.outbox if "reviewer@example.com" in m.to]
        self.assertEqual(len(sent), 1)
        self.assertIn("waiting for your review", sent[0].subject)
        self.assertIn("by notification and email", " ".join(str(m) for m in response.context["messages"]))

    def test_a_failed_email_still_sends_the_notification_and_says_so(self):
        self.mgr.email = ""
        self.mgr.save()
        self.client.force_login(self.hr_user)
        response = self.client.post(reverse("exit-remind", args=[self.letter.pk]), follow=True)
        self.assertTrue(ExitActivity.objects.entire().filter(action="reminder").exists())
        self.assertIn("email could not be sent", " ".join(str(m) for m in response.context["messages"]))

    def test_a_manager_cannot_send_reminders(self):
        self.client.force_login(self.mgr_user)
        self.client.post(reverse("exit-remind", args=[self.letter.pk]))
        self.assertFalse(ExitActivity.objects.entire().filter(action="reminder").exists())

    def test_the_employee_can_withdraw_their_own_but_not_someone_elses(self):
        self.client.force_login(self.other_user)
        self.client.post(reverse("exit-withdraw", args=[self.letter.pk]))
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "requested")

        self.client.force_login(self.emp_user)
        self.client.post(reverse("exit-withdraw", args=[self.letter.pk]))
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "withdrawn")


class ExitSettingsPageTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Co", address="a", country="c", state="s", city="c", zip="1"
        )
        self.admin_user, _ = _person(is_staff=True, is_superuser=True)
        self.plain_user, self.plain = _person()
        self.client.force_login(self.admin_user)

    def scope(self, value):
        session = self.client.session
        session["selected_company"] = value
        session.save()

    def test_only_those_with_the_permission_open_it(self):
        self.assertEqual(_full_page(self, reverse("exit-settings")).status_code, 200)
        self.client.force_login(self.plain_user)
        self.assertNotEqual(_full_page(self, reverse("exit-settings")).status_code, 200)

    def test_the_page_lists_the_starter_reasons(self):
        body = _full_page(self, reverse("exit-settings")).content.decode()
        self.assertIn("Relocating", body)
        self.assertIn("Misconduct", body)

    def test_saving_the_rules(self):
        self.client.post(
            reverse("exit-settings-save"),
            {"resignation_request": "on", "review_mode": "acknowledgement",
             "last_working_day_holiday_rule": "after"},
        )
        row = OffboardingGeneralSetting.objects.entire().get(company_id__isnull=True)
        self.assertTrue(row.resignation_request)
        self.assertFalse(row.allow_withdrawal)  # unticked box = off
        self.assertEqual(row.review_mode, "acknowledgement")
        self.assertEqual(row.last_working_day_holiday_rule, "after")

    def test_reasons_add_edit_delete_and_no_duplicates(self):
        self.client.post(reverse("exit-reason-save"), {"title": "Health", "exit_type": "resignation"})
        reason = ExitReasonOption.objects.entire().get(title="Health")
        self.client.post(reverse("exit-reason-save"), {"title": "health", "exit_type": "resignation"})
        self.assertEqual(ExitReasonOption.objects.entire().filter(title__iexact="health").count(), 1)
        self.client.post(reverse("exit-reason-update", args=[reason.pk]), {"title": "Health issues", "exit_type": "resignation"})
        reason.refresh_from_db()
        self.assertEqual(reason.title, "Health issues")
        self.client.post(reverse("exit-reason-delete", args=[reason.pk]))
        self.assertFalse(ExitReasonOption.objects.entire().filter(pk=reason.pk).exists())

    @override_settings(STORAGES=PLAIN_STATIC)
    def test_inside_a_company_global_reasons_are_read_only(self):
        global_reason = ExitReasonOption.objects.entire().get(title="Relocating", exit_type="resignation")
        self.scope(str(self.company.pk))
        response = self.client.post(reverse("exit-reason-delete", args=[global_reason.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ExitReasonOption.objects.entire().filter(pk=global_reason.pk).exists())
        self.client.post(reverse("exit-reason-save"), {"title": "Company only", "exit_type": "resignation"})
        mine = ExitReasonOption.objects.entire().get(title="Company only")
        self.assertEqual(mine.company_id_id, self.company.pk)
        self.assertIn(
            "Company only",
            list(ExitReasonOption.for_company("resignation", self.company.pk).values_list("title", flat=True)),
        )
        self.assertNotIn(
            "Company only",
            list(ExitReasonOption.for_company("resignation", None).values_list("title", flat=True)),
        )

    def test_policies_add_default_assign_and_protect(self):
        self.client.post(reverse("notice-policy-save"), {"title": "Standard", "days": 90, "description": "d", "is_default": "on"})
        policy = NoticePeriodPolicy.objects.entire().get(title="Standard")
        self.assertTrue(policy.is_default)
        self.client.post(reverse("notice-policy-save"), {"title": "standard", "days": 10})
        self.assertEqual(NoticePeriodPolicy.objects.entire().count(), 1)

        self.client.post(reverse("notice-policy-assign"), {"policy": policy.pk, "employees": [self.plain.pk]})
        self.assertEqual(EmployeeNoticePolicy.objects.entire().get(employee_id=self.plain).policy_id, policy)
        self.client.post(reverse("notice-policy-delete", args=[policy.pk]))
        self.assertTrue(NoticePeriodPolicy.objects.entire().filter(pk=policy.pk).exists())  # in use

        self.client.post(reverse("notice-policy-assign"), {"policy": "", "employees": [self.plain.pk]})
        self.assertFalse(EmployeeNoticePolicy.objects.entire().exists())
        self.client.post(reverse("notice-policy-delete", args=[policy.pk]))
        self.assertFalse(NoticePeriodPolicy.objects.entire().filter(pk=policy.pk).exists())

    def test_old_rules_page_redirects_here(self):
        response = self.client.get(reverse("offboarding-rules-view"))
        self.assertRedirects(response, reverse("exit-settings"), fetch_redirect_response=False)


class TabToolsInTopBarTests(PageBase):
    """Controls that belong to one tab ride along with that tab into the top bar."""

    def test_each_tab_hands_its_own_controls_to_the_top_bar(self):
        tools = 'id="xpTabTools" hx-swap-oob="true"'
        # (the survey tab's Export to Excel button is switched off for now - see survey_tab.html)
        for tab, expected in (("tasks", "By employee"), ("survey", 'name="status"')):
            response = self.tab(self.hr_user, tab)
            self.assertContains(response, tools)
            self.assertContains(response, expected)
        self.assertNotContains(self.tab(self.hr_user, "survey"), "Export to Excel")
        for tab in ("review", "progress", "exited"):
            response = self.tab(self.hr_user, tab)
            self.assertContains(response, tools)  # empties the bar again
            self.assertNotContains(response, "By employee")
            self.assertNotContains(response, "Export to Excel")


class OffboardingMenuTests(PageBase):
    """The Offboarding entry shows only when it has something to open."""

    def shows(self, user):
        from django.test import RequestFactory

        from offboarding import sidebar

        request = RequestFactory().get("/")
        request.user = user
        request.session = {}
        return bool(sidebar.offboarding_accessibility(request, None, None))

    def test_hr_and_managers_with_reports_see_it(self):
        self.assertTrue(self.shows(self.hr_user))
        self.assertTrue(self.shows(self.mgr_user))  # self.emp reports to them

    def test_an_employee_who_is_leaving_but_has_nothing_to_do_does_not(self):
        self.accepted_case()  # self.emp's exit is now in progress
        self.assertFalse(self.shows(self.emp_user))

    def test_an_employee_with_a_survey_waiting_does_see_it(self):
        from offboarding import exit_survey
        from offboarding.models import ExitSurveyQuestion

        ExitSurveyQuestion.objects.create(question="Why?", type="text")
        case = self.accepted_case()
        exit_survey.send_survey(case, self.hr)
        self.assertTrue(self.shows(self.emp_user))
