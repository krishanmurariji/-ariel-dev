"""
Mails for an employee's documents, built in (no template or automation): asked for a document ->
mail to each employee asked; uploaded -> mail to the employee who made the request; accepted or
rejected -> mail to the employee (with the reason when rejected).
"""

from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from horilla.test_access_control import HX, AccessTestCase
from horilla_documents import mails
from horilla_documents.models import Document, DocumentRequest

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def inline(func, *args, **kwargs):
    return func(*args, **kwargs)


@override_settings(STORAGES=PLAIN_STATIC)
class EmployeeDocumentMailTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        self.hr_user.is_superuser = True
        self.hr_user.save()
        self.request_obj = DocumentRequest.objects.create(
            title="Address proof", format="pdf", max_size=3, description="Recent", created_by=self.hr_user
        )
        self.request_obj.employee_id.add(self.owner, self.stranger)
        self.document = Document.objects.get(employee_id=self.owner, document_request_id=self.request_obj) if Document.objects.filter(
            employee_id=self.owner, document_request_id=self.request_obj
        ).exists() else Document.objects.create(
            title="Upload Address proof", employee_id=self.owner, document_request_id=self.request_obj
        )
        self.document.document = SimpleUploadedFile("a.pdf", b"%PDF-1.4 x")
        self.document.save()

    # ---- the mails themselves
    def test_every_employee_asked_gets_the_request(self):
        mails.notify_documents_requested(self.request_obj, "https://hr.test/employee/employee-profile/")
        recipients = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(recipients, sorted([self.owner.get_mail(), self.stranger.get_mail()]))
        html = mail.outbox[0].alternatives[0][0]
        for text in ("Address proof", "PDF", "3 MB", "Recent", "https://hr.test/employee/employee-profile/"):
            self.assertIn(text, html)

    def test_the_request_creator_is_told_about_an_upload(self):
        mails.notify_document_submitted(self.document, "https://hr.test/docs/")
        self.assertEqual([m.to for m in mail.outbox], [[self.hr.get_mail()]])
        self.assertIn(self.owner.get_full_name(), mail.outbox[0].subject)

    def test_the_employee_is_told_when_a_document_is_accepted(self):
        mails.notify_document_reviewed(self.document, approved=True)
        self.assertEqual(mail.outbox[0].to, [self.owner.get_mail()])
        self.assertIn("accepted", mail.outbox[0].subject.lower())

    def test_the_employee_is_told_why_a_document_was_rejected(self):
        self.document.reject_reason = "Photo is blurry"
        mails.notify_document_reviewed(self.document, approved=False)
        self.assertIn("Photo is blurry", mail.outbox[0].alternatives[0][0])
        self.assertIn("not accepted", mail.outbox[0].subject)

    def test_no_creator_means_no_submission_mail(self):
        DocumentRequest.objects.filter(pk=self.request_obj.pk).update(created_by=None)
        self.request_obj.refresh_from_db()
        self.assertFalse(mails.notify_document_submitted(self.document, "https://hr.test/"))
        self.assertEqual(len(mail.outbox), 0)

    def test_what_staff_typed_cannot_inject_html_into_the_mail(self):
        self.document.reject_reason = "<script>alert(1)</script>"
        mails.notify_document_reviewed(self.document, approved=False)
        self.assertNotIn("<script>alert(1)</script>", mail.outbox[0].alternatives[0][0])

    # ---- the places that send them
    def test_approving_a_document_mails_the_employee(self):
        self.client.force_login(self.hr_user)
        with mock.patch("employee.views.run_in_background", side_effect=inline):
            self.client.post(reverse("document-approve", kwargs={"id": self.document.pk}), **HX)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, "approved")
        self.assertEqual([m.to for m in mail.outbox], [[self.owner.get_mail()]])

    def test_bulk_approve_and_bulk_reject_mail_each_employee(self):
        other = Document.objects.filter(employee_id=self.stranger, document_request_id=self.request_obj).first() or (
            Document.objects.create(title="Upload proof", employee_id=self.stranger, document_request_id=self.request_obj)
        )
        other.document = SimpleUploadedFile("b.pdf", b"%PDF-1.4 y")
        other.save()
        self.client.force_login(self.hr_user)
        with mock.patch("employee.views.run_in_background", side_effect=inline):
            self.client.post(reverse("document-bulk-approve"), {"ids": [self.document.pk, other.pk]}, **HX)
            self.assertEqual(sorted(m.to[0] for m in mail.outbox), sorted([self.owner.get_mail(), self.stranger.get_mail()]))
            mail.outbox.clear()
            self.client.post(
                reverse("document-bulk-reject"),
                {"ids": [self.document.pk, other.pk], "reject_reason": "Not legible"}, **HX,
            )
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Not legible", mail.outbox[0].alternatives[0][0])
