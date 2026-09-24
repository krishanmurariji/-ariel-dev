"""
recruitment/tests/test_discovered_bugs.py

Regression coverage for bugs found during a comprehensive QA pass over the
Recruitment module (2026-09-10) and fixed the same day. Each test asserts
the correct (now-fixed) behavior. Password for non-admin test users is
"123qwe".
"""

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from base.models import Company, Department, JobPosition
from employee.models import Employee, EmployeeWorkInformation
from recruitment.models import Candidate, CandidateDocument, CandidateRating, Recruitment, Stage
from recruitment.views.views import CANDIDATE_LOGIN_MAX_ATTEMPTS

PASSWORD = "123qwe"


def _make_company(name):
    return Company.objects.create(
        company=name, address="x", country="x", state="x", city="x", zip="00000"
    )


def _make_recruitment_manager(company, recruitment, first_name, email, phone):
    """
    An employee who satisfies manager_can_enter/manager_or_owner-style
    checks for `recruitment` via recruitment_managers, without needing to
    hand out blanket Django permissions - mirrors how a real interviewer
    would be scoped to just the recruitments they're assigned to.
    """
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    EmployeeWorkInformation.objects.filter(employee_id=employee).update(company_id=company)
    employee = Employee.objects.get(pk=employee.pk)
    user = employee.employee_user_id
    user.is_new_employee = False
    user.set_password(PASSWORD)
    user.save()
    recruitment.recruitment_managers.add(employee)
    return employee


class RecruitmentStrSideEffectTests(TestCase):
    """
    Recruitment.__str__() used to call
    self.open_positions.add(self.job_position_id) as a side effect of
    building its string representation - a __str__/__repr__ method must
    be pure. Since Django's default Model.__repr__ calls str(self)
    internally, ANY incidental repr/str of a Recruitment (admin change
    list, shell, print(), logging, or Django's own ProtectedError message
    formatting during a blocked delete) silently performed a database
    write. This was discovered because it made Recruitment.delete() fail
    unpredictably: clearing open_positions, then having some unrelated
    code path repr() the object before the real delete, resurrected the
    M2M row and reintroduced the FK violation. Fixed by removing the
    write from __str__ entirely (recruitment/cbv/recruitment_view.py's
    save flow already sets open_positions explicitly and correctly).
    """

    def setUp(self):
        self.company = _make_company("StrSideEffectCo")
        self.dept = Department.objects.create(department="StrSideEffectDept")
        self.job = JobPosition.objects.create(
            job_position="StrSideEffectRole", department_id=self.dept
        )
        self.recruitment = Recruitment.objects.create(
            title="StrSideEffectRecruitment", company_id=self.company, job_position_id=self.job
        )

    def test_str_does_not_mutate_open_positions(self):
        self.recruitment.open_positions.clear()
        str(self.recruitment)
        self.assertEqual(
            list(self.recruitment.open_positions.all()), [],
            "str(recruitment) must not write to open_positions as a side effect",
        )

    def test_repr_does_not_mutate_open_positions(self):
        self.recruitment.open_positions.clear()
        repr(self.recruitment)
        self.assertEqual(
            list(self.recruitment.open_positions.all()), [],
            "repr(recruitment) - which Django's own error formatting uses - "
            "must not write to open_positions as a side effect",
        )


class DuplicateCandidateEmailCaseSensitivityTests(TestCase):
    """
    CandidateForm.clean() blocked a duplicate application
    case-insensitively (email__iexact), but Candidate.Meta.unique_together
    = ("email", "recruitment_id") - what full_clean()/the DB itself
    enforce - is case-SENSITIVE, so any candidate-creation path that
    doesn't go through CandidateForm (e.g. the email intake feature,
    recruitment/intake.py) could create a second candidate row for the
    same person/job with a differently-cased email. Fixed by adding the
    same case-insensitive check directly to Candidate.save(), so it
    applies regardless of entry point.
    """

    def setUp(self):
        self.company = _make_company("CaseDupCo")
        self.dept = Department.objects.create(department="CaseDupDept")
        self.job = JobPosition.objects.create(job_position="CaseDupRole", department_id=self.dept)
        self.recruitment = Recruitment.objects.create(
            title="CaseDupRecruitment", company_id=self.company, job_position_id=self.job
        )
        self.recruitment.open_positions.add(self.job)
        self.stage = Stage.objects.filter(recruitment_id=self.recruitment).order_by("sequence").first()
        Candidate.objects.create(
            name="First Applicant", email="same.person@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )

    def test_case_variant_duplicate_email_is_rejected(self):
        duplicate = Candidate(
            name="Same Person, Different Case", email="SAME.PERSON@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )
        with self.assertRaises(ValidationError) as ctx:
            duplicate.save()
        self.assertIn("email", ctx.exception.error_dict)


class CandidateDocumentIdorTests(TestCase):
    """
    view_file/file_upload/candidate_add_notes (recruitment/views/views.py)
    are gated only by @candidate_login_required, which verifies *a*
    candidate is logged in via request.session["candidate_id"] - none of
    the three then checked that the object being acted on (a
    CandidateDocument, or a note's candidate) actually belonged to that
    session's candidate. Fixed by adding an ownership check in each view;
    these tests now assert the fixed (secure) behavior.
    """

    def setUp(self):
        self.company = _make_company("IdorRegressionCo")
        self.dept = Department.objects.create(department="IdorRegressionDept")
        self.job = JobPosition.objects.create(job_position="IdorRegressionRole", department_id=self.dept)
        self.recruitment = Recruitment.objects.create(
            title="IdorRegressionRecruitment", company_id=self.company, job_position_id=self.job
        )
        self.recruitment.open_positions.add(self.job)
        self.stage = Stage.objects.filter(recruitment_id=self.recruitment).order_by("sequence").first()
        self.candidate_a = Candidate.objects.create(
            name="Idor Regression A", email="idor.reg.a@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )
        self.candidate_b = Candidate.objects.create(
            name="Idor Regression B", email="idor.reg.b@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )
        self.doc_b = CandidateDocument.objects.create(
            title="Candidate B private document",
            candidate_id=self.candidate_b,
            document=SimpleUploadedFile("private_b.pdf", b"%PDF-1.4 secret"),
            status="submitted",
        )

    def _client_as_candidate(self, candidate):
        client = Client()
        session = client.session
        session["candidate_id"] = candidate.pk
        session.save()
        return client

    def test_candidate_a_cannot_view_candidate_bs_document(self):
        client = self._client_as_candidate(self.candidate_a)
        response = client.get(
            f"/recruitment/candidate-view-file/{self.doc_b.pk}/", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(
            response.status_code, 404,
            "a candidate must not be able to open another candidate's document by pk",
        )

    def test_candidate_a_cannot_upload_over_candidate_bs_document(self):
        client = self._client_as_candidate(self.candidate_a)
        response = client.post(
            f"/recruitment/candidate-file-upload/{self.doc_b.pk}/",
            data={"title": "Overwritten"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 404)
        self.doc_b.refresh_from_db()
        self.assertEqual(self.doc_b.title, "Candidate B private document")

    def test_candidate_b_can_view_their_own_document(self):
        client = self._client_as_candidate(self.candidate_b)
        response = client.get(
            f"/recruitment/candidate-view-file/{self.doc_b.pk}/", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)

    def test_candidate_a_cannot_post_a_note_onto_candidate_bs_timeline(self):
        client = self._client_as_candidate(self.candidate_a)
        response = client.post(
            f"/recruitment/candidate-add-notes/{self.candidate_b.pk}/",
            data={"description": "Spoofed note", "candidate_id": self.candidate_b.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 404)


class CandidatePortalLoginThrottlingTests(TestCase):
    """
    The candidate portal authenticates with the applicant's own phone
    number (CandidateAuthenticationBackend) - low entropy, and often known
    to third parties - with no lockout, making it brute-forceable for
    anyone who knows/guesses a candidate's application email. Fixed by
    throttling failed attempts per email in candidate_login
    (recruitment/views/views.py).
    """

    def setUp(self):
        cache.clear()
        self.company = _make_company("LoginThrottleCo")
        self.dept = Department.objects.create(department="LoginThrottleDept")
        self.job = JobPosition.objects.create(job_position="LoginThrottleRole", department_id=self.dept)
        self.recruitment = Recruitment.objects.create(
            title="LoginThrottleRecruitment", company_id=self.company, job_position_id=self.job
        )
        self.recruitment.open_positions.add(self.job)
        self.stage = Stage.objects.filter(recruitment_id=self.recruitment).order_by("sequence").first()
        self.candidate = Candidate.objects.create(
            name="Throttle Target", email="throttle.target@example.com", mobile="9998887777",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )

    def test_locks_out_after_repeated_failed_attempts(self):
        client = Client()
        for _ in range(CANDIDATE_LOGIN_MAX_ATTEMPTS):
            client.post(
                "/recruitment/candidate-login/",
                data={"email": self.candidate.email, "phone": "0000000000"},
            )
        # Even the CORRECT phone number must now be rejected - the account
        # is locked, not just still failing on wrong guesses.
        response = client.post(
            "/recruitment/candidate-login/",
            data={"email": self.candidate.email, "phone": self.candidate.mobile},
        )
        self.assertNotIn("candidate_id", client.session)
        self.assertIn("Too many failed login attempts", response.content.decode())

    def test_successful_login_resets_the_counter(self):
        client = Client()
        for _ in range(CANDIDATE_LOGIN_MAX_ATTEMPTS - 1):
            client.post(
                "/recruitment/candidate-login/",
                data={"email": self.candidate.email, "phone": "0000000000"},
            )
        response = client.post(
            "/recruitment/candidate-login/",
            data={"email": self.candidate.email, "phone": self.candidate.mobile},
        )
        self.assertEqual(client.session.get("candidate_id"), self.candidate.pk)

    def test_lockout_is_scoped_per_email_not_global(self):
        other_candidate = Candidate.objects.create(
            name="Unrelated Candidate", email="unrelated@example.com", mobile="1112223333",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )
        client = Client()
        for _ in range(CANDIDATE_LOGIN_MAX_ATTEMPTS):
            client.post(
                "/recruitment/candidate-login/",
                data={"email": self.candidate.email, "phone": "0000000000"},
            )
        response = client.post(
            "/recruitment/candidate-login/",
            data={"email": other_candidate.email, "phone": other_candidate.mobile},
        )
        self.assertEqual(client.session.get("candidate_id"), other_candidate.pk)

    def test_ip_wide_lockout_after_many_distinct_email_failures(self):
        """
        The per-email counter alone doesn't stop an attacker cycling
        through many known candidate emails from the same source. Fixed
        by pairing it with a coarser per-IP cap
        (CANDIDATE_LOGIN_MAX_ATTEMPTS_PER_IP) in candidate_login.
        """
        from recruitment.views.views import CANDIDATE_LOGIN_MAX_ATTEMPTS_PER_IP

        client = Client()
        for i in range(CANDIDATE_LOGIN_MAX_ATTEMPTS_PER_IP):
            other = Candidate.objects.create(
                name=f"IP Sweep Target {i}", email=f"ip.sweep.{i}@example.com",
                mobile="5550000000", recruitment_id=self.recruitment,
                job_position_id=self.job, stage_id=self.stage,
            )
            client.post(
                "/recruitment/candidate-login/",
                data={"email": other.email, "phone": "0000000000"},
            )
        # Even a brand-new candidate's CORRECT credentials must now be
        # rejected - the IP itself is locked, not just each individual
        # email's own counter.
        response = client.post(
            "/recruitment/candidate-login/",
            data={"email": self.candidate.email, "phone": self.candidate.mobile},
        )
        self.assertNotIn("candidate_id", client.session)
        self.assertIn("Too many failed login attempts", response.content.decode())


class DragDropHireFlagTests(TestCase):
    """
    update_candidate_stage_and_sequence (recruitment/views/views.py) is the
    pipeline drag-and-drop endpoint - it moved candidates via a queryset
    .update() call, which bypasses Candidate.save() and never set
    hired=True, unlike the sibling update_candidate_sequence endpoint
    three lines away that does. A candidate dragged into a "hired"-type
    stage via drag-and-drop kept hired=False, undercounting
    Recruitment.total_hires() on the Recruitment list/card. Fixed by
    setting hired on the same .update() call.
    """

    def setUp(self):
        from horilla.horilla_middlewares import set_selected_company

        set_selected_company(None)
        self.company = _make_company("DragDropHireCo")
        self.dept = Department.objects.create(department="DragDropHireDept")
        self.job = JobPosition.objects.create(
            job_position="DragDropHireRole", department_id=self.dept
        )
        self.recruitment = Recruitment.objects.create(
            title="DragDropHireRecruitment", company_id=self.company, job_position_id=self.job
        )
        self.recruitment.open_positions.add(self.job)
        self.applied_stage = Stage.objects.filter(
            recruitment_id=self.recruitment, stage_type="applied"
        ).first()
        self.hired_stage = Stage.objects.filter(
            recruitment_id=self.recruitment, stage_type="hired"
        ).first()
        self.candidate = Candidate.objects.create(
            name="Drag Drop Candidate", email="dragdrop.candidate@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job,
            stage_id=self.applied_stage,
        )
        self.manager = _make_recruitment_manager(
            self.company, self.recruitment,
            "DragDropMgr", "dragdrop.mgr@example.com", "9990000001",
        )

    def tearDown(self):
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None
        super().tearDown()

    def test_drag_and_drop_into_hired_stage_sets_hired_flag(self):
        client = Client()
        assert client.login(username=self.manager.email, password=PASSWORD)
        session = client.session
        session["selected_company"] = str(self.company.pk)
        session.save()

        # Populate the per-session pipeline cache the drag-and-drop
        # endpoint reads from - it's filter_pipeline ("pipeline-search"),
        # the HX-only endpoint the pipeline page's JS calls after load,
        # not the plain "pipeline" page shell itself.
        client.get(reverse("pipeline-search"), HTTP_HX_REQUEST="true")

        response = client.get(
            reverse("update-candidate-stage-and-sequence"),
            {"order": [self.candidate.pk], "stage_id": self.hired_stage.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.stage_id_id, self.hired_stage.pk)
        self.assertTrue(
            self.candidate.hired,
            "dragging a candidate into a 'hired' stage must set hired=True, "
            "matching update_candidate_sequence's behavior",
        )


class CandidateRatingPermissionAndValidationTests(TestCase):
    """
    create_candidate_rating/update_candidate_rating (recruitment/views/
    views.py) only had @login_required - any authenticated employee,
    regardless of recruitment role, could rate any candidate, and the raw
    POST "rating" value was passed straight to .create()/int(rating) with
    no validation, bypassing the model's MinValueValidator(0)/
    MaxValueValidator(5). Fixed by adding manager_can_enter and running
    full_clean() before save.
    """

    def setUp(self):
        from horilla.horilla_middlewares import set_selected_company

        set_selected_company(None)
        self.company = _make_company("RatingPermCo")
        self.dept = Department.objects.create(department="RatingPermDept")
        self.job = JobPosition.objects.create(
            job_position="RatingPermRole", department_id=self.dept
        )
        self.recruitment = Recruitment.objects.create(
            title="RatingPermRecruitment", company_id=self.company, job_position_id=self.job
        )
        self.recruitment.open_positions.add(self.job)
        self.stage = Stage.objects.filter(recruitment_id=self.recruitment).order_by("sequence").first()
        self.candidate = Candidate.objects.create(
            name="Rating Perm Candidate", email="rating.perm.candidate@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job, stage_id=self.stage,
        )
        self.outsider = Employee.objects.create(
            employee_first_name="Outsider", email="rating.outsider@example.com", phone="9990000002"
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.outsider).update(
            company_id=self.company
        )
        self.outsider = Employee.objects.get(pk=self.outsider.pk)
        self.outsider.employee_user_id.is_new_employee = False
        self.outsider.employee_user_id.set_password(PASSWORD)
        self.outsider.employee_user_id.save()
        self.manager = _make_recruitment_manager(
            self.company, self.recruitment,
            "RatingMgr", "rating.mgr@example.com", "9990000003",
        )

    def tearDown(self):
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None
        super().tearDown()

    def _login(self, employee):
        client = Client()
        assert client.login(username=employee.email, password=PASSWORD)
        session = client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        return client

    def test_employee_with_no_recruitment_role_cannot_create_a_rating(self):
        client = self._login(self.outsider)
        client.post(
            reverse("create-candidate-rating", args=[self.candidate.pk]),
            data={"rating": 4},
        )
        self.assertFalse(
            CandidateRating.objects.filter(candidate_id=self.candidate).exists(),
            "an employee with no recruitment_managers/stage_managers role "
            "and no add_candidaterating permission must not be able to "
            "rate a candidate",
        )

    def test_recruitment_manager_can_create_a_valid_rating(self):
        client = self._login(self.manager)
        client.post(
            reverse("create-candidate-rating", args=[self.candidate.pk]),
            data={"rating": 4},
        )
        rating = CandidateRating.objects.get(candidate_id=self.candidate)
        self.assertEqual(rating.rating, 4)

    def test_out_of_range_rating_is_rejected_not_saved(self):
        client = self._login(self.manager)
        client.post(
            reverse("create-candidate-rating", args=[self.candidate.pk]),
            data={"rating": 99},
        )
        self.assertFalse(
            CandidateRating.objects.filter(candidate_id=self.candidate).exists(),
            "a rating outside the model's 0-5 range must be rejected by "
            "full_clean(), not silently saved",
        )

    def test_non_numeric_rating_does_not_crash(self):
        client = self._login(self.manager)
        response = client.post(
            reverse("create-candidate-rating", args=[self.candidate.pk]),
            data={"rating": "not-a-number"},
        )
        self.assertNotEqual(response.status_code, 500)
        self.assertFalse(CandidateRating.objects.filter(candidate_id=self.candidate).exists())

    def test_update_rejects_out_of_range_value_and_keeps_old_rating(self):
        CandidateRating.objects.create(
            candidate_id=self.candidate, employee_id=self.manager, rating=3
        )
        client = self._login(self.manager)
        client.post(
            reverse("update-candidate-rating", args=[self.candidate.pk]),
            data={"rating": -1},
        )
        rating = CandidateRating.objects.get(candidate_id=self.candidate, employee_id=self.manager)
        self.assertEqual(rating.rating, 3, "an invalid update must not overwrite the existing rating")
