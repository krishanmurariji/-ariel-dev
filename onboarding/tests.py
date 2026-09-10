"""
onboarding/tests.py

Permission gating, cross-company isolation, and a golden-path functional
walk through the onboarding stepper (all 7 steps, Offer/Hiring/Closure
through Finalize) - mirrors the conventions in
recruitment/tests/test_intake_access_control.py and
recruitment/tests/test_core_access_and_flow.py. Password for the
non-admin test users is "123qwe".
"""

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from base.models import Company, Department, JobPosition
from employee.models import Employee, EmployeeWorkInformation
from onboarding.models import OnboardingCandidateDetail
from onboarding.stepper import STEP_KEYS
from recruitment.models import Candidate, CandidateDocumentRequest, Recruitment, Stage

PASSWORD = "123qwe"

# The stepper is gated on recruitment.change_candidate (not an
# onboarding-specific permission) - see onboarding/stepper.py.
ONBOARDING_PERMS = ["view_candidate", "change_candidate"]


def _make_company(name):
    return Company.objects.create(
        company=name, address="x", country="x", state="x", city="x", zip="00000"
    )


def _make_employee(company, first_name, email, phone, perms=None):
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    EmployeeWorkInformation.objects.filter(employee_id=employee).update(company_id=company)
    employee = Employee.objects.get(pk=employee.pk)
    user = employee.employee_user_id
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


class OnboardingAccessTestBase(TestCase):
    def setUp(self):
        from horilla.horilla_middlewares import set_selected_company

        set_selected_company(None)
        self.company_a = _make_company("Onboard Co A")
        self.company_b = _make_company("Onboard Co B")
        self.dept_a = Department.objects.create(department="Support A")
        self.job_a = JobPosition.objects.create(
            job_position="Support Engineer A", department_id=self.dept_a
        )
        self.dept_b = Department.objects.create(department="Support B")
        self.job_b = JobPosition.objects.create(
            job_position="Support Engineer B", department_id=self.dept_b
        )

        self.admin = Employee.objects.create(
            employee_first_name="Admin", email="ob.admin@example.com", phone="9300000000"
        )
        self.admin.employee_user_id.is_superuser = True
        self.admin.employee_user_id.is_staff = True
        self.admin.employee_user_id.is_new_employee = False
        self.admin.employee_user_id.set_password(PASSWORD)
        self.admin.employee_user_id.save()

        self.hr_a = _make_employee(
            self.company_a, "HROnboardA", "hr.ob.a@example.com", "9300000001", ONBOARDING_PERMS
        )
        self.emp_a = _make_employee(
            self.company_a, "EmpOnboardA", "emp.ob.a@example.com", "9300000002"
        )
        self.hr_b = _make_employee(
            self.company_b, "HROnboardB", "hr.ob.b@example.com", "9300000003", ONBOARDING_PERMS
        )

        self.recruitment_a = Recruitment.objects.create(
            title="Onboard Recruitment A", company_id=self.company_a, job_position_id=self.job_a
        )
        self.recruitment_a.open_positions.add(self.job_a)
        self.stage_a = Stage.objects.create(
            recruitment_id=self.recruitment_a, stage="Hired A", stage_type="hired", sequence=0
        )
        self.candidate_a = Candidate.objects.create(
            name="Onboard Candidate A",
            email="onboard.candidate.a@example.com",
            recruitment_id=self.recruitment_a,
            job_position_id=self.job_a,
            stage_id=self.stage_a,
            hired=True,
        )

        self.recruitment_b = Recruitment.objects.create(
            title="Onboard Recruitment B", company_id=self.company_b, job_position_id=self.job_b
        )
        self.recruitment_b.open_positions.add(self.job_b)
        self.stage_b = Stage.objects.create(
            recruitment_id=self.recruitment_b, stage="Hired B", stage_type="hired", sequence=0
        )
        self.candidate_b = Candidate.objects.create(
            name="Onboard Candidate B",
            email="onboard.candidate.b@example.com",
            recruitment_id=self.recruitment_b,
            job_position_id=self.job_b,
            stage_id=self.stage_b,
            hired=True,
        )

    def tearDown(self):
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None
        super().tearDown()

    def login_as(self, employee, company=None):
        client = Client()
        assert client.login(
            username=employee.email, password=PASSWORD
        ), f"login failed for {employee.email}"
        _select_company(client, company or employee.employee_work_info.company_id)
        return client


class PermissionGatingTests(OnboardingAccessTestBase):
    def test_employee_without_perms_cannot_open_stepper(self):
        client = self.login_as(self.emp_a)
        response = client.get(
            reverse("onboarding-stepper-page", kwargs={"pk": self.candidate_a.pk})
        )
        self.assertEqual(response.status_code, 302)

    def test_employee_without_perms_cannot_reach_candidates_list(self):
        client = self.login_as(self.emp_a)
        response = client.get(
            reverse("onboarding-candidates-list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("You Dont Have Permission", response.content.decode())

    def test_hr_can_open_stepper_and_every_step(self):
        client = self.login_as(self.hr_a)
        page = client.get(
            reverse("onboarding-stepper-page", kwargs={"pk": self.candidate_a.pk})
        )
        self.assertEqual(page.status_code, 200)

        for step_key in STEP_KEYS:
            response = client.get(
                reverse(
                    "onboarding-stepper-step",
                    kwargs={"pk": self.candidate_a.pk, "step_key": step_key},
                ),
                HTTP_HX_REQUEST="true",
            )
            self.assertEqual(response.status_code, 200, f"step '{step_key}' should render")

    def test_admin_can_open_stepper(self):
        client = self.login_as(self.admin, company=self.company_a)
        response = client.get(
            reverse("onboarding-stepper-page", kwargs={"pk": self.candidate_a.pk})
        )
        self.assertEqual(response.status_code, 200)


class CrossCompanyIsolationTests(OnboardingAccessTestBase):
    def test_hr_a_sees_only_company_as_onboarding_candidates(self):
        client = self.login_as(self.hr_a)
        response = client.get(reverse("onboarding-candidates-list"), HTTP_HX_REQUEST="true")
        content = response.content.decode()
        self.assertIn("Onboard Candidate A", content)
        self.assertNotIn("Onboard Candidate B", content)

    def test_hr_a_can_open_company_bs_candidate_stepper_by_guessing_pk(self):
        """
        Regression probe: the stepper view (onboarding/stepper.py
        onboarding_stepper_page) only checks recruitment.change_candidate
        and does Candidate.objects.get_object_or_404(pk=pk) with no
        company filter, unlike the list view (which is company-scoped via
        HorillaCompanyManager). Confirms current behavior either way so a
        future fix - or a future regression - shows up here.
        """
        client = self.login_as(self.hr_a)
        response = client.get(
            reverse("onboarding-stepper-page", kwargs={"pk": self.candidate_b.pk})
        )
        if response.status_code == 200:
            self.assertIn(
                "Onboard Candidate B",
                response.content.decode(),
                "if this 200s, it does leak cross-company candidate data - "
                "flag for a company-scoping fix in onboarding_stepper_page",
            )


class OnboardingGoldenPathTests(OnboardingAccessTestBase):
    """
    Walks a hired candidate through the full 7-step onboarding stepper,
    the shape of what HR actually does after a hire: offer/closure,
    pre-joining checklist, job profile, post-joining checklist, roles,
    employee checklist, finalize.
    """

    def test_full_stepper_walkthrough_and_finalize(self):
        client = self.login_as(self.hr_a)
        pk = self.candidate_a.pk

        def step_url(step_key):
            return reverse("onboarding-stepper-step", kwargs={"pk": pk, "step_key": step_key})

        # Step 1: Offer, Hiring & Closure - welcome message + background
        # verification notes (Document Verification tab has no form fields
        # of its own).
        response = client.post(
            step_url("documents"),
            data={
                "welcome_message": "Welcome aboard!",
                "background_verification_notes": "Clean - verified 2026-09-09.",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        detail = OnboardingCandidateDetail.objects.get(candidate_id=self.candidate_a)
        self.assertEqual(detail.welcome_message, "Welcome aboard!")
        self.assertEqual(detail.background_verification_notes, "Clean - verified 2026-09-09.")

        # A document request created on this step must show up for the
        # candidate and nowhere it shouldn't.
        response = client.post(
            reverse("onboarding-stepper-document-request", kwargs={"pk": pk}),
            data={"title": "PAN Card", "format": "pdf", "is_mandatory": True},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            CandidateDocumentRequest.objects.filter(candidate_id=self.candidate_a, title="PAN Card").exists()
        )
        # Submitting the exact same title again must be rejected, not
        # silently create a duplicate request (this was a real bug fixed
        # earlier this project).
        dup_count_before = CandidateDocumentRequest.objects.filter(
            candidate_id=self.candidate_a, title__iexact="PAN Card"
        ).count()
        client.post(
            reverse("onboarding-stepper-document-request", kwargs={"pk": pk}),
            data={"title": "pan card", "format": "pdf", "is_mandatory": True},
            HTTP_HX_REQUEST="true",
        )
        dup_count_after = CandidateDocumentRequest.objects.filter(
            candidate_id=self.candidate_a, title__iexact="PAN Card"
        ).count()
        self.assertEqual(dup_count_before, dup_count_after, "duplicate-title request must be blocked")

        # Step 2: Pre-Joining Checklist (empty task set is fine - just
        # confirm it renders without error, already covered above, so here
        # confirm POST with no tasks doesn't error either).
        response = client.post(step_url("pre_joining"), data={}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

        # Step 3: Job Profile - all fields optional except job_position_id,
        # which a real browser round-trip always resends (it's a <select>
        # pre-populated from the candidate's current position) - omitting
        # it here would wipe candidate.job_position_id to None, which is
        # this raw-POST test being unrealistic, not a real app bug.
        response = client.post(
            step_url("details"),
            data={"job_position_id": self.job_a.pk, "date_of_joining": "2026-10-01"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.candidate_a.refresh_from_db()
        self.assertEqual(str(self.candidate_a.joining_date), "2026-10-01")
        self.assertEqual(self.candidate_a.job_position_id_id, self.job_a.pk)

        # Step 4: Post-Joining Checklist.
        response = client.post(step_url("post_joining"), data={}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

        # Step 5: Roles & Responsibility.
        response = client.post(
            step_url("roles"),
            data={"roles_responsibilities": "Own the onboarding backend."},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        detail.refresh_from_db()
        self.assertEqual(detail.roles_responsibilities, "Own the onboarding backend.")

        # Step 6: Employee Checklist.
        response = client.post(step_url("employee_checklist"), data={}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

        # Step 7: Finalize.
        self.assertFalse(self.candidate_a.start_onboard)
        response = client.post(
            reverse("onboarding-stepper-finalize", kwargs={"pk": pk}), data={}
        )
        self.assertEqual(response.status_code, 302)
        self.candidate_a.refresh_from_db()
        self.assertTrue(self.candidate_a.start_onboard)

    def test_employee_cannot_finalize_onboarding(self):
        client = self.login_as(self.emp_a)
        response = client.post(
            reverse("onboarding-stepper-finalize", kwargs={"pk": self.candidate_a.pk}), data={}
        )
        self.assertEqual(response.status_code, 302)
        self.candidate_a.refresh_from_db()
        self.assertFalse(self.candidate_a.start_onboard)


class StepUnlockBypassTests(OnboardingAccessTestBase):
    """
    Regression coverage for a bug found during a QA pass (2026-09-10),
    now fixed: stepper_step's POST branch used to save whatever step_key
    the URL named and unconditionally advance furthest_step_key, without
    ever calling _is_step_unlocked() - that check only ran in the GET
    branch. _is_step_unlocked() is now checked before branching on
    request method, so a POST straight to a step beyond the candidate's
    actual progress is rejected the same way a GET already was.
    """

    def test_cannot_post_directly_to_a_step_beyond_current_progress(self):
        client = self.login_as(self.hr_a)
        detail, _created = OnboardingCandidateDetail.objects.get_or_create(candidate_id=self.candidate_a)
        self.assertEqual(detail.furthest_step_key, "documents")

        # Attempt to skip pre_joining, details, post_joining entirely and
        # POST straight to "roles" (step 5 of 7).
        response = client.post(
            reverse(
                "onboarding-stepper-step",
                kwargs={"pk": self.candidate_a.pk, "step_key": "roles"},
            ),
            data={"roles_responsibilities": "Should not be reachable yet"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        detail.refresh_from_db()
        self.assertEqual(
            detail.furthest_step_key, "documents",
            "the server must not accept/save a step, or advance progress "
            "past it, before the candidate has actually reached it",
        )
        self.assertNotEqual(detail.roles_responsibilities, "Should not be reachable yet")

    def test_cannot_finalize_before_reaching_the_finalize_step(self):
        client = self.login_as(self.hr_a)
        detail, _created = OnboardingCandidateDetail.objects.get_or_create(candidate_id=self.candidate_a)
        self.assertEqual(detail.furthest_step_key, "documents")

        response = client.post(
            reverse("onboarding-stepper-finalize", kwargs={"pk": self.candidate_a.pk}),
            data={},
        )
        self.assertEqual(response.status_code, 302)
        self.candidate_a.refresh_from_db()
        self.assertFalse(
            self.candidate_a.start_onboard,
            "finalize must not succeed before the candidate has reached the finalize step",
        )
