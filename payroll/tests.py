"""
payroll/tests.py

Regression coverage for bugs found during a QA pattern-audit of the
payroll app (2026-09-10) and fixed the same day - see the matching
recruitment/onboarding audit earlier that session for the pattern
catalogue these were checked against. Password for test employees is
"123qwe" (recruitment.factories.PASSWORD).
"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from horilla import horilla_middlewares
from payroll.models.models import Payslip, Reimbursement
from recruitment.factories import PASSWORD, CompanyFactory, EmployeeFactory


class ReimbursementIdorTests(TestCase):
    """
    create_reimbursement (payroll/views/component_views.py) and
    ReimbursementsFormView (payroll/cbv/reimbursements.py) took an
    instance_id with no check that the caller owned it - any employee
    could edit (and, via Reimbursement.save()'s own reassignment logic,
    effectively hijack) another employee's pending claim.
    """

    def setUp(self):
        self.company = CompanyFactory()
        self.employee_a = EmployeeFactory(company=self.company)
        self.employee_b = EmployeeFactory(company=self.company)

        # Reimbursement.save() reads the current user off a thread-local
        # request (set by ThreadLocalMiddleware on every real HTTP
        # request) to decide who the claim belongs to - fake that here
        # since this creation happens outside of a request/response cycle.
        request = RequestFactory().get("/")
        request.user = self.employee_b.employee_user_id
        horilla_middlewares._thread_locals.request = request
        try:
            self.reimbursement_b = Reimbursement.objects.create(
                employee_id=self.employee_b,
                title="Employee B's claim",
                type="reimbursement",
                amount=100,
                allowance_on=timezone.now().date(),
                attachment=SimpleUploadedFile("receipt.pdf", b"%PDF-1.4"),
            )
        finally:
            horilla_middlewares._thread_locals.request = None

    def _client_as(self, employee):
        client = Client()
        client.login(username=employee.email, password=PASSWORD)
        session = client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        return client

    def test_employee_a_cannot_edit_employee_bs_reimbursement_via_function_view(self):
        client = self._client_as(self.employee_a)
        response = client.post(
            f"/payroll/create-reimbursement/?instance_id={self.reimbursement_b.pk}",
            data={"title": "Hijacked", "type": "reimbursement", "amount": 999},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.reimbursement_b.refresh_from_db()
        self.assertEqual(self.reimbursement_b.title, "Employee B's claim")
        self.assertEqual(self.reimbursement_b.employee_id, self.employee_b)

    def test_employee_a_cannot_edit_employee_bs_reimbursement_via_cbv(self):
        client = self._client_as(self.employee_a)
        response = client.post(
            f"/payroll/reimbursement-update/{self.reimbursement_b.pk}/",
            data={"title": "Hijacked", "type": "reimbursement", "amount": 999},
        )
        self.assertNotEqual(response.status_code, 200)
        self.reimbursement_b.refresh_from_db()
        self.assertEqual(self.reimbursement_b.title, "Employee B's claim")

    def test_owner_can_still_edit_their_own_reimbursement(self):
        client = self._client_as(self.employee_b)
        response = client.post(
            f"/payroll/create-reimbursement/?instance_id={self.reimbursement_b.pk}",
            data={
                "title": "Updated by owner", "type": "reimbursement", "amount": 150,
                "allowance_on": self.reimbursement_b.allowance_on,
                "description": "Updated description",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.reimbursement_b.refresh_from_db()
        self.assertEqual(self.reimbursement_b.title, "Updated by owner")


class PayslipDuplicateDetectionTests(TestCase):
    """
    Payslip.save()'s own duplicate guard checked `.count() > 1`, which
    only ever catches a THIRD duplicate (two others must already exist)
    - the second payslip for the same employee/period, the actual case
    this exists to prevent, always sailed through undetected.
    """

    def setUp(self):
        self.company = CompanyFactory()
        self.employee = EmployeeFactory(company=self.company)

    def test_second_payslip_for_same_employee_and_period_is_rejected(self):
        start = timezone.now().date().replace(day=1)
        end = timezone.now().date()
        Payslip.objects.create(
            employee_id=self.employee, start_date=start, end_date=end,
            status="draft", basic_pay=100, gross_pay=100, net_pay=100,
            pay_head_data={},
        )
        with self.assertRaises(ValidationError):
            Payslip.objects.create(
                employee_id=self.employee, start_date=start, end_date=end,
                status="draft", basic_pay=200, gross_pay=200, net_pay=200,
                pay_head_data={},
            )
