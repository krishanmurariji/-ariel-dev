"""
Mails for a candidate's documents, built in (no template or automation): asked for a document ->
mail to the candidate; accepted -> mail to the candidate; submitted -> mail to the employee who
made the request.
"""

from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from base.models import Company, Department, JobPosition
from horilla.test_access_control import HX, AccessTestCase
from onboarding import document_mails, stepper
from onboarding.models import OnboardingPortal
from recruitment.models import Candidate, CandidateDocument, CandidateDocumentRequest, Recruitment

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def inline(func, *args, **kwargs):
    return func(*args, **kwargs)


@override_settings(STORAGES=PLAIN_STATIC)
class CandidateDocumentMailTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        company = Company.objects.create(company="Test Co", address="x", country="x", state="x", city="x", zip="0")
        department = Department.objects.create(department="Eng")
        job = JobPosition.objects.create(job_position="Dev", department_id=department)
        recruitment = Recruitment.objects.create(
            title="Dev", closed=False, is_active=True, company_id=company, job_position_id=job
        )
        recruitment.open_positions.add(job)
        self.candidate = Candidate.objects.create(
            name="Jane Doe", email="jane@example.com", recruitment_id=recruitment, job_position_id=job
        )
        self.hr_user.is_superuser = True
        self.hr_user.save()
        self.request_obj = CandidateDocumentRequest.objects.create(
            title="Passport", format="pdf", max_size=5, is_mandatory=True,
            description="First page", created_by=self.hr_user,
        )
        self.request_obj.candidate_id.add(self.candidate)
        self.document = CandidateDocument.objects.filter(candidate_id=self.candidate).first() or (
            CandidateDocument.objects.create(
                title="Passport", candidate_id=self.candidate, document_request_id=self.request_obj
            )
        )

    # ---- the mails themselves
    def test_the_candidate_is_told_what_is_wanted_and_where_to_upload(self):
        document_mails.notify_candidate_document_requested(
            self.candidate, self.request_obj, "https://hr.test/onboarding/document-upload/abc/"
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["jane@example.com"])
        self.assertIn("Passport", mail.outbox[0].subject)
        html = mail.outbox[0].alternatives[0][0]
        for text in ("Passport", "PDF", "5 MB", "First page", "https://hr.test/onboarding/document-upload/abc/", "Required"):
            self.assertIn(text, html)

    def test_an_accepted_document_is_confirmed_to_the_candidate(self):
        document_mails.notify_candidate_document_approved(self.document)
        self.assertEqual(mail.outbox[0].to, ["jane@example.com"])
        self.assertIn("accepted", mail.outbox[0].subject.lower())

    def test_the_employee_who_made_the_request_hears_about_the_submission(self):
        document_mails.notify_document_submitted(self.document, "https://hr.test/onboarding/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.hr.get_mail()])
        self.assertIn("Jane Doe", mail.outbox[0].subject)
        self.assertIn("https://hr.test/onboarding/", mail.outbox[0].alternatives[0][0])

    def test_a_request_with_no_known_creator_sends_nothing(self):
        CandidateDocumentRequest.objects.filter(pk=self.request_obj.pk).update(created_by=None)
        self.request_obj.refresh_from_db()
        self.assertFalse(document_mails.notify_document_submitted(self.document, "https://hr.test/"))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_candidate_without_an_email_gets_nothing(self):
        Candidate.objects.filter(pk=self.candidate.pk).update(email="")
        self.candidate.refresh_from_db()
        self.assertFalse(document_mails.notify_candidate_document_requested(self.candidate, self.request_obj, "x"))

    def test_what_staff_typed_cannot_inject_html_into_the_mail(self):
        self.request_obj.title = "<script>alert(1)</script>"
        self.request_obj.save()
        document_mails.notify_candidate_document_requested(self.candidate, self.request_obj, "https://hr.test/u/")
        self.assertNotIn("<script>alert(1)</script>", mail.outbox[0].alternatives[0][0])

    # ---- the places that send them
    def test_asking_for_a_document_in_onboarding_mails_the_candidate(self):
        self.client.force_login(self.hr_user)
        with mock.patch("base.mail_helpers.run_in_background", side_effect=inline), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("onboarding-stepper-document-request", kwargs={"pk": self.candidate.pk}),
                {"title": "Visa", "format": "pdf", "max_size": 5, "is_mandatory": "on", "description": "d"},
                **HX,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Visa", mail.outbox[0].subject)
        self.assertIn("/onboarding/document-upload/", mail.outbox[0].alternatives[0][0])

    def test_asking_for_the_same_document_twice_mails_once(self):
        self.client.force_login(self.hr_user)
        data = {"title": "Visa", "format": "pdf", "max_size": 5, "description": "d"}
        url = reverse("onboarding-stepper-document-request", kwargs={"pk": self.candidate.pk})
        with mock.patch("base.mail_helpers.run_in_background", side_effect=inline), \
                self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, data, **HX)
            self.client.post(url, data, **HX)
        self.assertEqual(len(mail.outbox), 1)

    def test_asking_from_the_candidates_profile_mails_the_candidate_too(self):
        # the other screen that creates a document request (the recruitment candidate profile)
        self.client.force_login(self.hr_user)
        with mock.patch("base.mail_helpers.run_in_background", side_effect=inline), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("candidate-document-request"),
                {"title": "Degree", "candidate_id": [self.candidate.pk], "format": "pdf", "max_size": 4, "description": "x"},
                **HX,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [["jane@example.com"]])
        self.assertIn("Degree", mail.outbox[0].subject)
        self.assertIn("/onboarding/document-upload/", mail.outbox[0].alternatives[0][0])

    def test_adding_the_same_candidate_again_does_not_mail_again(self):
        with mock.patch("base.mail_helpers.run_in_background", side_effect=inline), \
                self.captureOnCommitCallbacks(execute=True):
            self.request_obj.candidate_id.add(self.candidate)  # already on it
        self.assertEqual(len(mail.outbox), 0)

    def test_a_request_made_without_a_web_request_still_mails_with_a_link(self):
        # e.g. from the API or a script: no request to take the address from
        second = CandidateDocumentRequest.objects.create(title="Photo", format="any", created_by=self.hr_user)
        with mock.patch("base.mail_helpers.run_in_background", side_effect=inline), \
                self.captureOnCommitCallbacks(execute=True):
            second.candidate_id.add(self.candidate)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/onboarding/document-upload/", mail.outbox[0].alternatives[0][0])

    def test_the_candidate_uploading_through_their_link_mails_the_request_creator(self):
        OnboardingPortal.objects.create(candidate_id=self.candidate, token="tok123", used=False)
        with mock.patch("base.mail_helpers.run_in_background", side_effect=inline):
            self.client.post(
                reverse("document-upload", kwargs={"token": "tok123"}),
                {f"doc_request_{self.request_obj.pk}": SimpleUploadedFile("p.pdf", b"%PDF-1.4 x")},
            )
        recipients = [m.to for m in mail.outbox]
        self.assertIn([self.hr.get_mail()], recipients)
