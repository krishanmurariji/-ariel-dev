"""
recruitment/tests/test_core_access_and_flow.py

Permission gating, cross-company isolation, and a golden-path functional
walk for the core Recruitment screens (Recruitment list, Candidate list,
Pipeline) - the everyday pages a real recruiter/HR user and a plain
employee would hit. Mirrors the conventions in
recruitment/tests/test_intake_access_control.py. Password for the
non-admin test users is "123qwe".
"""

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from base.models import Company, Department, JobPosition
from employee.models import Employee, EmployeeWorkInformation
from recruitment.models import Candidate, Recruitment, Stage

PASSWORD = "123qwe"

RECRUITER_PERMS = [
    "view_recruitment",
    "add_recruitment",
    "change_recruitment",
    "view_candidate",
    "add_candidate",
    "change_candidate",
    "view_stage",
    "add_stage",
    "change_stage",
]


def _make_company(name):
    return Company.objects.create(
        company=name, address="x", country="x", state="x", city="x", zip="00000"
    )


def _make_employee(company, first_name, email, phone, perms=None, reporting_manager=None):
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    EmployeeWorkInformation.objects.filter(employee_id=employee).update(
        company_id=company, reporting_manager_id=reporting_manager
    )
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


# HorillaListView list-fragment endpoints only render for an HX request -
# a plain GET 405s regardless of permission, matching every other
# HorillaListView-based list in this app (see test_intake_access_control.py).
HX_ONLY_URLS = {"list-recruitment", "list-candidate"}


def _get(client, name, **kwargs):
    if name in HX_ONLY_URLS:
        kwargs.setdefault("HTTP_HX_REQUEST", "true")
    return client.get(reverse(name), **kwargs)


class RecruitmentAccessTestBase(TestCase):
    def setUp(self):
        from horilla.horilla_middlewares import set_selected_company

        set_selected_company(None)
        self.company_a = _make_company("Recruit Co A")
        self.company_b = _make_company("Recruit Co B")
        self.dept_a = Department.objects.create(department="Engineering A")
        self.dept_b = Department.objects.create(department="Engineering B")
        self.job_a = JobPosition.objects.create(
            job_position="Backend Engineer A", department_id=self.dept_a
        )
        self.job_b = JobPosition.objects.create(
            job_position="Backend Engineer B", department_id=self.dept_b
        )

        self.admin = Employee.objects.create(
            employee_first_name="Admin", email="rec.admin@example.com", phone="9200000000"
        )
        self.admin.employee_user_id.is_superuser = True
        self.admin.employee_user_id.is_staff = True
        self.admin.employee_user_id.is_new_employee = False
        self.admin.employee_user_id.set_password(PASSWORD)
        self.admin.employee_user_id.save()

        self.hr_a = _make_employee(
            self.company_a, "HRAlice", "hr.rec.a@example.com", "9200000001", RECRUITER_PERMS
        )
        self.emp_a = _make_employee(
            self.company_a, "EmpARec", "emp.rec.a@example.com", "9200000002"
        )
        # Reports to emp_a's manager, but has NO recruitment permissions at
        # all - used to probe whether horilla/decorators.py:manager_can_enter
        # grants access to protected views merely for being *someone's*
        # reporting manager, regardless of whether that access relates to
        # recruitment in any way.
        self.some_manager_a = _make_employee(
            self.company_a, "SomeManagerA", "manager.a@example.com", "9200000003"
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.emp_a).update(
            reporting_manager_id=self.some_manager_a
        )

        self.hr_b = _make_employee(
            self.company_b, "HRBob", "hr.rec.b@example.com", "9200000004", RECRUITER_PERMS
        )

        self.recruitment_a = Recruitment.objects.create(
            title="Recruitment A", company_id=self.company_a, job_position_id=self.job_a
        )
        self.recruitment_a.open_positions.add(self.job_a)
        self.recruitment_b = Recruitment.objects.create(
            title="Recruitment B", company_id=self.company_b, job_position_id=self.job_b
        )
        self.recruitment_b.open_positions.add(self.job_b)

        self.stage_a = Stage.objects.create(
            recruitment_id=self.recruitment_a, stage="Applied A", stage_type="initial", sequence=0
        )
        self.stage_b = Stage.objects.create(
            recruitment_id=self.recruitment_b, stage="Applied B", stage_type="initial", sequence=0
        )

        self.candidate_a = Candidate.objects.create(
            name="Candidate A",
            email="candidate.a@example.com",
            recruitment_id=self.recruitment_a,
            job_position_id=self.job_a,
            stage_id=self.stage_a,
        )
        self.candidate_b = Candidate.objects.create(
            name="Candidate B",
            email="candidate.b@example.com",
            recruitment_id=self.recruitment_b,
            job_position_id=self.job_b,
            stage_id=self.stage_b,
        )

    def tearDown(self):
        # _thread_locals leaks the logged-in user across TestCase instances
        # (see recruitment/tests/test_ai_scoring.py for the same note).
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


class PermissionGatingTests(RecruitmentAccessTestBase):
    GET_URLS = ["recruitment-view", "list-recruitment", "candidate-view", "list-candidate", "pipeline"]

    def test_employee_without_perms_is_denied_every_screen(self):
        client = self.login_as(self.emp_a)
        for name in self.GET_URLS:
            response = _get(client, name)
            if name in HX_ONLY_URLS:
                self.assertEqual(
                    response.status_code, 200,
                    f"{name} should render the no-permission fragment, not error",
                )
                self.assertIn(
                    "You Dont Have Permission", response.content.decode(),
                    f"{name} should not leak real recruitment/candidate data to an employee",
                )
            else:
                self.assertEqual(
                    response.status_code, 302, f"{name} should not be reachable by an employee"
                )

    def test_hr_with_perms_can_reach_every_screen(self):
        client = self.login_as(self.hr_a)
        for name in self.GET_URLS:
            response = _get(client, name)
            self.assertEqual(response.status_code, 200, f"HR should reach {name}")

    def test_admin_can_reach_every_screen(self):
        client = self.login_as(self.admin, company=self.company_a)
        for name in self.GET_URLS:
            response = _get(client, name)
            self.assertEqual(response.status_code, 200, f"Admin should reach {name}")

    def test_employee_cannot_delete_a_candidate(self):
        client = self.login_as(self.emp_a)
        response = client.post(
            reverse("rec-candidate-delete", kwargs={"cand_id": self.candidate_a.pk})
        )
        self.assertEqual(response.status_code, 302, "delete should redirect (denied), not run")
        self.assertTrue(
            Candidate.objects.filter(pk=self.candidate_a.pk).exists(),
            "candidate must survive a delete attempt from a user without delete_candidate",
        )

    def test_hr_can_delete_a_candidate(self):
        client = self.login_as(self.hr_a)
        # hr_a's RECRUITER_PERMS list doesn't include delete_candidate -
        # grant it just for this test to confirm the permission actually
        # unlocks the action once held (not just that it blocks without it).
        client_employee = self.hr_a
        client_employee.employee_user_id.user_permissions.add(
            Permission.objects.get(codename="delete_candidate")
        )
        response = client.post(
            reverse("rec-candidate-delete", kwargs={"cand_id": self.candidate_a.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Candidate.objects.filter(pk=self.candidate_a.pk).exists())

    def test_ordinary_reporting_manager_with_zero_recruitment_perms_is_denied_everywhere(self):
        """
        SomeManagerA holds no recruitment permissions and manages EmpARec
        via an ordinary (non-recruitment) reporting line. Every one of
        these screens must deny them - including pipeline, which is
        gated by recruitment.decorators.manager_can_enter, NOT
        horilla.decorators.manager_can_enter (recruitment/views/views.py
        imports its own local version). That local version correctly
        checks `Stage.objects.filter(stage_managers=employee).exists() or
        Recruitment.objects.filter(recruitment_managers=employee).exists()`
        - actual recruitment-management relationships, not generic "is
        someone's reporting manager" - so an unrelated people-manager
        correctly gets no special access here.
        """
        client = self.login_as(self.some_manager_a)
        for name in self.GET_URLS:
            response = _get(client, name)
            if name in HX_ONLY_URLS:
                self.assertIn("You Dont Have Permission", response.content.decode())
            else:
                self.assertEqual(response.status_code, 302, f"{name} should deny SomeManagerA")

    def test_stage_manager_with_zero_permissions_can_reach_pipeline(self):
        """
        The flip side of the test above: someone with zero recruitment
        permissions, but who IS listed as a stage_managers member on an
        actual Stage, should be let into the pipeline - confirming the
        bypass exists and works for the relationship it's actually meant
        for, not just that it correctly excludes unrelated managers.
        """
        stage_manager = _make_employee(
            self.company_a, "StageManagerA", "stage.manager.a@example.com", "9200000005"
        )
        self.stage_a.stage_managers.add(stage_manager)
        client = self.login_as(stage_manager)

        response = _get(client, "pipeline")
        self.assertEqual(response.status_code, 200)
        # The plain permission_required-gated screens are unaffected by
        # this bypass - it's specific to manager_can_enter-gated views.
        response = _get(client, "recruitment-view")
        self.assertEqual(response.status_code, 302)


class CrossCompanyIsolationTests(RecruitmentAccessTestBase):
    def test_hr_a_sees_only_company_as_recruitment_and_candidates(self):
        client = self.login_as(self.hr_a)
        response = _get(client, "list-recruitment")
        content = response.content.decode()
        self.assertIn("Recruitment A", content)
        self.assertNotIn("Recruitment B", content)

        response = _get(client, "list-candidate")
        content = response.content.decode()
        self.assertIn("Candidate A", content)
        self.assertNotIn("Candidate B", content)

    def test_hr_b_sees_only_company_bs_recruitment_and_candidates(self):
        client = self.login_as(self.hr_b)
        response = _get(client, "list-recruitment")
        content = response.content.decode()
        self.assertIn("Recruitment B", content)
        self.assertNotIn("Recruitment A", content)

        response = _get(client, "list-candidate")
        content = response.content.decode()
        self.assertIn("Candidate B", content)
        self.assertNotIn("Candidate A", content)

    def test_hr_a_cannot_open_company_bs_candidate_detail_by_guessing_pk(self):
        client = self.login_as(self.hr_a)
        # CandidateDetail is a HorillaDetailedView (recruitment/cbv/
        # candidates.py) - like every other Horilla*View, it 405s a plain
        # GET and only renders for an HX-Request, same convention as the
        # list fragments above.
        response = client.get(
            reverse("candidate-detail", kwargs={"pk": self.candidate_b.pk}),
            HTTP_HX_REQUEST="true",
        )
        # Either denied outright, or if rendered (e.g. a shared detail
        # template with its own object-level guard), it must not leak B's
        # candidate name into the page.
        if response.status_code == 200:
            self.assertNotIn(
                "Candidate B", response.content.decode(),
                "hr_a (Company A) must not see Company B's candidate detail "
                "by guessing its pk - HorillaCompanyManager scoping gap",
            )
        else:
            self.assertIn(response.status_code, (302, 403, 404))


class RecruitmentGoldenPathTests(RecruitmentAccessTestBase):
    """
    Walks the everyday recruiter flow end to end: open a recruitment,
    add a second stage, move a candidate forward a stage, then mark them
    hired - the shape of what a recruiter actually does day to day.
    """

    def test_create_stage_and_progress_candidate_through_pipeline(self):
        client = self.login_as(self.hr_a)

        # recruitment/signals.py:create_initial_stage auto-creates "Applied"
        # + "Initial" stages whenever a Recruitment is saved, on top of the
        # stage_a created manually in setUp - so count before/after the
        # new stage, don't assume a fixed starting count.
        stage_count_before = Stage.objects.filter(recruitment_id=self.recruitment_a).count()
        interview_stage = Stage.objects.create(
            recruitment_id=self.recruitment_a,
            stage="Interview A",
            stage_type="interview",
            sequence=3,
        )
        self.assertEqual(
            Stage.objects.filter(recruitment_id=self.recruitment_a).count(),
            stage_count_before + 1,
        )

        # Move the candidate forward a stage - the write path a recruiter
        # exercises via pipeline drag-and-drop.
        self.candidate_a.stage_id = interview_stage
        self.candidate_a.save()
        self.candidate_a.refresh_from_db()
        self.assertEqual(self.candidate_a.stage_id_id, interview_stage.pk)

        hired_stage = Stage.objects.create(
            recruitment_id=self.recruitment_a, stage="Hired A", stage_type="hired", sequence=2
        )
        self.candidate_a.stage_id = hired_stage
        self.candidate_a.hired = True
        self.candidate_a.save()
        self.candidate_a.refresh_from_db()
        self.assertTrue(self.candidate_a.hired)

        # The candidate must still resolve under the company-scoped
        # manager after all these writes - regression guard for the
        # HorillaCompanyManager scoping on Candidate/Stage.
        response = _get(client, "list-candidate")
        self.assertIn("Candidate A", response.content.decode())
