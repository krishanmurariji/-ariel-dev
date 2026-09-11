"""
recruitment/tests/test_concurrency.py

Real concurrency tests using actual threads + a shared, file-backed
SQLite test database (see horilla/settings/base.py DATABASES["default"]
["TEST"]["NAME"] - :memory: wouldn't be visible across threads/
connections). TransactionTestCase (not TestCase) is required here:
TestCase wraps each test in one outer transaction that's rolled back at
the end, so a second thread's connection would never see anything the
first thread wrote - there's no way to observe a race at all under it.

Note on select_for_update() and SQLite: Django's select_for_update() is
a no-op there (the backend silently drops FOR UPDATE from the query),
so the row-level lock the document-request fix asks for isn't literally
enforced by SQLite the way it is by PostgreSQL (this app's production
database). Empirically, running this test still passes reliably against
SQLite: its whole-database (not row-level) write lock, combined with
Django's default busy-timeout retry, ends up serializing the competing
writes anyway. That's read as a property of this test environment, not
a guarantee - a failure here (either a wrong final count, or an
OperationalError/ValidationError surfacing from a thread) is a real
finding either way, not assumed away.
"""

import threading

from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import Client, TransactionTestCase

from recruitment.factories import CompanyFactory, EmployeeFactory, RecruitmentFactory
from recruitment.models import Candidate, CandidateDocumentRequest


class DocumentRequestRaceConditionTests(TransactionTestCase):
    """
    onboarding/stepper.py:document_request_modal - two near-simultaneous
    submissions of the same title for the same candidate should produce
    exactly one CandidateDocumentRequest, not two.
    """

    def setUp(self):
        self.company = CompanyFactory()
        # Pre-warm the per-company BiometricAttendance row the
        # biometric_is_installed context processor lazily creates on
        # first page render - otherwise this test's own concurrent first
        # requests race to create it too, which is a real (now separately
        # fixed) bug in that context processor, but isn't what this test
        # is targeting.
        from base.models import BiometricAttendance

        BiometricAttendance.objects.get_or_create(company_id=self.company)
        self.recruitment = RecruitmentFactory(company_id=self.company)
        self.candidate = Candidate.objects.create(
            name="Race Candidate", email="race.candidate@example.com",
            recruitment_id=self.recruitment, job_position_id=self.recruitment.job_position_id,
        )
        self.hr = EmployeeFactory(company=self.company, perms=["change_candidate"])

    def _client(self):
        client = Client()
        client.login(username=self.hr.email, password="123qwe")
        session = client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        return client

    def test_concurrent_identical_document_requests_produce_only_one_row(self):
        url = f"/onboarding/candidate/{self.candidate.pk}/onboard-document-request/"
        n_threads = 10
        results = []
        results_lock = threading.Lock()

        def submit():
            close_old_connections()
            try:
                client = self._client()
                response = client.post(
                    url,
                    data={"title": "Race Condition Document", "format": "pdf", "is_mandatory": False},
                    HTTP_HX_REQUEST="true",
                )
                outcome = response.status_code
            except Exception as exc:
                # A bare threading.Thread silently drops exceptions raised
                # inside its target - capture them here instead, so a
                # crash under concurrency shows up as a real assertion
                # failure below rather than a mysterious "0 responses".
                outcome = f"{type(exc).__name__}: {exc}"
            with results_lock:
                results.append(outcome)
            close_old_connections()

        threads = [threading.Thread(target=submit) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), n_threads, "a thread's outcome was lost")
        crashes = [r for r in results if r != 200]
        count = CandidateDocumentRequest.objects.filter(
            candidate_id=self.candidate, title__iexact="Race Condition Document"
        ).count()

        # This documents whatever this environment actually does under
        # real concurrency, rather than asserting one fixed expectation -
        # see the module docstring for why. Two distinct outcomes are
        # both "findings", not silent passes:
        if crashes:
            self.fail(
                f"{len(crashes)}/{n_threads} concurrent submissions raised "
                f"instead of returning a normal response - SQLite's default "
                f"busy-timeout is too short for this level of write "
                f"contention on this endpoint (crashes: {crashes[:3]}...). "
                f"Consider raising DATABASES['default']['OPTIONS']['timeout'] "
                f"in horilla/settings/base.py."
            )
        self.assertEqual(
            count, 1,
            f"Expected exactly 1 CandidateDocumentRequest after {n_threads} "
            f"concurrent identical submissions (all returned 200), got "
            f"{count} - the select_for_update() lock did not hold. Expected "
            f"on SQLite (FOR UPDATE is a no-op there); re-run with "
            f"DATABASE_URL pointed at Postgres to confirm the fix holds "
            f"against the actual production database.",
        )
