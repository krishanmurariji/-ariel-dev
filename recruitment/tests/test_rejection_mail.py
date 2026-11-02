"""
The built-in "not selected" mail sent when a candidate is rejected - no mail template or mail
automation needed, and an automation that already owns the mail (on or off) is respected.
"""

from unittest import mock

from django.core import mail
from django.test import TestCase
from horilla.isolated_testcase import IsolatedTestCase

from base.models import Company, Department, JobPosition
from recruitment import rejection_mail
from recruitment.models import Candidate, RejectedCandidate, Recruitment


class RejectionMailTests(IsolatedTestCase):
    def setUp(self):
        # the mail is sent from a background thread in real use; run it inline so it can be checked
        patcher = mock.patch(
            "recruitment.signals._in_background", side_effect=lambda f, *a, **k: f(*a, **k)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.company = Company.objects.create(
            company="Test Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        department = Department.objects.create(department="Engineering")
        self.job = JobPosition.objects.create(job_position="Backend Engineer", department_id=department)
        self.recruitment = Recruitment.objects.create(
            title="Backend Engineer", closed=False, is_active=True,
            company_id=self.company, job_position_id=self.job,
        )
        self.recruitment.open_positions.add(self.job)

    def candidate(self, stage_type="", email="jane@example.com"):
        return Candidate.objects.create(
            name="Jane Doe", email=email, recruitment_id=self.recruitment,
            job_position_id=self.job, stage_type_at_rejection=stage_type,
        )

    def reject(self, candidate):
        with self.captureOnCommitCallbacks(execute=True):
            return RejectedCandidate.objects.create(candidate_id=candidate, description="not a fit")

    def html(self):
        return mail.outbox[-1].alternatives[0][0]

    def test_a_rejected_candidate_gets_the_mail(self):
        self.reject(self.candidate())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["jane@example.com"])
        self.assertIn("Backend Engineer", mail.outbox[0].subject)
        self.assertIn("Jane Doe", self.html())
        self.assertIn("Test Co", self.html())

    def test_the_wording_depends_on_where_they_were_rejected(self):
        self.reject(self.candidate("interview", "a@example.com"))
        self.assertIn("Thank you for interviewing", self.html())
        self.assertNotIn("Thank you for applying", self.html())
        self.reject(self.candidate("applied", "b@example.com"))
        self.assertIn("Thank you for applying", self.html())
        self.assertIn("Backend Engineer", self.html())

    def test_it_is_sent_once_not_again_when_the_record_is_edited(self):
        rejected = self.reject(self.candidate())
        rejected.description = "changed"
        with self.captureOnCommitCallbacks(execute=True):
            rejected.save()
        self.assertEqual(len(mail.outbox), 1)

    def test_a_candidate_without_an_email_is_skipped(self):
        self.reject(self.candidate(email=""))
        self.assertEqual(len(mail.outbox), 0)

    def test_an_automation_that_owns_the_mail_means_no_second_mail(self):
        # switched on: it sends; switched off: an admin chose not to send - either way, not this
        with mock.patch.object(rejection_mail, "automation_owns_it", return_value=True):
            self.reject(self.candidate())
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failing_mail_server_does_not_break_the_rejection(self):
        with mock.patch.object(rejection_mail, "build_letterhead_email", side_effect=RuntimeError("smtp down")):
            rejected = self.reject(self.candidate())
        self.assertTrue(RejectedCandidate.objects.filter(pk=rejected.pk).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_what_the_applicant_typed_cannot_inject_html_into_the_mail(self):
        candidate = Candidate.objects.create(
            name="<script>alert(1)</script>", email="x@example.com",
            recruitment_id=self.recruitment, job_position_id=self.job,
        )
        self.reject(candidate)
        self.assertNotIn("<script>alert(1)</script>", self.html())
