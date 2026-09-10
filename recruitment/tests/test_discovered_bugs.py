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

from base.models import Company, Department, JobPosition
from recruitment.models import Candidate, CandidateDocument, Recruitment, Stage
from recruitment.views.views import CANDIDATE_LOGIN_MAX_ATTEMPTS


def _make_company(name):
    return Company.objects.create(
        company=name, address="x", country="x", state="x", city="x", zip="00000"
    )


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
