"""
payroll_pro/tests/test_access_control.py

Role-based access control, IDOR and multi-company isolation coverage for
Payroll Pro, exercised as three roles: a plain Employee (self-service only),
an HR user (granted the payroll_pro model permissions), and an Admin
(superuser). Password for the non-admin test users is "123qwe" throughout,
matching how the accompanying manual verification logs into the live app.
"""

from datetime import date

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from base.models import Company
from employee.models import Employee, EmployeeWorkInformation
from horilla.horilla_middlewares import set_selected_company
from payroll_pro import scheduler as pp_scheduler
from payroll_pro.models import (
    EmployeeSalary,
    PayrollProAutoSetting,
    PayrollRun,
    PayrollRunRecord,
    SalaryStructure,
    TaxRegime,
)

PASSWORD = "123qwe"

HR_PERMS = [
    "view_taxregime", "add_taxregime", "change_taxregime", "delete_taxregime",
    "view_salarystructure", "add_salarystructure", "change_salarystructure",
    "view_employeesalary", "add_employeesalary",
    "view_payrollrun", "change_payrollrun",
    "view_payrollrunrecord",
    "view_payrollproautosetting", "change_payrollproautosetting",
]


def _make_company(name):
    return Company.objects.create(
        company=name, address="x", country="x", state="x", city="x", zip="00000"
    )


def _make_employee(company, first_name, email, phone, perms=None):
    """A real Employee (so employee_get / ESS views work), linked user
    password reset to the shared test password, with the given payroll_pro
    permission codenames (if any) granted directly."""
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    EmployeeWorkInformation.objects.filter(employee_id=employee).update(
        company_id=company
    )
    # Employee.save() already accessed .employee_work_info once (to decide
    # whether to create it) and cached that pre-update instance on this
    # Python object - refetch so callers see the company we just set.
    employee = Employee.objects.get(pk=employee.pk)
    user = employee.employee_user_id
    # Auto-created by Employee.save() with is_new_employee=True, which a
    # global middleware (base/middleware.py) uses to force-redirect every
    # request to /change-password/ until cleared - the test password is
    # already the "changed" password, so mark it done.
    user.is_new_employee = False
    user.set_password(PASSWORD)
    user.save()
    for codename in perms or []:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return employee


def _select_company(client, company):
    session = client.session
    session["selected_company"] = str(company.pk)
    session.save()


class PayrollProTestBase(TestCase):
    """Two companies, an HR + a plain Employee in each, one admin, a shared
    (company-less/global) tax regime + salary structure, matching how
    TaxRegime/SalaryStructure are actually created in this app today."""

    def setUp(self):
        # current_company_id is a contextvar, not DB state - Django's
        # TestCase rollback doesn't touch it, so a previous test's last
        # request (in the same worker thread) can leak its selected
        # company into this test's setUp before we get a chance to pick
        # our own, silently filtering out employees we're about to create.
        set_selected_company(None)
        self.company_a = _make_company("Company A")
        self.company_b = _make_company("Company B")

        self.admin = Employee.objects.create(
            employee_first_name="Admin", email="admin@example.com", phone="9000000000"
        )
        self.admin.employee_user_id.is_superuser = True
        self.admin.employee_user_id.is_staff = True
        self.admin.employee_user_id.is_new_employee = False
        self.admin.employee_user_id.set_password(PASSWORD)
        self.admin.employee_user_id.save()

        self.hr_a = _make_employee(
            self.company_a, "HRAlice", "hr.a@example.com", "9000000001", HR_PERMS
        )
        self.emp_a1 = _make_employee(
            self.company_a, "EmpAOne", "emp.a1@example.com", "9000000002"
        )
        self.emp_a2 = _make_employee(
            self.company_a, "EmpATwo", "emp.a2@example.com", "9000000003"
        )
        self.hr_b = _make_employee(
            self.company_b, "HRBob", "hr.b@example.com", "9000000004", HR_PERMS
        )
        self.emp_b1 = _make_employee(
            self.company_b, "EmpBOne", "emp.b1@example.com", "9000000005"
        )

        self.regime = TaxRegime.objects.create(name="New Regime")
        self.structure = SalaryStructure.objects.create(
            name="Standard", tax_regime_id=self.regime
        )

        for employee in (self.emp_a1, self.emp_a2, self.emp_b1):
            EmployeeSalary.objects.create(
                employee_id=employee,
                salary_structure_id=self.structure,
                annual_ctc=600000,
                effective_date=date(2026, 1, 1),
            )

    def login_as(self, employee, company=None):
        client = Client()
        assert client.login(
            username=employee.email, password=PASSWORD
        ), f"login failed for {employee.email}"
        _select_company(client, company or employee.employee_work_info.company_id)
        return client


class PermissionGatingTests(PayrollProTestBase):
    """Employee (no payroll_pro perms) must be turned away from every admin
    screen; HR (granted the perms) and Admin (superuser) get in."""

    ADMIN_URLS = [
        "pp-tax-regimes",
        "pp-structures",
        "pp-salary-overview",
        "pp-pay-revisions",
        "pp-run-payroll",
        "pp-payslips",
        "pp-auto-settings",
    ]

    def test_employee_is_redirected_away_from_every_admin_screen(self):
        client = self.login_as(self.emp_a1)
        for name in self.ADMIN_URLS:
            response = client.get(reverse(name))
            self.assertEqual(
                response.status_code, 302, f"{name} should not be reachable by a plain employee"
            )

    def test_hr_can_reach_every_admin_screen(self):
        client = self.login_as(self.hr_a)
        for name in self.ADMIN_URLS:
            response = client.get(reverse(name))
            self.assertEqual(response.status_code, 200, f"HR should reach {name}")

    def test_admin_can_reach_every_admin_screen(self):
        client = self.login_as(self.admin, company=self.company_a)
        for name in self.ADMIN_URLS:
            response = client.get(reverse(name))
            self.assertEqual(response.status_code, 200, f"Admin should reach {name}")

    def test_employee_can_reach_my_finance_self_service(self):
        """My Finance has no permission_required - login is enough, since
        every employee is entitled to see their own pay."""
        client = self.login_as(self.emp_a1)
        response = client.get(reverse("pp-my-finance"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["employee"], self.emp_a1)

    def test_anonymous_is_redirected_to_login(self):
        client = Client()
        response = client.get(reverse("pp-my-finance"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class IDORTests(PayrollProTestBase):
    """An employee must never be able to read or act on another employee's
    payroll data, whether via a direct object reference or the
    ``?employee=`` staff-assist override."""

    def _finalized_record_for(self, employee, company):
        run = PayrollRun.objects.create(
            year=2026, month=6, company_id=company, total_days=26
        )
        revision = EmployeeSalary.current_for(employee)
        record = PayrollRunRecord.objects.create(
            run_id=run, employee_id=employee, salary_revision_id=revision,
            present_days=26, lop_days=0,
        )
        from payroll_pro import services

        calc = services.compute_for_record(record)
        record.computed = calc
        record.gross_earnings = calc["gross_earnings"]
        record.total_deductions = calc["total_deductions"]
        record.net_pay = calc["net_pay"]
        record.save()
        run.status = "completed"
        run.payslips_released = True
        run.save()
        return record

    def test_employee_can_download_own_payslip(self):
        record = self._finalized_record_for(self.emp_a1, self.company_a)
        client = self.login_as(self.emp_a1)
        response = client.get(reverse("pp-payslip-pdf", kwargs={"pk": record.pk}))
        self.assertEqual(response.status_code, 200)

    def test_employee_cannot_download_a_coworkers_payslip(self):
        other_record = self._finalized_record_for(self.emp_a2, self.company_a)
        client = self.login_as(self.emp_a1)
        response = client.get(reverse("pp-payslip-pdf", kwargs={"pk": other_record.pk}))
        self.assertEqual(response.status_code, 404)

    def test_employee_employee_query_param_is_ignored_without_permission(self):
        """A plain employee passing ?employee=<someone_else> must still only
        ever see their own record - the override is gated on
        payroll_pro.view_payrollrunrecord, which they don't have."""
        client = self.login_as(self.emp_a1)
        response = client.get(
            f"{reverse('pp-my-finance')}?employee={self.emp_a2.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["employee"], self.emp_a1)

    def test_hr_can_use_employee_query_param_to_assist(self):
        client = self.login_as(self.hr_a)
        response = client.get(
            f"{reverse('pp-my-finance')}?employee={self.emp_a1.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["employee"], self.emp_a1)

    def test_employee_cannot_reassign_reimbursement_claim_to_coworker(self):
        client = self.login_as(self.emp_a1)
        response = client.post(
            reverse("pp-reimbursement-request"),
            data={
                "employee": self.emp_a2.pk,
                "title": "Travel",
                "amount": "500",
                "allowance_on": "2026-06-01",
                "description": "cab",
                "attachment": SimpleUploadedFile("receipt.txt", b"receipt"),
            },
        )
        self.assertEqual(response.status_code, 302)
        from payroll.models.models import Reimbursement

        claim = Reimbursement.objects.filter(type="reimbursement").first()
        self.assertIsNotNone(claim)
        self.assertEqual(claim.employee_id_id, self.emp_a1.pk)


class CrossCompanyIsolationTests(PayrollProTestBase):
    """Regression coverage for the PayrollRun.company_id fix: two companies
    running payroll for the same month must never end up sharing one run."""

    def test_two_companies_get_separate_runs_for_the_same_month(self):
        client_a = self.login_as(self.hr_a)
        client_b = self.login_as(self.hr_b)

        resp_a = client_a.get(reverse("pp-run-payroll"), {"year": 2026, "month": 6})
        resp_b = client_b.get(reverse("pp-run-payroll"), {"year": 2026, "month": 6})
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_b.status_code, 200)

        run_a = resp_a.context["run"]
        run_b = resp_b.context["run"]
        self.assertNotEqual(
            run_a.pk, run_b.pk, "companies must not share the same PayrollRun row"
        )
        self.assertEqual(run_a.company_id_id, self.company_a.pk)
        self.assertEqual(run_b.company_id_id, self.company_b.pk)

        # Sanity: exactly two rows exist for that month, not one shared row.
        self.assertEqual(
            PayrollRun.objects.entire().filter(year=2026, month=6).count(), 2
        )

    def test_finalizing_one_companys_run_does_not_lock_the_others(self):
        client_a = self.login_as(self.hr_a)
        client_b = self.login_as(self.hr_b)
        run_a = client_a.get(
            reverse("pp-run-payroll"), {"year": 2026, "month": 6}
        ).context["run"]
        run_b = client_b.get(
            reverse("pp-run-payroll"), {"year": 2026, "month": 6}
        ).context["run"]

        client_a.post(reverse("pp-run-finalize", kwargs={"pk": run_a.pk}))

        run_a.refresh_from_db()
        run_b.refresh_from_db()
        self.assertTrue(run_a.is_locked)
        self.assertFalse(run_b.is_locked)

    def test_companys_run_only_contains_its_own_employees(self):
        client_a = self.login_as(self.hr_a)
        run_a = client_a.get(
            reverse("pp-run-payroll"), {"year": 2026, "month": 6}
        ).context["run"]
        employee_ids = set(run_a.records.values_list("employee_id", flat=True))
        self.assertIn(self.emp_a1.pk, employee_ids)
        self.assertIn(self.emp_a2.pk, employee_ids)
        self.assertNotIn(self.emp_b1.pk, employee_ids)


class AutoRunSchedulerCompanyTests(PayrollProTestBase):
    """Regression coverage for the scheduler fix: auto_payroll_run() must
    process each due company independently, and must reset the thread-local
    selected company afterwards (APScheduler reuses worker threads)."""

    def test_each_company_gets_its_own_auto_run(self):
        today_day = str(date.today().day) if date.today().day <= 28 else "last day"
        PayrollProAutoSetting.objects.create(
            company_id=self.company_a, run_day=today_day, auto_run=True,
            auto_finalize=True,
        )
        PayrollProAutoSetting.objects.create(
            company_id=self.company_b, run_day=today_day, auto_run=True,
            auto_finalize=False,
        )

        pp_scheduler.auto_payroll_run()

        from horilla.horilla_middlewares import get_selected_company

        self.assertIsNone(get_selected_company())

        runs = list(PayrollRun.objects.entire().all())
        self.assertEqual(len(runs), 2)
        run_by_company = {run.company_id_id: run for run in runs}
        self.assertIn(self.company_a.pk, run_by_company)
        self.assertIn(self.company_b.pk, run_by_company)

        # Only company A's setting had auto_finalize=True.
        self.assertEqual(run_by_company[self.company_a.pk].status, "completed")
        self.assertEqual(run_by_company[self.company_b.pk].status, "draft")

        a_employee_ids = set(
            run_by_company[self.company_a.pk].records.values_list(
                "employee_id", flat=True
            )
        )
        b_employee_ids = set(
            run_by_company[self.company_b.pk].records.values_list(
                "employee_id", flat=True
            )
        )
        self.assertIn(self.emp_a1.pk, a_employee_ids)
        self.assertNotIn(self.emp_b1.pk, a_employee_ids)
        self.assertIn(self.emp_b1.pk, b_employee_ids)
        self.assertNotIn(self.emp_a1.pk, b_employee_ids)
