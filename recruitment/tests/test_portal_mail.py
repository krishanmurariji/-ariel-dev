"""
The built-in "application received" mail with the candidate portal login: sent when a
candidate is created, only when Settings > Recruitment > "Send Candidate Portal Mail" (with
Application Tracking) asks for it, and without anyone having to set up a mail automation.
"""

from unittest import mock

from django.core import mail
from django.test import TestCase
from horilla.isolated_testcase import IsolatedTestCase

from base.models import Company, Department, JobPosition
from recruitment import portal_mail
from recruitment.models import Candidate, Recruitment, RecruitmentGeneralSetting


class PortalMailTests(IsolatedTestCase):
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

    def settings_row(self, company="own", tracking=True, send=True):
        return RecruitmentGeneralSetting.objects.create(
            company_id=self.company if company == "own" else None,
            candidate_self_tracking=tracking,
            send_candidate_portal_mail=send,
        )

    def apply(self, email="jane@example.com", **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return Candidate.objects.create(
                name="Jane Doe", email=email, mobile="9876543210",
                recruitment_id=self.recruitment, job_position_id=self.job, **extra,
            )

    def test_the_mail_goes_to_the_candidate_with_their_portal_login(self):
        self.settings_row()
        self.apply()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["jane@example.com"])
        self.assertIn("Backend Engineer", message.subject)
        html = message.alternatives[0][0]
        self.assertIn("Jane Doe", html)
        # a button that opens the portal ...
        self.assertIn("/recruitment/candidate-login/", html)
        self.assertIn("Open Candidate Portal", html)
        # ... and a line saying how to sign in - the details themselves are not printed
        self.assertIn("your email address is your username and your mobile number is your password", html)
        self.assertNotIn("jane@example.com", html)
        self.assertNotIn("9876543210", html)

    def test_nothing_is_sent_when_the_setting_is_off_or_missing(self):
        self.apply("none@example.com")  # no settings row at all
        self.assertEqual(len(mail.outbox), 0)
        row = self.settings_row(send=False)
        self.apply("off@example.com")
        self.assertEqual(len(mail.outbox), 0)
        row.send_candidate_portal_mail = True
        row.candidate_self_tracking = False  # the mail needs Application Tracking too
        row.save()
        self.apply("notracking@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_a_setting_not_tied_to_a_company_is_the_fallback(self):
        self.settings_row(company=None)
        self.apply()
        self.assertEqual(len(mail.outbox), 1)

    def test_the_companys_own_setting_beats_the_general_one(self):
        self.settings_row(company=None, send=True)
        self.settings_row(company="own", send=False)
        self.apply()
        self.assertEqual(len(mail.outbox), 0)

    def test_it_is_sent_once_not_again_when_the_candidate_is_edited(self):
        self.settings_row()
        candidate = self.apply()
        candidate.name = "Jane Q Doe"
        with self.captureOnCommitCallbacks(execute=True):
            candidate.save()
        self.assertEqual(len(mail.outbox), 1)

    def test_a_mail_automation_that_already_sends_it_is_not_doubled(self):
        self.settings_row()
        with mock.patch.object(portal_mail, "automation_sends_it", return_value=True):
            self.apply()
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failing_mail_server_does_not_break_the_application(self):
        self.settings_row()
        with mock.patch.object(portal_mail, "build_letterhead_email", side_effect=RuntimeError("smtp down")):
            candidate = self.apply()
        self.assertTrue(Candidate.objects.filter(pk=candidate.pk).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_what_the_applicant_typed_cannot_inject_html_into_the_mail(self):
        self.settings_row()
        with self.captureOnCommitCallbacks(execute=True):
            Candidate.objects.create(
                name="<script>alert(1)</script>", email="x@example.com", mobile="<b>1</b>",
                recruitment_id=self.recruitment, job_position_id=self.job,
            )
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>1</b>", html)
