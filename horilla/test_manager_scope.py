"""
`manager_can_enter` (horilla/decorators.py) lets in anyone who manages *somebody*, not someone who manages
the record's employee. These tests act as a manager of one person against a record that belongs to an
unrelated employee; the matching control tests act on that manager's own report, which must keep working.
"""

import datetime

from django.test import Client
from django.urls import reverse

from employee.models import EmployeeBankDetails
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from leave.models import AvailableLeave, LeaveRequest, LeaveType

HX = {"HTTP_HX_REQUEST": "true"}
DAY = datetime.date.today() + datetime.timedelta(days=5)


def client_for(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class Setup(IsolatedTestCase):
    def setUp(self):
        self.manager_user, self.manager = make_employee("ms_manager")
        _, self.report = make_employee("ms_report", manager=self.manager)
        _, self.outsider = make_employee("ms_outsider")
        self.client = client_for(self.manager_user)

    def bank_for(self, employee):
        return EmployeeBankDetails.objects.bulk_create(
            [
                EmployeeBankDetails(
                    employee_id=employee,
                    bank_name="Original Bank",
                    account_number="1111",
                    branch="Main",
                    address="Somewhere",
                    any_other_code1="ORIG0000001",
                    pan_number=f"ABCDE{employee.pk:04d}F",
                    uan_number=f"1001{employee.pk:08d}",
                )
            ]
        )[0]

    def leave_for(self, employee):
        leave_type = LeaveType.objects.bulk_create([LeaveType(name=f"ms type {employee.pk}")])[0]
        AvailableLeave.objects.bulk_create(
            [
                AvailableLeave(
                    employee_id=employee,
                    leave_type_id=leave_type,
                    available_days=10,
                    total_leave_days=10,
                )
            ]
        )
        return LeaveRequest.objects.bulk_create(
            [
                LeaveRequest(
                    employee_id=employee,
                    leave_type_id=leave_type,
                    start_date=DAY,
                    end_date=DAY,
                    requested_days=1,
                    description="ms leave",
                )
            ]
        )[0]


class BankDetails(Setup):
    def post_bank(self, bank):
        data = {
            "bank_name": "Hijacked Bank",
            "account_number": "9999",
            "branch": "Evil",
            "any_other_code1": "HACK0000001",
            "address": "Elsewhere",
            "pan_number": "ZZZZZ9999Z",
            "uan_number": "999999999999",
        }
        return self.client.post(
            reverse("employee-bank-details-view-update", args=[bank.pk]), data, **HX
        )

    def test_a_manager_cannot_edit_the_bank_details_of_someone_they_do_not_manage(self):
        bank = self.bank_for(self.outsider)
        self.post_bank(bank)
        bank.refresh_from_db()
        self.assertEqual(bank.bank_name, "Original Bank")

    def test_a_manager_can_edit_the_bank_details_of_their_own_report(self):
        bank = self.bank_for(self.report)
        self.post_bank(bank)
        bank.refresh_from_db()
        self.assertEqual(bank.bank_name, "Hijacked Bank")


class LeaveRequests(Setup):
    def test_a_manager_cannot_delete_the_leave_request_of_someone_they_do_not_manage(self):
        leave = self.leave_for(self.outsider)
        self.client.post(reverse("request-delete", args=[leave.pk]), **HX)
        self.assertTrue(LeaveRequest.objects.entire().filter(pk=leave.pk).exists())

    def test_a_manager_can_delete_the_leave_request_of_their_own_report(self):
        leave = self.leave_for(self.report)
        self.client.post(reverse("request-delete", args=[leave.pk]), **HX)
        self.assertFalse(LeaveRequest.objects.entire().filter(pk=leave.pk).exists())

    def test_a_manager_cannot_approve_the_leave_request_of_someone_they_do_not_manage(self):
        leave = self.leave_for(self.outsider)
        self.client.post(reverse("request-approve", args=[leave.pk]), **HX)
        leave.refresh_from_db()
        self.assertNotEqual(leave.status, "approved")

    def test_a_manager_can_approve_the_leave_request_of_their_own_report(self):
        leave = self.leave_for(self.report)
        self.client.post(reverse("request-approve", args=[leave.pk]), **HX)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")
