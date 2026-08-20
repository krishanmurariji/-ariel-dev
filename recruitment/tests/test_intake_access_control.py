"""
recruitment/tests/test_intake_access_control.py

Role-based access control and multi-company isolation coverage for the
Candidate Email Intake feature (Settings > Recruitment), exercised as three
roles: a plain Employee (no business here at all), an HR user (granted the
recruitment intake permissions), and an Admin (superuser). Password for the
non-admin test users is "123qwe".
"""

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from base.models import Company
from employee.models import Employee, EmployeeWorkInformation
from gmail_auth.models import GoogleApi
from horilla.horilla_middlewares import set_selected_company
from outlook_auth.models import AzureApi
from recruitment.forms import CandidateIntakeMailboxForm
from recruitment.models import CandidateIntakeLog, CandidateIntakeMailbox

PASSWORD = "123qwe"

HR_PERMS = [
    "view_candidateintakemailbox",
    "add_candidateintakemailbox",
    "change_candidateintakemailbox",
    "delete_candidateintakemailbox",
    "view_candidateintakelog",
]


def _make_company(name):
    return Company.objects.create(
        company=name, address="x", country="x", state="x", city="x", zip="00000"
    )


def _make_employee(company, first_name, email, phone, perms=None):
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    EmployeeWorkInformation.objects.filter(employee_id=employee).update(
        company_id=company
    )
    # Employee.save() already cached a pre-update .employee_work_info on
    # this Python object - refetch so callers see the company we just set.
    employee = Employee.objects.get(pk=employee.pk)
    user = employee.employee_user_id
    # Auto-created by Employee.save() with is_new_employee=True, which a
    # global middleware force-redirects to /change-password/ until cleared.
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


# HorillaListView (horilla_views/generic/cbv/views.py) is class-decorated
# with hx_request_required for the whole framework - these two are plain
# CBV list-fragment endpoints and always 405 without the HX-Request header,
# exactly like every other HorillaListView-based list in this app. They are
# never navigated to directly; the *-settings/-logs shell pages fetch them.
HX_ONLY_URLS = {"candidate-intake-mailbox-list", "candidate-intake-log-list"}


def _get(client, name, **kwargs):
    if name in HX_ONLY_URLS:
        kwargs.setdefault("HTTP_HX_REQUEST", "true")
    return client.get(reverse(name), **kwargs)


def _make_mailbox(company, display_name, email):
    api = AzureApi.objects.create(
        outlook_client_id="x",
        outlook_client_secret="x",
        outlook_tenant_id="x",
        outlook_email=email,
        outlook_display_name=display_name,
        outlook_redirect_uri="https://example.com/cb",
        company=company,
        token={"access_token": "fake", "expires_in": 3600},
    )
    return CandidateIntakeMailbox.objects.create(azure_api=api, company_id=company)


class IntakeAccessTestBase(TestCase):
    def setUp(self):
        # current_company_id is a contextvar, not DB state - a previous
        # test's last request (same worker thread) can otherwise leak its
        # selected company into this test's setUp before we pick our own.
        set_selected_company(None)
        self.company_a = _make_company("Company A")
        self.company_b = _make_company("Company B")

        self.admin = Employee.objects.create(
            employee_first_name="Admin", email="admin@example.com", phone="9100000000"
        )
        self.admin.employee_user_id.is_superuser = True
        self.admin.employee_user_id.is_staff = True
        self.admin.employee_user_id.is_new_employee = False
        self.admin.employee_user_id.set_password(PASSWORD)
        self.admin.employee_user_id.save()

        self.hr_a = _make_employee(
            self.company_a, "HRAlice", "hr.a@example.com", "9100000001", HR_PERMS
        )
        self.emp_a1 = _make_employee(
            self.company_a, "EmpAOne", "emp.a1@example.com", "9100000002"
        )
        self.hr_b = _make_employee(
            self.company_b, "HRBob", "hr.b@example.com", "9100000003", HR_PERMS
        )

        self.mailbox_a = _make_mailbox(self.company_a, "Mailbox A", "intake.a@example.com")
        self.mailbox_b = _make_mailbox(self.company_b, "Mailbox B", "intake.b@example.com")

        self.log_a = CandidateIntakeLog.objects.create(
            mailbox=self.mailbox_a,
            message_id="msg-a-1",
            from_email="candidate.a@example.com",
            subject="Application A",
            received_at="2026-06-01T10:00:00Z",
            status="no_job_match",
            company_id=self.company_a,
        )
        self.log_b = CandidateIntakeLog.objects.create(
            mailbox=self.mailbox_b,
            message_id="msg-b-1",
            from_email="candidate.b@example.com",
            subject="Application B",
            received_at="2026-06-01T10:00:00Z",
            status="no_job_match",
            company_id=self.company_b,
        )

    def login_as(self, employee, company=None):
        client = Client()
        assert client.login(
            username=employee.email, password=PASSWORD
        ), f"login failed for {employee.email}"
        _select_company(client, company or employee.employee_work_info.company_id)
        return client


class PermissionGatingTests(IntakeAccessTestBase):
    GET_URLS = [
        "candidate-intake-mailbox-settings",
        "candidate-intake-mailbox-list",
        "candidate-intake-logs",
        "candidate-intake-log-list",
        "candidate-intake-poll-status",
    ]

    def test_employee_is_redirected_away_from_every_intake_screen(self):
        client = self.login_as(self.emp_a1)
        for name in self.GET_URLS:
            response = _get(client, name)
            if name in HX_ONLY_URLS:
                # HorillaListView's permission_required (horilla_views/
                # cbv_methods.py) renders a 200 "no permission" fragment
                # for a denied HX-Request - a redirect would corrupt the
                # HTMX partial swap instead of showing an inline message.
                self.assertEqual(
                    response.status_code,
                    200,
                    f"{name} should render the no-permission fragment, not error",
                )
                self.assertIn(
                    "You Dont Have Permission",
                    response.content.decode(),
                    f"{name} should not leak its real content to an employee",
                )
            else:
                self.assertEqual(
                    response.status_code, 302, f"{name} should not be reachable by an employee"
                )

    def test_hr_can_reach_every_intake_screen(self):
        client = self.login_as(self.hr_a)
        for name in self.GET_URLS:
            response = _get(client, name)
            self.assertEqual(response.status_code, 200, f"HR should reach {name}")

    def test_admin_can_reach_every_intake_screen(self):
        client = self.login_as(self.admin, company=self.company_a)
        for name in self.GET_URLS:
            response = _get(client, name)
            self.assertEqual(response.status_code, 200, f"Admin should reach {name}")

    def test_employee_cannot_delete_a_mailbox(self):
        client = self.login_as(self.emp_a1)
        response = client.get(
            reverse("candidate-intake-mailbox-delete", kwargs={"pk": self.mailbox_a.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CandidateIntakeMailbox.objects.entire().filter(pk=self.mailbox_a.pk).exists()
        )

    def test_employee_cannot_trigger_a_poll(self):
        client = self.login_as(self.emp_a1)
        response = client.get(reverse("candidate-intake-poll-now"))
        self.assertEqual(response.status_code, 302)

    def test_employee_cannot_retry_logs(self):
        client = self.login_as(self.emp_a1)
        response = client.post(
            reverse("candidate-intake-log-retry-selected"),
            data={"ids": [self.log_a.pk]},
        )
        self.assertEqual(response.status_code, 302)


class CrossCompanyIsolationTests(IntakeAccessTestBase):
    """Regression coverage for the CandidateIntakeMailbox/Log manager fix:
    an HR user scoped to one company must never see another company's
    connected mailboxes or processed emails."""

    def test_hr_a_sees_only_company_as_mailbox(self):
        client = self.login_as(self.hr_a)
        response = _get(client, "candidate-intake-mailbox-list")
        content = response.content.decode()
        self.assertIn("Mailbox A", content)
        self.assertNotIn("Mailbox B", content)

    def test_hr_b_sees_only_company_bs_mailbox(self):
        client = self.login_as(self.hr_b)
        response = _get(client, "candidate-intake-mailbox-list")
        content = response.content.decode()
        self.assertIn("Mailbox B", content)
        self.assertNotIn("Mailbox A", content)

    def test_hr_a_sees_only_company_as_intake_logs(self):
        client = self.login_as(self.hr_a)
        response = _get(client, "candidate-intake-log-list")
        content = response.content.decode()
        self.assertIn("candidate.a@example.com", content)
        self.assertNotIn("candidate.b@example.com", content)

    def test_hr_a_cannot_open_company_bs_log_detail_by_guessing_the_pk(self):
        client = self.login_as(self.hr_a)
        response = client.get(
            reverse("candidate-intake-log-detail", kwargs={"pk": self.log_b.pk})
        )
        content = response.content.decode()
        self.assertNotIn("candidate.b@example.com", content)

    def test_admin_with_all_companies_selected_sees_both_mailboxes(self):
        client = self.login_as(self.admin, company=self.company_a)
        session = client.session
        session["selected_company"] = "all"
        session.save()
        response = _get(client, "candidate-intake-mailbox-list")
        content = response.content.decode()
        self.assertIn("Mailbox A", content)
        self.assertIn("Mailbox B", content)


class CandidateIntakeMailboxFormValidationTests(IntakeAccessTestBase):
    """
    Confirms the provider/account-FK consistency rule (recruitment/forms.py
    CandidateIntakeMailboxForm.clean(), mirrored in
    CandidateIntakeMailbox.clean()) actually rejects mismatched submissions
    instead of silently accepting an unusable mailbox row.
    """

    def _google_api(self):
        return GoogleApi.objects.create(
            google_client_id="x",
            google_client_secret="x",
            google_email="gmail.intake@example.com",
            google_display_name="Gmail Intake",
            google_redirect_uri="https://example.com/cb",
            company=self.company_a,
            token={},
        )

    def test_outlook_provider_without_azure_api_is_rejected(self):
        form = CandidateIntakeMailboxForm(
            data={
                "provider": "outlook",
                "azure_api": "",
                "google_api": "",
                "is_active": True,
                "company_id": self.company_a.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("azure_api", form.errors)

    def test_outlook_provider_with_google_api_instead_is_rejected(self):
        google_api = self._google_api()
        form = CandidateIntakeMailboxForm(
            data={
                "provider": "outlook",
                "azure_api": "",
                "google_api": google_api.pk,
                "is_active": True,
                "company_id": self.company_a.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("azure_api", form.errors)
        self.assertIn("google_api", form.errors)

    def test_gmail_provider_without_google_api_is_rejected(self):
        form = CandidateIntakeMailboxForm(
            data={
                "provider": "gmail",
                "azure_api": "",
                "google_api": "",
                "is_active": True,
                "company_id": self.company_a.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("google_api", form.errors)

    def test_gmail_provider_with_matching_google_api_is_accepted(self):
        google_api = self._google_api()
        form = CandidateIntakeMailboxForm(
            data={
                "provider": "gmail",
                "azure_api": "",
                "google_api": google_api.pk,
                "is_active": True,
                "company_id": self.company_a.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_outlook_provider_with_matching_azure_api_is_accepted(self):
        api = AzureApi.objects.create(
            outlook_client_id="x",
            outlook_client_secret="x",
            outlook_tenant_id="x",
            outlook_email="outlook.intake@example.com",
            outlook_display_name="Outlook Intake",
            outlook_redirect_uri="https://example.com/cb",
            company=self.company_a,
            token={},
        )
        form = CandidateIntakeMailboxForm(
            data={
                "provider": "outlook",
                "azure_api": api.pk,
                "google_api": "",
                "is_active": True,
                "company_id": self.company_a.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class GmailAuthPermissionGatingTests(IntakeAccessTestBase):
    """
    Mirrors the recruitment-side permission gating above, for the
    gmail_auth settings screens themselves (outlook_auth has no equivalent
    suite to mirror - it has no tests at all - so this is new coverage,
    not a port).
    """

    def setUp(self):
        super().setUp()
        self.hr_a.employee_user_id.user_permissions.add(
            Permission.objects.get(codename="view_googleapi")
        )

    def test_employee_cannot_view_gmail_servers(self):
        client = self.login_as(self.emp_a1)
        response = client.get(reverse("gmail_view_records"))
        self.assertEqual(response.status_code, 302)

    def test_employee_cannot_reach_gmail_server_list_fragment(self):
        """Denied HX-fragment requests render a 200 'no permission'
        placeholder (handle_no_permission), not a 302 - a redirect would
        corrupt the HTMX partial swap. Confirms no real server data leaks."""
        client = self.login_as(self.emp_a1)
        response = client.get(
            reverse("gmail_server_list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("You Dont Have Permission", response.content.decode())

    def test_hr_with_view_perm_can_view_gmail_servers(self):
        client = self.login_as(self.hr_a)
        response = client.get(reverse("gmail_view_records"))
        self.assertEqual(response.status_code, 200)

    def test_employee_cannot_create_gmail_server(self):
        client = self.login_as(self.emp_a1)
        response = client.get(
            reverse("gmail_server_create"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("You Dont Have Permission", response.content.decode())

    def test_employee_cannot_start_gmail_login_flow(self):
        client = self.login_as(self.emp_a1)
        response = client.get(reverse("gmail_login"))
        self.assertEqual(response.status_code, 302)
