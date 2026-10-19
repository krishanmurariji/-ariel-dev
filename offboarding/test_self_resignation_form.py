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
    def test_the_reporting_manager_gets_the_resignation_mail(self):
        self.submit()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.manager.get_mail()])
        self.assertIn(self.employee.get_full_name(), message.subject)
        self.assertEqual(message.reply_to, [self.employee.get_mail()])
        html = message.alternatives[0][0]
        for expected in (
            self.employee.get_full_name(),
            "has requested for resignation",
            "Requested Date",
            datetime.date.today().strftime("%d %b %Y"),
            "Reason",
            "Relocating",
            "Note",
            "Thank you for the opportunity.",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("Hi All", html)
        self.assertIn("Relocating", message.body)
        self.assertIn("Thank you for the opportunity.", message.body)

    def test_the_greeting_uses_the_recipients_name_not_hi_all_or_a_designation(self):
        from base.models import Department, JobPosition

        department = Department.objects.create(department="Engineering")
        position = JobPosition.objects.create(
            job_position="Engineering Lead", department_id=department
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.manager).update(
            job_position_id=position
        )
        self.submit()
        message = mail.outbox[0]
        name = self.manager.get_full_name()
        self.assertIn("Hi <strong>%s</strong>," % name, message.alternatives[0][0])
        self.assertTrue(message.body.startswith("Hi %s," % name))
        self.assertNotIn("Engineering Lead", message.body)
        self.assertNotIn("Hi All", message.body)

    def test_each_chosen_person_is_greeted_by_their_own_name(self):
        p_user, person = _employee(company=self.company)
        self.submit(notify_employees=[person.pk])
        by_address = {m.to[0]: m for m in mail.outbox}
        self.assertTrue(
            by_address[person.get_mail()].body.startswith("Hi %s," % person.get_full_name())
        )
        self.assertTrue(
            by_address[self.manager.get_mail()].body.startswith(
                "Hi %s," % self.manager.get_full_name()
            )
        )

    def test_the_note_line_is_left_out_when_there_is_no_comment(self):
        self.submit(additional_comment="")
        message = mail.outbox[0]
        self.assertNotIn("Note :", message.body)
        self.assertNotIn("Note :", message.alternatives[0][0])

    def test_text_the_employee_typed_cannot_inject_markup_into_the_email(self):
        self.submit(additional_comment="<h1>Big</h1> & more")
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<h1>Big</h1>", html)
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


class PersonToNotifyTests(SelfResignationTestBase):
    """The optional "Persons to notify" dropdown on the resignation form."""

    def setUp(self):
        super().setUp()
        self.p1_user, self.p1 = _employee(company=self.company)
        self.p2_user, self.p2 = _employee(company=self.company)

    def recipients(self):
        return sorted(address for m in mail.outbox for address in m.to)

    def select_html(self):
        body = self.client.get(reverse(URL), **HX).content.decode()
        return body, body.split('name="notify_employees"')[1].split("</select>")[0]

    def test_the_dropdown_is_on_the_form_and_lists_people_with_their_designation(self):
        from base.models import Department, JobPosition

        department = Department.objects.create(department="Finance")
        position = JobPosition.objects.create(
            job_position="Finance Head", department_id=department
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.p1).update(
            job_position_id=position
        )
        body, select = self.select_html()
        self.assertIn("Persons to notify", body)
        self.assertIn("%s - Finance Head" % self.p1.get_full_name(), select)
        self.assertIn("same email as your reporting manager", body)

    def test_the_employee_themselves_is_not_offered(self):
        _body, select = self.select_html()
        self.assertNotIn('value="%s"' % self.employee.pk, select)
        self.assertIn('value="%s"' % self.p1.pk, select)

    def test_archived_people_are_not_offered(self):
        Employee.objects.filter(pk=self.p2.pk).update(is_active=False)
        _body, select = self.select_html()
        self.assertNotIn('value="%s"' % self.p2.pk, select)

    def test_the_field_is_optional(self):
        self.submit()
        self.assertEqual(self.letters().count(), 1)
        self.assertEqual(self.letters().get().notify_employees.count(), 0)
        self.assertEqual(self.recipients(), [self.manager.get_mail()])

    def test_chosen_people_get_the_same_mail_as_the_reporting_manager(self):
        self.submit(notify_employees=[self.p1.pk, self.p2.pk])

        letter = self.letters().get()
        self.assertEqual(
            set(letter.notify_employees.values_list("pk", flat=True)),
            {self.p1.pk, self.p2.pk},
        )
        self.assertEqual(
            self.recipients(),
            sorted([self.manager.get_mail(), self.p1.get_mail(), self.p2.get_mail()]),
        )
        # same body apart from the greeting line
        bodies = {m.alternatives[0][0].split("</p>", 1)[1] for m in mail.outbox}
        self.assertEqual(len(bodies), 1, "everyone must get the same body")
        for message in mail.outbox:
            self.assertEqual(message.subject, mail.outbox[0].subject)
            self.assertEqual(message.reply_to, [self.employee.get_mail()])

    def test_choosing_the_manager_does_not_send_them_two_mails(self):
        self.submit(notify_employees=[self.manager.pk, self.p1.pk])
        self.assertEqual(self.recipients().count(self.manager.get_mail()), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_chosen_people_also_get_an_in_app_notice(self):
        from notifications.models import Notification

        self.submit(notify_employees=[self.p1.pk])
        for user in (self.manager_user, self.p1_user):
            self.assertTrue(
                Notification.objects.filter(
                    recipient=user, verb__contains="requested for resignation"
                ).exists(),
                user.username,
            )
        self.assertFalse(Notification.objects.filter(recipient=self.p2_user).exists())

    def test_someone_who_is_not_chosen_gets_nothing(self):
        self.submit(notify_employees=[self.p1.pk])
        self.assertNotIn(self.p2.get_mail(), self.recipients())

    def test_a_chosen_person_without_an_email_does_not_break_the_others(self):
        Employee.objects.filter(pk=self.p1.pk).update(email="")
        EmployeeWorkInformation.objects.filter(employee_id=self.p1).update(email="")
        self.submit(notify_employees=[self.p1.pk, self.p2.pk])
        self.assertEqual(self.letters().count(), 1)
        self.assertIn(self.p2.get_mail(), self.recipients())
        self.assertIn(self.manager.get_mail(), self.recipients())

    def test_the_employee_cannot_pick_themselves_or_an_archived_employee(self):
        Employee.objects.filter(pk=self.p2.pk).update(is_active=False)
        for bad in (self.employee.pk, self.p2.pk, 999999):
            with self.subTest(bad=bad):
                response = self.submit(notify_employees=[bad])
                self.assertEqual(self.letters().count(), 0)
                self.assertIn("Select a valid choice", response.content.decode())
        self.assertEqual(len(mail.outbox), 0)

    def test_a_mail_failure_for_one_person_does_not_stop_the_submission(self):
        from offboarding import mailers

        real = mailers._notify_one_manager

        def flaky(letter, person):
            if person.pk == self.p1.pk:
                raise RuntimeError("smtp down")
            return real(letter, person)

        with mock.patch.object(mailers, "_notify_one_manager", side_effect=flaky):
            self.submit(notify_employees=[self.p1.pk, self.p2.pk])
        self.assertEqual(self.letters().count(), 1)
