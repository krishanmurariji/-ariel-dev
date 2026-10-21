"""
/media/ no longer hands a file to any signed-in user: files that hold personal data go only to
their owner, the owner's managers, and holders of the view permission.
"""

import tempfile

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from horilla.isolated_testcase import IsolatedTestCase
from horilla.media_access import RULES
from horilla.test_access_control import grant, make_employee

MEDIA = tempfile.mkdtemp(prefix="media_test_")


@override_settings(MEDIA_ROOT=MEDIA + "/")
class DocumentFileTests(IsolatedTestCase):
    def setUp(self):
        from horilla_documents.models import Document

        self.manager_user, manager = make_employee("md_manager")
        self.owner_user, owner = make_employee("md_owner", manager=manager)
        self.stranger_user, _ = make_employee("md_stranger")
        self.hr_user, _ = make_employee("md_hr")
        grant(self.hr_user, "horilla_documents", "view_document")
        self.admin_user, _ = make_employee("md_admin")
        self.admin_user.is_superuser = True
        self.admin_user.save()
        document = Document(title="Passport", employee_id=owner)
        document.document.save("passport.pdf", SimpleUploadedFile("passport.pdf", b"%PDF-1.4 secret"), save=False)
        document.save()
        self.url = "/media/" + document.document.name

    def fetch(self, user):
        client = Client()
        client.force_login(user)
        return client.get(self.url)

    def test_the_owner_can_open_it(self):
        self.assertEqual(self.fetch(self.owner_user).status_code, 200)

    def test_the_reporting_manager_can_open_it(self):
        self.assertEqual(self.fetch(self.manager_user).status_code, 200)

    def test_a_holder_of_the_view_permission_can_open_it(self):
        self.assertEqual(self.fetch(self.hr_user).status_code, 200)

    def test_a_superuser_can_open_it(self):
        self.assertEqual(self.fetch(self.admin_user).status_code, 200)

    def test_another_employee_cannot_open_it_even_with_the_url(self):
        self.assertEqual(self.fetch(self.stranger_user).status_code, 404)

    def test_an_anonymous_visitor_is_sent_to_login(self):
        self.assertEqual(Client().get(self.url).status_code, 302)


@override_settings(MEDIA_ROOT=MEDIA + "/")
class CandidateResumeTests(IsolatedTestCase):
    def setUp(self):
        from recruitment.factories import CandidateFactory

        self.candidate = CandidateFactory()
        self.candidate.resume.save("cv.pdf", SimpleUploadedFile("cv.pdf", b"%PDF-1.4 cv"), save=True)
        self.url = "/media/" + self.candidate.resume.name
        self.stranger, _ = make_employee("cv_stranger")
        self.recruiter, recruiter = make_employee("cv_recruiter")
        self.candidate.recruitment_id.recruitment_managers.add(recruiter)
        self.viewer, _ = make_employee("cv_viewer")
        grant(self.viewer, "recruitment", "view_candidate")

    def fetch(self, user):
        client = Client()
        client.force_login(user)
        return client.get(self.url)

    def test_the_recruitment_manager_can_open_the_resume(self):
        self.assertEqual(self.fetch(self.recruiter).status_code, 200)

    def test_a_holder_of_view_candidate_can_open_the_resume(self):
        self.assertEqual(self.fetch(self.viewer).status_code, 200)

    def test_an_unrelated_employee_cannot(self):
        self.assertEqual(self.fetch(self.stranger).status_code, 404)


class UnruledFoldersKeepWorking(IsolatedTestCase):
    def test_a_folder_without_a_rule_is_open_to_any_signed_in_user(self):
        from horilla.media_access import may_open

        user, _ = make_employee("plain_media")
        self.assertTrue(may_open(user, "project/project/file/plan-1a2b3c4d.pdf"))
        self.assertTrue(may_open(user, "employee/employee/employee_profile/me.png"))
        # another field of a ruled model, and a three-part path nobody owns
        self.assertTrue(may_open(user, "recruitment/candidate/profile_thumbnail/t-1a2b3c4d.png"))
        self.assertTrue(may_open(user, "recruitment/candidate/some-photo-1a2b3c4d.png"))

    def test_an_orphaned_file_of_a_ruled_field_is_not_handed_out(self):
        from horilla.media_access import may_open

        user, _ = make_employee("orphan_media")
        self.assertFalse(may_open(user, "horilla_documents/document/document/gone-1a2b3c4d.pdf"))

    def test_every_rule_names_a_real_file_field(self):
        for app_label, model_name, field in RULES:
            model = apps.get_model(app_label, model_name)
            self.assertTrue(model._meta.get_field(field).get_internal_type() in ("FileField", "ImageField"), (app_label, model_name, field))
