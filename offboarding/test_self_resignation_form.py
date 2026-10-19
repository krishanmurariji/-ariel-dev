"""
The employee's own "Resign from job" form: reason, whether they spoke to their
manager (with a summary if so), an optional earlier last working day inside
the notice period, an extra comment - and an email to their reporting manager
when it is submitted.
"""

import datetime
import itertools
from unittest import mock

from django.contrib.auth.models import Permission
from django.core import mail
from django.urls import reverse

from base.models import Company
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase
from offboarding.models import OffboardingGeneralSetting, ResignationLetter
from payroll.models.models import PayrollGeneralSetting

HX = {"HTTP_HX_REQUEST": "true"}
_counter = itertools.count()
URL = "resignation-self-create"


def _employee(*, company=None, manager=None, **user_extra):
    user = make_user(f"selfresign_{next(_counter)}", **user_extra)
    employee = Employee.objects.get(employee_user_id=user)
    EmployeeWorkInformation.objects.entire().update_or_create(
        employee_id=employee,
        defaults={"company_id": company, "reporting_manager_id": manager},
    )
    return user, employee


class SelfResignationTestBase(IsolatedTestCase):
    # Days saved in the payroll general setting for the company. None = no
    # setting saved at all (the page default then applies); 0 = saved as "no
    # notice period".
    NOTICE_DAYS = 90

    def setUp(self):
        self.company = Company.objects.create(
            company="Co X", address="a", country="c", state="s", city="c", zip="1"
        )
        OffboardingGeneralSetting.objects.create(
            resignation_request=True, company_id=None
        )
        if self.NOTICE_DAYS is not None:
            PayrollGeneralSetting.objects.create(
                notice_period=self.NOTICE_DAYS, company_id=self.company
            )
        self.manager_user, self.manager = _employee(company=self.company)
        self.user, self.employee = _employee(
            company=self.company, manager=self.manager
        )
        self.client.force_login(self.user)
        days = 30 if self.NOTICE_DAYS is None else self.NOTICE_DAYS
        self.notice_end = datetime.date.today() + datetime.timedelta(days=days)

    def payload(self, **overrides):
        data = {
            "discussed_with_manager": "yes",
            "discussion_summary": "Spoke with my manager about the move.",
            "reason": "relocating",
            "early_last_working_day": "no",
            "preferred_last_working_day": "",
            "additional_comment": "Thank you for the opportunity.",
        }
        data.update(overrides)
        return data

    def submit(self, **overrides):
        return self.client.post(reverse(URL), self.payload(**overrides), **HX)

    def letters(self):
        return ResignationLetter.objects.filter(employee_id=self.employee)


class ModalContentTests(SelfResignationTestBase):
    def test_modal_shows_the_questions_and_the_notice_policy(self):
        body = self.client.get(reverse(URL), **HX).content.decode()

        self.assertIn("Resign from job", body)
        self.assertIn("always advisable to talk to your management", body)
        self.assertIn("Did you have discussion with manager", body)
        self.assertIn("Please select a reason for your resignation", body)
        self.assertIn("Explore Other Careers", body)
        self.assertIn("Retirement", body)
        self.assertIn("Any preference of an early last working day?", body)
        self.assertIn("subject to mutual agreement with the employer", body)
        self.assertIn("serve notice period of 90 days", body)
        self.assertIn(self.notice_end.strftime("%d %b %Y"), body)
        self.assertIn("Submit resignation", body)

    def test_the_profile_tab_create_button_opens_this_modal(self):
        tab = self.client.get(
            reverse(
                "individual-resignation-tab-list", kwargs={"pk": self.employee.pk}
            ),
            **HX,
        ).content.decode()
        self.assertIn(reverse(URL), tab)

    def test_the_hr_form_does_not_show_the_employee_only_fields(self):
        hr = make_user("selfresign_hr", is_staff=True, is_superuser=True)
        self.client.force_login(hr)
        body = self.client.get(reverse("resignation-requests-create"), **HX).content.decode()
        self.assertNotIn("Select reason", body)
        self.assertNotIn("name=\"discussion_summary\"", body)


class SubmissionTests(SelfResignationTestBase):
    def test_a_valid_submission_files_the_resignation_with_every_answer(self):
        response = self.submit()

        self.assertEqual(response.status_code, 200)
        letter = self.letters().get()
        self.assertEqual(letter.status, "requested")
        self.assertEqual(letter.reason, "relocating")
        self.assertTrue(letter.discussed_with_manager)
        self.assertEqual(letter.discussion_summary, "Spoke with my manager about the move.")
        self.assertIsNone(letter.preferred_last_working_day)
        self.assertEqual(letter.notice_period_days, 90)
        self.assertEqual(letter.notice_period_ends, self.notice_end)
        self.assertEqual(letter.planned_to_leave_on, self.notice_end)
        self.assertEqual(letter.additional_comment, "Thank you for the opportunity.")

    def test_not_having_spoken_to_the_manager_drops_any_summary(self):
        self.submit(discussed_with_manager="no", discussion_summary="ignored")
        letter = self.letters().get()
        self.assertFalse(letter.discussed_with_manager)
        self.assertEqual(letter.discussion_summary, "")

    def test_an_early_last_working_day_inside_the_notice_period_is_kept(self):
        early = datetime.date.today() + datetime.timedelta(days=30)
        self.submit(
            early_last_working_day="yes", preferred_last_working_day=early.isoformat()
        )
        letter = self.letters().get()
        self.assertEqual(letter.preferred_last_working_day, early)
        self.assertEqual(letter.planned_to_leave_on, early)
        self.assertEqual(letter.notice_period_ends, self.notice_end)

    def test_a_date_typed_while_early_is_set_to_no_is_ignored(self):
        early = datetime.date.today() + datetime.timedelta(days=30)
        self.submit(
            early_last_working_day="no", preferred_last_working_day=early.isoformat()
        )
        letter = self.letters().get()
        self.assertIsNone(letter.preferred_last_working_day)
        self.assertEqual(letter.planned_to_leave_on, self.notice_end)

    def test_it_is_always_filed_for_the_logged_in_employee(self):
        other_user, other = _employee(company=self.company)
        self.submit(employee_id=other.pk)
        self.assertEqual(self.letters().count(), 1)
        self.assertFalse(ResignationLetter.objects.filter(employee_id=other).exists())

    def test_script_like_text_is_refused_with_a_clear_message_and_nothing_is_sent(self):
        for field in ("additional_comment", "discussion_summary"):
            response = self.submit(**{field: "<script>alert(1)</script>hello"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("content that is not allowed", response.content.decode())
        self.assertEqual(self.letters().count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_ordinary_markup_in_the_comment_is_stored_as_plain_text(self):
        self.submit(additional_comment="Thanks <b>all</b> & goodbye")
        description = self.letters().get().description
        self.assertNotIn("<b>", description)
        self.assertIn("&lt;b&gt;all&lt;/b&gt; &amp; goodbye", description)


class ValidationTests(SelfResignationTestBase):
    def assertRejected(self, response, text):
        self.assertIn(text, response.content.decode())
        self.assertEqual(self.letters().count(), 0)

    def test_a_reason_is_required(self):
        self.assertRejected(self.submit(reason=""), "Resignation reason is required")

    def test_a_summary_is_required_when_they_discussed_it_with_their_manager(self):
        self.assertRejected(self.submit(discussion_summary="  "), "Summary is required")

    def test_early_yes_needs_a_date(self):
        self.assertRejected(
            self.submit(early_last_working_day="yes"), "Please select a date"
        )

    def test_a_date_in_the_past_is_refused(self):
        past = datetime.date.today() - datetime.timedelta(days=1)
        self.assertRejected(
            self.submit(
                early_last_working_day="yes", preferred_last_working_day=past.isoformat()
            ),
            "cannot be in the past",
        )

    def test_a_date_after_the_notice_period_is_refused(self):
        late = self.notice_end + datetime.timedelta(days=1)
        self.assertRejected(
            self.submit(
                early_last_working_day="yes", preferred_last_working_day=late.isoformat()
            ),
            "on or before",
        )

    def test_a_second_resignation_while_one_is_pending_is_refused(self):
        self.submit()
        response = self.submit()
        self.assertIn("already in", response.content.decode())
        self.assertEqual(self.letters().count(), 1)


class ManagerEmailTests(SelfResignationTestBase):
    def test_the_reporting_manager_gets_a_clear_summary_email(self):
        early = datetime.date.today() + datetime.timedelta(days=30)
        self.submit(
            early_last_working_day="yes", preferred_last_working_day=early.isoformat()
        )

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.manager.get_mail()])
        self.assertIn(self.employee.get_full_name(), message.subject)
        self.assertEqual(message.reply_to, [self.employee.get_mail()])
        html = message.alternatives[0][0]
        for expected in (
            self.manager.get_full_name(),
            self.employee.get_full_name(),
            "Relocating",
            "Spoke with my manager about the move.",
            "90 days",
            self.notice_end.strftime("%d %b %Y"),
            early.strftime("%d %b %Y"),
            "Subject to mutual agreement",
            "Thank you for the opportunity.",
        ):
            self.assertIn(expected, html)
        self.assertIn("Relocating", message.body)

    def test_text_the_employee_typed_cannot_inject_markup_into_the_email(self):
        self.submit(
            discussion_summary='Talked <b>twice</b> - see <a href="http://evil.example">this</a>',
            additional_comment="<h1>Big</h1> & more",
        )
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<b>twice</b>", html)
        self.assertNotIn('<a href="http://evil.example">', html)
        self.assertNotIn("<h1>Big</h1>", html)
        self.assertIn("&lt;b&gt;twice&lt;/b&gt;", html)
        self.assertIn("&lt;h1&gt;Big&lt;/h1&gt; &amp; more", html)

    def test_no_manager_means_no_email_but_the_resignation_still_goes_through(self):
        EmployeeWorkInformation.objects.filter(employee_id=self.employee).update(
            reporting_manager_id=None
        )
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.letters().count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_mail_failure_does_not_undo_or_break_the_submission(self):
        with mock.patch(
            "offboarding.mailers.build_letterhead_email",
            side_effect=RuntimeError("smtp down"),
        ):
            response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.letters().count(), 1)


class NoticePeriodFollowsTheSettingTests(SelfResignationTestBase):
    NOTICE_DAYS = 45

    def test_the_days_and_the_end_date_come_from_the_saved_setting(self):
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertIn("serve notice period of 45 days", body)
        self.assertIn(self.notice_end.strftime("%d %b %Y"), body)
        self.assertIn("Any preference of an early last working day?", body)

        self.submit()
        letter = self.letters().get()
        self.assertEqual(letter.notice_period_days, 45)
        self.assertEqual(letter.notice_period_ends, self.notice_end)

    def test_changing_the_setting_changes_the_note(self):
        PayrollGeneralSetting.objects.update(notice_period=60)
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertIn("serve notice period of 60 days", body)

    def test_the_employees_contract_notice_period_wins_over_the_setting(self):
        from payroll.models.models import Contract

        # Every employee already gets a draft contract automatically; make
        # that one active with its own notice period.
        updated = Contract.objects.entire().filter(employee_id=self.employee).update(
            contract_status="active", notice_period_in_days=15
        )
        self.assertEqual(updated, 1)
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertIn("serve notice period of 15 days", body)


class NoSettingSavedTests(SelfResignationTestBase):
    NOTICE_DAYS = None

    def test_the_page_default_of_30_days_applies_and_the_sections_still_show(self):
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertIn("serve notice period of 30 days", body)
        self.assertIn("Any preference of an early last working day?", body)


class NoNoticePolicyTests(SelfResignationTestBase):
    NOTICE_DAYS = 0

    def test_a_notice_period_of_zero_means_the_employee_must_pick_a_date(self):
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertNotIn("serve notice period", body)

        refused = self.submit()
        self.assertIn("Please select a date", refused.content.decode())
        self.assertEqual(self.letters().count(), 0)

        when = datetime.date.today() + datetime.timedelta(days=45)
        self.submit(preferred_last_working_day=when.isoformat())
        letter = self.letters().get()
        self.assertEqual(letter.planned_to_leave_on, when)
        self.assertIsNone(letter.notice_period_days)


class HrCanSeeTheAnswersTests(SelfResignationTestBase):
    def test_the_resignation_detail_shows_reason_and_dates(self):
        self.submit()
        letter = self.letters().get()
        hr = make_user("selfresign_hr_viewer")
        hr.user_permissions.add(
            Permission.objects.get(
                codename="view_resignationletter", content_type__app_label="offboarding"
            )
        )
        self.client.force_login(hr)

        body = self.client.get(
            reverse("resignation-requests-detail-view", kwargs={"pk": letter.pk}), **HX
        ).content.decode()

        self.assertIn("Relocating", body)
        self.assertIn("Discussed with manager", body)
