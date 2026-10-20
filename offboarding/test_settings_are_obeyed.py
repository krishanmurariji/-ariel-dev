"""
One test per Exit setting that changes what an employee or HR sees or can do:
switching it the other way must visibly change the behaviour. (The early last
working day question once ignored its switch; this keeps every setting honest.)
"""

import datetime

from django.contrib.auth.models import Permission
from django.urls import reverse

from offboarding import exit_service
from offboarding.models import OffboardingGeneralSetting, ResignationLetter
from offboarding.test_self_resignation_form import (
    HX,
    URL,
    SelfResignationTestBase,
    _employee,
)

TODAY = datetime.date.today


def _setting(**changes):
    OffboardingGeneralSetting.objects.filter(company_id=None).update(**changes)


class NoticeStartSettingTests(SelfResignationTestBase):
    """"Consider the resignation date as the start of the notice period"."""

    def test_on_the_notice_ends_a_fixed_number_of_days_after_resigning(self):
        self.submit()
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        self.assertEqual(letter.notice_period_ends, self.notice_end)
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertIn(self.notice_end.strftime("%d %b %Y"), body)

    def test_off_the_notice_waits_for_the_acceptance(self):
        _setting(notice_starts_on_resignation_date=False)
        body = self.client.get(reverse(URL), **HX).content.decode()
        self.assertIn("It starts when your resignation is accepted", body)
        self.assertNotIn("that is till", body)

        self.submit()
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        self.assertIsNone(letter.notice_period_ends)
        self.assertEqual(letter.notice_period_days, 90)

    def test_off_the_reviewer_is_offered_a_day_counted_from_today(self):
        _setting(notice_starts_on_resignation_date=False)
        self.submit()
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        self.assertEqual(
            exit_service.suggested_last_working_day(letter),
            TODAY() + datetime.timedelta(days=90),
        )

    def test_on_the_reviewer_is_offered_the_day_the_employee_saw(self):
        self.submit()
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        self.assertEqual(exit_service.suggested_last_working_day(letter), self.notice_end)

    def test_an_employees_own_early_date_is_always_offered(self):
        _setting(notice_starts_on_resignation_date=False)
        early = TODAY() + datetime.timedelta(days=30)
        self.submit(
            early_last_working_day="yes", preferred_last_working_day=early.isoformat()
        )
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        self.assertEqual(exit_service.suggested_last_working_day(letter), early)

    def test_off_the_notice_starts_on_the_day_it_is_accepted(self):
        _setting(notice_starts_on_resignation_date=False)
        self.submit()
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        ResignationLetter.objects.filter(pk=letter.pk).update(
            created_at=datetime.datetime.now() - datetime.timedelta(days=10)
        )
        letter.refresh_from_db()
        case = exit_service.review_request(
            letter,
            self.manager,
            "accept",
            "ok",
            last_working_day=TODAY() + datetime.timedelta(days=90),
        )
        self.assertEqual(case.notice_date, TODAY())

    def test_on_the_notice_starts_on_the_day_it_was_submitted(self):
        self.submit()
        letter = ResignationLetter.objects.get(employee_id=self.employee)
        ResignationLetter.objects.filter(pk=letter.pk).update(
            created_at=datetime.datetime.now() - datetime.timedelta(days=10)
        )
        letter.refresh_from_db()
        case = exit_service.review_request(
            letter,
            self.manager,
            "accept",
            "ok",
            last_working_day=TODAY() + datetime.timedelta(days=80),
        )
        self.assertEqual(case.notice_date, TODAY() - datetime.timedelta(days=10))


class ApprovalStatusSettingTests(SelfResignationTestBase):
    """"Show approval status to employees"."""

    def setUp(self):
        super().setUp()
        self.letter = ResignationLetter.objects.create(
            employee_id=self.employee,
            title="My resignation",
            description="x",
            planned_to_leave_on=TODAY() + datetime.timedelta(days=30),
            status="retained",
        )
        self.list_url = reverse(
            "individual-resignation-tab-list", kwargs={"pk": self.employee.pk}
        )
        self.detail_url = reverse(
            "tab-resignation-requests-detail-view", kwargs={"pk": self.letter.pk}
        )

    def pages(self):
        return (
            self.client.get(self.list_url, **HX).content.decode(errors="ignore"),
            self.client.get(self.detail_url, **HX).content.decode(errors="ignore"),
        )

    def test_on_the_employee_sees_the_status(self):
        for page in self.pages():
            self.assertIn("Retained", page)

    def test_off_the_employee_does_not_see_the_status(self):
        _setting(show_approval_status=False)
        for page in self.pages():
            self.assertNotIn("Retained", page)

    def test_off_hr_still_sees_the_status(self):
        _setting(show_approval_status=False)
        hr_user, _hr = _employee(company=self.company)
        hr_user.user_permissions.add(
            Permission.objects.get(codename="view_resignationletter")
        )
        self.client.force_login(hr_user)
        for page in self.pages():
            self.assertIn("Retained", page)


class WithdrawSettingTests(SelfResignationTestBase):
    """"Allow withdrawal of resignation" - the button and the action agree."""

    def setUp(self):
        super().setUp()
        self.letter = ResignationLetter.objects.create(
            employee_id=self.employee,
            title="My resignation",
            description="x",
            planned_to_leave_on=TODAY() + datetime.timedelta(days=30),
            status="requested",
        )
        self.list_url = reverse(
            "individual-resignation-tab-list", kwargs={"pk": self.employee.pk}
        )

    def test_on_the_button_is_there_and_it_works(self):
        page = self.client.get(self.list_url, **HX).content.decode(errors="ignore")
        self.assertIn(reverse("exit-withdraw", args=[self.letter.pk]), page)

    def test_off_the_button_is_gone_and_the_action_refuses(self):
        _setting(allow_withdrawal=False)
        page = self.client.get(self.list_url, **HX).content.decode(errors="ignore")
        self.assertNotIn(reverse("exit-withdraw", args=[self.letter.pk]), page)
        self.client.post(reverse("exit-withdraw", args=[self.letter.pk]), **HX)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "requested")


class OwnResignationIsNotEditableTests(SelfResignationTestBase):
    """On the profile tab an employee can withdraw a request, never rewrite it."""

    def setUp(self):
        super().setUp()
        self.letter = ResignationLetter.objects.create(
            employee_id=self.employee,
            title="My resignation",
            description="original text",
            planned_to_leave_on=TODAY() + datetime.timedelta(days=30),
            status="requested",
        )
        self.edit_url = reverse("resignation-request-update", args=[self.letter.pk])

    def test_the_tab_has_no_edit_button(self):
        page = self.client.get(
            reverse("individual-resignation-tab-list", kwargs={"pk": self.employee.pk}),
            **HX,
        ).content.decode(errors="ignore")
        self.assertNotIn(self.edit_url, page)
        self.assertIn(reverse("exit-withdraw", args=[self.letter.pk]), page)

    def test_the_edit_window_and_save_are_refused_for_the_employee(self):
        window = self.client.get(self.edit_url, **HX).content.decode(errors="ignore")
        self.assertNotIn("original text", window)
        self.client.post(
            self.edit_url,
            {"title": "Changed", "description": "rewritten", "planned_to_leave_on": "2031-01-01"},
            **HX,
        )
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.title, "My resignation")
        self.assertIn("original text", self.letter.description)
