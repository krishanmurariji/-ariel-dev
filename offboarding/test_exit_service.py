"""
The exit rules (offboarding/exit_service.py + exit_policy.py): reviewing a
request, the mandatory comment, withdrawing, changing the last working day,
cancelling / rejoining, the daily move to Exited, and which notice period an
employee is on.
"""

import datetime
import itertools
from unittest import mock

from django.urls import reverse

from base.models import Company
from base.test_security_regressions import make_user
from employee.models import Employee, EmployeeWorkInformation
from horilla.isolated_testcase import IsolatedTestCase
from offboarding import exit_policy, exit_service
from offboarding.exit_service import ExitError
from offboarding.models import (
    EmployeeNoticePolicy,
    ExitActivity,
    NoticePeriodPolicy,
    OffboardingEmployee,
    OffboardingGeneralSetting,
    ResignationLetter,
)
from payroll.models.models import PayrollGeneralSetting

_n = itertools.count()
TODAY = datetime.date.today()


def _person(company=None, manager=None, **user_extra):
    user = make_user(f"exitsvc_{next(_n)}", **user_extra)
    employee = Employee.objects.get(employee_user_id=user)
    EmployeeWorkInformation.objects.entire().update_or_create(
        employee_id=employee,
        defaults={"company_id": company, "reporting_manager_id": manager},
    )
    return user, Employee.objects.get(pk=employee.pk)


def _letter(employee, **overrides):
    data = dict(
        employee_id=employee,
        title="Resignation",
        description="<p>bye</p>",
        planned_to_leave_on=TODAY + datetime.timedelta(days=30),
        reason="Relocating",
        initiated_by=employee,
    )
    data.update(overrides)
    return ResignationLetter.objects.create(**data)


class ExitServiceTestBase(IsolatedTestCase):
    def setUp(self):
        self.hr_user, self.hr = _person(is_staff=True, is_superuser=True)
        self.manager_user, self.manager = _person()
        self.user, self.employee = _person(manager=self.manager)
        self.letter = _letter(self.employee)

    def accept(self, **kw):
        args = dict(decision="accept", comment="Fine, accepted.")
        args.update(kw)
        return exit_service.review_request(self.letter, self.hr, **args)


class ReviewTests(ExitServiceTestBase):
    def test_accepting_starts_an_exit_in_progress(self):
        lwd = TODAY + datetime.timedelta(days=45)
        case = self.accept(
            last_working_day=lwd, rehire_eligible=True, rehire_comment="Great person"
        )

        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "approved")
        self.assertEqual(self.letter.review_comment, "Fine, accepted.")
        self.assertEqual(self.letter.reviewed_by, self.hr)
        self.assertIsNotNone(self.letter.reviewed_at)
        self.assertEqual(self.letter.offboarding_employee_id, case)
        self.assertEqual(case.status, "in_progress")
        self.assertEqual(case.last_working_day, lwd)
        self.assertEqual(case.notice_period_ends, lwd)  # old field kept in step
        self.assertTrue(case.rehire_eligible)
        self.assertEqual(case.rehire_comment, "Great person")
        self.assertEqual(self.letter.planned_to_leave_on, lwd)
        self.assertTrue(
            ExitActivity.objects.entire()
            .filter(employee_id=self.employee, action="accepted")
            .exists()
        )
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_active)

    def test_rejecting_and_retaining_leave_no_exit_and_allow_a_new_request(self):
        exit_service.review_request(self.letter, self.hr, "reject", "Not now")
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "rejected")
        self.assertFalse(OffboardingEmployee.objects.entire().exists())

        second = _letter(self.employee)
        exit_service.review_request(second, self.hr, "retain", "Counter-offer accepted")
        second.refresh_from_db()
        self.assertEqual(second.status, "retained")
        _letter(self.employee)  # no "already pending" block after either decision

    def test_a_comment_is_mandatory_for_every_decision(self):
        for decision in ("accept", "reject", "retain"):
            with self.assertRaises(ExitError):
                exit_service.review_request(self.letter, self.hr, decision, "   ")
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "requested")

    def test_script_like_text_in_the_comment_is_refused(self):
        with self.assertRaises(ExitError):
            self.accept(comment="<script>alert(1)</script>")

    def test_a_request_cannot_be_reviewed_twice(self):
        self.accept(last_working_day=TODAY + datetime.timedelta(days=10))
        with self.assertRaises(ExitError):
            exit_service.review_request(self.letter, self.hr, "reject", "changed my mind")

    def test_an_unknown_decision_is_refused(self):
        with self.assertRaises(ExitError):
            exit_service.review_request(self.letter, self.hr, "maybe", "hmm")

    def test_who_may_review(self):
        stranger_user, _ = _person()
        self.assertTrue(exit_service.can_review(self.hr_user, self.letter))
        self.assertTrue(exit_service.can_review(self.manager_user, self.letter))
        self.assertFalse(exit_service.can_review(stranger_user, self.letter))
        self.assertFalse(exit_service.can_review(self.user, self.letter))

    def test_acknowledgement_mode_starts_the_exit_without_a_review(self):
        OffboardingGeneralSetting.objects.create(
            resignation_request=True, review_mode="acknowledgement", company_id=None
        )
        case = exit_service.settle_new_request(self.letter)
        self.assertEqual(case.status, "in_progress")
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "approved")

    def test_approval_mode_leaves_it_under_review(self):
        OffboardingGeneralSetting.objects.create(
            resignation_request=True, review_mode="approval", company_id=None
        )
        self.assertIsNone(exit_service.settle_new_request(self.letter))
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "requested")

    def test_holiday_rule_moves_the_last_working_day(self):
        sat = datetime.date(2030, 6, 1)
        OffboardingGeneralSetting.objects.create(
            resignation_request=True,
            last_working_day_holiday_rule="before",
            company_id=None,
        )
        with mock.patch(
            "offboarding.exit_policy.is_non_working_day",
            side_effect=lambda day, employee=None: day.weekday() >= 5,
        ):
            case = self.accept(last_working_day=sat)
        self.assertEqual(case.last_working_day, datetime.date(2030, 5, 31))


class HolidayAdjustTests(IsolatedTestCase):
    def test_rules(self):
        weekend = lambda day, employee=None: day.weekday() >= 5  # noqa: E731
        sat = datetime.date(2030, 6, 1)
        with mock.patch("offboarding.exit_policy.is_non_working_day", side_effect=weekend):
            self.assertEqual(exit_policy.adjust_last_working_day(sat, "none"), sat)
            self.assertEqual(
                exit_policy.adjust_last_working_day(sat, "before"),
                datetime.date(2030, 5, 31),
            )
            self.assertEqual(
                exit_policy.adjust_last_working_day(sat, "after"),
                datetime.date(2030, 6, 3),
            )
            weekday = datetime.date(2030, 5, 29)
            self.assertEqual(exit_policy.adjust_last_working_day(weekday, "after"), weekday)
        self.assertIsNone(exit_policy.adjust_last_working_day(None, "after"))


class WithdrawTests(ExitServiceTestBase):
    def test_the_employee_can_withdraw_while_under_review(self):
        exit_service.withdraw_request(self.letter, self.employee)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "withdrawn")
        _letter(self.employee)  # and can raise a fresh one

    def test_not_when_the_setting_forbids_it(self):
        OffboardingGeneralSetting.objects.create(
            resignation_request=True, allow_withdrawal=False, company_id=None
        )
        with self.assertRaises(ExitError):
            exit_service.withdraw_request(self.letter, self.employee)

    def test_not_after_review(self):
        self.accept(last_working_day=TODAY + datetime.timedelta(days=5))
        with self.assertRaises(ExitError):
            exit_service.withdraw_request(self.letter, self.employee)

    def test_someone_else_cannot_withdraw_it(self):
        _, stranger = _person()
        with self.assertRaises(ExitError):
            exit_service.withdraw_request(self.letter, stranger)


class RunningExitTests(ExitServiceTestBase):
    def setUp(self):
        super().setUp()
        self.lwd = TODAY + datetime.timedelta(days=30)
        self.case = self.accept(last_working_day=self.lwd)

    def test_changing_the_last_working_day_needs_a_reason_and_is_logged(self):
        new = self.lwd + datetime.timedelta(days=7)
        with self.assertRaises(ExitError):
            exit_service.change_last_working_day(self.case, new, " ", self.hr)

        exit_service.change_last_working_day(self.case, new, "Handover needs longer", self.hr)

        self.case.refresh_from_db()
        self.letter.refresh_from_db()
        self.assertEqual(self.case.last_working_day, new)
        self.assertEqual(self.case.notice_period_ends, new)
        self.assertEqual(self.letter.planned_to_leave_on, new)
        entry = ExitActivity.objects.entire().get(action="lwd_changed")
        self.assertEqual(entry.comment, "Handover needs longer")
        self.assertEqual(entry.to_value, new.strftime("%d %b %Y"))

    def test_cancelling_keeps_the_employee_and_allows_a_new_resignation(self):
        with self.assertRaises(ExitError):
            exit_service.cancel_exit(self.case, self.hr, "")
        exit_service.cancel_exit(self.case, self.hr, "Employee decided to stay")

        self.case.refresh_from_db()
        self.letter.refresh_from_db()
        self.assertEqual(self.case.status, "cancelled")
        self.assertEqual(self.letter.status, "cancelled")
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_active)

        again = _letter(self.employee)
        case2 = exit_service.review_request(
            again, self.hr, "accept", "ok", last_working_day=self.lwd
        )
        self.assertEqual(case2.pk, self.case.pk)  # the same row is reused
        self.assertEqual(case2.status, "in_progress")

    def test_cancelling_an_exited_employee_reactivates_them(self):
        exit_service.mark_exited(self.case)
        Employee.objects.filter(pk=self.employee.pk).update(is_active=False)
        self.user.is_active = False
        self.user.save()

        exit_service.cancel_exit(self.case, self.hr, "Rejoining")

        self.employee.refresh_from_db()
        self.user.refresh_from_db()
        self.assertTrue(self.employee.is_active)
        self.assertTrue(self.user.is_active)
        self.assertTrue(
            ExitActivity.objects.entire().filter(action="rejoined").exists()
        )

    def test_the_daily_job_moves_only_those_whose_day_has_passed(self):
        OffboardingEmployee.objects.entire().filter(pk=self.case.pk).update(
            last_working_day=TODAY - datetime.timedelta(days=1)
        )
        other_user, other = _person()
        other_case = exit_service.review_request(
            _letter(other), self.hr, "accept", "ok", last_working_day=TODAY
        )

        moved = exit_service.process_due_exits()

        self.assertEqual(moved, 1)
        self.case.refresh_from_db()
        other_case.refresh_from_db()
        self.assertEqual(self.case.status, "exited")
        self.assertEqual(self.case.exited_on, TODAY - datetime.timedelta(days=1))
        self.assertEqual(other_case.status, "in_progress")  # today is still a working day
        self.assertEqual(exit_service.process_due_exits(), 0)  # idempotent
        # Becoming "exited" never switches the login off by itself.
        self.employee.refresh_from_db()
        self.user.refresh_from_db()
        self.assertTrue(self.employee.is_active)
        self.assertTrue(self.user.is_active)

    def test_moving_an_exited_employees_date_into_the_future_reopens_it(self):
        exit_service.mark_exited(self.case)
        exit_service.change_last_working_day(
            self.case, TODAY + datetime.timedelta(days=3), "Extended", self.hr
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, "in_progress")
        self.assertIsNone(self.case.exited_on)


class NoticePolicyTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Co", address="a", country="c", state="s", city="c", zip="1"
        )
        _, self.employee = _person(company=self.company)

    def days(self):
        return exit_policy.notice_period_days(Employee.objects.get(pk=self.employee.pk))

    def test_nothing_saved_gives_the_30_day_default(self):
        self.assertEqual(self.days(), 30)

    def test_payroll_setting_then_default_policy_then_contract_then_assigned(self):
        PayrollGeneralSetting.objects.create(notice_period=45, company_id=None)
        self.assertEqual(self.days(), 45)

        NoticePeriodPolicy.objects.create(title="Global", days=60, is_default=True)
        self.assertEqual(self.days(), 60)

        NoticePeriodPolicy.objects.create(
            title="Co default", days=75, is_default=True, company_id=self.company
        )
        self.assertEqual(self.days(), 75)

        from payroll.models.models import Contract

        Contract.objects.entire().filter(employee_id=self.employee).update(
            contract_status="active", notice_period_in_days=15
        )
        self.assertEqual(self.days(), 15)

        special = NoticePeriodPolicy.objects.create(title="Exec", days=120)
        EmployeeNoticePolicy.objects.create(employee_id=self.employee, policy_id=special)
        self.assertEqual(self.days(), 120)

    def test_a_policy_of_zero_days_means_no_notice(self):
        NoticePeriodPolicy.objects.create(title="Immediate", days=0, is_default=True)
        self.assertIsNone(self.days())

    def test_only_one_default_policy_per_company(self):
        first = NoticePeriodPolicy.objects.create(title="A", days=10, is_default=True)
        second = NoticePeriodPolicy.objects.create(title="B", days=20, is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_a_policy_in_use_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        policy = NoticePeriodPolicy.objects.create(title="Used", days=30)
        EmployeeNoticePolicy.objects.create(employee_id=self.employee, policy_id=policy)
        with self.assertRaises(ProtectedError):
            policy.delete()


class SelfResignationAcknowledgementTests(IsolatedTestCase):
    """End to end: the employee's own form + Acknowledgement mode."""

    def test_the_exit_starts_immediately(self):
        OffboardingGeneralSetting.objects.create(
            resignation_request=True, review_mode="acknowledgement", company_id=None
        )
        _, manager = _person()
        user, employee = _person(manager=manager)
        self.client.force_login(user)

        self.client.post(
            reverse("resignation-self-create"),
            {
                "discussed_with_manager": "no",
                "reason": "Relocating",
                "early_last_working_day": "no",
            },
            HTTP_HX_REQUEST="true",
        )

        case = OffboardingEmployee.objects.entire().get(employee_id=employee)
        self.assertEqual(case.status, "in_progress")
