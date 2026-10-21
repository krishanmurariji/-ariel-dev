"""
Requests and their comments belong to the employee who made them. An unrelated employee used to be
able to delete another employee's shift or work-type request, approve or cancel their shift swap,
read reimbursement comments and comment on or delete them, and open any payroll deduction.
"""

import datetime

from django.test import Client
from django.urls import reverse

from base.models import EmployeeShift, ShiftRequest, WorkType, WorkTypeRequest
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee

HX = {"HTTP_HX_REQUEST": "true"}
DAY = datetime.date.today() + datetime.timedelta(days=5)


def client_for(user):
    # a view that crashes while building its redirect must not hide whether it acted first
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class Setup(IsolatedTestCase):
    def setUp(self):
        self.manager_user, manager = make_employee("ro_manager")
        self.victim_user, self.victim = make_employee("ro_victim", manager=manager)
        self.stranger_user, _stranger = make_employee("ro_stranger")
        self.colleague_user, self.colleague = make_employee("ro_colleague")
        self.hr_user, _hr = make_employee("ro_hr")
        self.stranger = client_for(self.stranger_user)
        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="RO shift")])[0]
        work_type = WorkType.objects.bulk_create([WorkType(work_type="RO work type")])[0]
        self.shift_request = ShiftRequest.objects.bulk_create(
            [
                ShiftRequest(
                    employee_id=self.victim,
                    shift_id=shift,
                    requested_date=DAY,
                    requested_till=DAY,
                    description="victim shift",
                    reallocate_to=self.colleague,
                )
            ]
        )[0]
        self.work_request = WorkTypeRequest.objects.bulk_create(
            [
                WorkTypeRequest(
                    employee_id=self.victim,
                    work_type_id=work_type,
                    requested_date=DAY,
                    requested_till=DAY,
                    description="victim work type",
                )
            ]
        )[0]

    def shift_exists(self, **filters):
        return ShiftRequest.objects.entire().filter(pk=self.shift_request.pk, **filters).exists()

    def work_exists(self):
        return WorkTypeRequest.objects.entire().filter(pk=self.work_request.pk).exists()


class ShiftAndWorkTypeRequests(Setup):
    def test_a_stranger_cannot_delete_a_shift_request(self):
        self.stranger.post(reverse("shift-request-delete", args=[self.shift_request.pk]), **HX)
        self.assertTrue(self.shift_exists())

    def test_the_owner_can_delete_their_shift_request(self):
        client_for(self.victim_user).post(reverse("shift-request-delete", args=[self.shift_request.pk]), **HX)
        self.assertFalse(self.shift_exists())

    def test_a_stranger_cannot_delete_a_work_type_request(self):
        self.stranger.post(reverse("work-type-request-delete", args=[self.work_request.pk]), **HX)
        self.assertTrue(self.work_exists())

    def test_the_owner_can_delete_their_work_type_request(self):
        client_for(self.victim_user).post(reverse("work-type-request-delete", args=[self.work_request.pk]), **HX)
        self.assertFalse(self.work_exists())

    def test_a_stranger_cannot_cancel_a_shift_swap(self):
        self.stranger.post(reverse("shift-allocation-request-cancel", args=[self.shift_request.pk]), **HX)
        self.assertTrue(self.shift_exists(reallocate_canceled=False))

    def test_a_stranger_cannot_approve_a_shift_swap(self):
        self.stranger.post(reverse("shift-allocation-request-approve", args=[self.shift_request.pk]), **HX)
        self.assertTrue(self.shift_exists(reallocate_approved=False))

    def test_the_colleague_asked_can_approve_the_swap(self):
        client_for(self.colleague_user).post(
            reverse("shift-allocation-request-approve", args=[self.shift_request.pk]), **HX
        )
        self.assertTrue(self.shift_exists(reallocate_approved=True))

    def test_the_reporting_manager_can_cancel_the_swap(self):
        client_for(self.manager_user).post(
            reverse("shift-allocation-request-cancel", args=[self.shift_request.pk]), **HX
        )
        self.assertTrue(self.shift_exists(reallocate_canceled=True))

    def test_a_stranger_cannot_open_the_comment_form_of_a_shift_request(self):
        response = self.stranger.get(reverse("shift-request-add-comment", args=[self.shift_request.pk]), **HX)
        self.assertEqual(response.status_code, 404)

    def test_a_stranger_cannot_comment_on_a_work_type_request(self):
        response = self.stranger.get(reverse("worktype-request-add-comment", args=[self.work_request.pk]), **HX)
        self.assertEqual(response.status_code, 404)


class ReimbursementComments(Setup):
    def setUp(self):
        super().setUp()
        from payroll.models.models import Reimbursement, ReimbursementrequestComment

        self.reimbursement = Reimbursement.objects.bulk_create(
            [Reimbursement(title="victim claim", employee_id=self.victim, allowance_on=DAY)]
        )[0]
        self.comment = ReimbursementrequestComment.objects.bulk_create(
            [
                ReimbursementrequestComment(
                    request_id=self.reimbursement, employee_id=self.victim, comment="PRIVATE-COMMENT"
                )
            ]
        )[0]
        self.model = ReimbursementrequestComment

    def test_a_stranger_cannot_read_the_comments(self):
        response = self.stranger.get(reverse("payroll-request-view-comment", args=[self.reimbursement.pk]), **HX)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"PRIVATE-COMMENT", response.content)

    def test_a_stranger_cannot_open_the_comment_form(self):
        response = self.stranger.get(reverse("payroll-request-add-comment", args=[self.reimbursement.pk]), **HX)
        self.assertEqual(response.status_code, 404)

    def test_a_stranger_cannot_delete_a_comment(self):
        self.stranger.post(reverse("payroll-request-delete-comment", args=[self.comment.pk]), **HX)
        self.assertTrue(self.model.objects.filter(pk=self.comment.pk).exists())

    def test_the_owner_and_the_manager_can_read_the_comments(self):
        for user in (self.victim_user, self.manager_user):
            response = client_for(user).get(
                reverse("payroll-request-view-comment", args=[self.reimbursement.pk]), **HX
            )
            self.assertContains(response, "PRIVATE-COMMENT")

    def test_the_author_can_delete_their_own_comment(self):
        client_for(self.victim_user).post(reverse("payroll-request-delete-comment", args=[self.comment.pk]), **HX)
        self.assertFalse(self.model.objects.filter(pk=self.comment.pk).exists())


class DeductionPage(Setup):
    def test_an_ordinary_employee_cannot_open_a_deduction(self):
        from payroll.models.models import Deduction

        deduction = Deduction.objects.bulk_create([Deduction(title="SECRET-DEDUCTION")])[0]
        response = self.stranger.get(reverse("single-deduction-view", args=[deduction.pk]), **HX)
        self.assertNotIn(b"SECRET-DEDUCTION", response.content)
        grant(self.hr_user, "payroll", "view_allowance")
        response = client_for(self.hr_user).get(reverse("single-deduction-view", args=[deduction.pk]), **HX)
        self.assertContains(response, "SECRET-DEDUCTION")
