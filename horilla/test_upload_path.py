"""
Every FileField/ImageField in the project stores under MEDIA_ROOT, which
nginx serves publicly with no authentication (a known, separately-tracked
gap). upload_path() mitigates that by making the stored path unguessable -
a slugified original filename plus an 8-hex-character random suffix. For a
handful of fields where even the slugified *name* would leak something
sensitive to anyone who saw a leaked URL - a Google service-account
credential, an ID/compliance document, an offer letter, a biometric face
image - sensitive_upload_path() goes further and drops the original
filename entirely, keeping only a full, unguessable UUID.
"""

from django.test import SimpleTestCase

from horilla.models import sensitive_upload_path, upload_path


class _FakeInstance:
    """A stand-in with just enough shape for upload_path/sensitive_upload_path:
    a _meta with app_label/model_name, and a __dict__ that may or may not
    contain a FieldFile-like attribute whose .name matches the upload."""

    class _Meta:
        app_label = "testapp"
        model_name = "testmodel"

    _meta = _Meta()


class _FieldFile:
    def __init__(self, name):
        self.name = name


class UploadPathTests(SimpleTestCase):
    def test_keeps_a_recognizable_slug_and_adds_a_short_random_suffix(self):
        instance = _FakeInstance()
        path = upload_path(instance, "My Resume.pdf")
        self.assertTrue(path.startswith("testapp/testmodel/my-resume-"))
        self.assertTrue(path.endswith(".pdf"))
        suffix = path.rsplit("-", 1)[-1].removesuffix(".pdf")
        self.assertEqual(len(suffix), 8)
        int(suffix, 16)  # must be valid hex

    def test_includes_field_name_when_resolvable(self):
        instance = _FakeInstance()
        instance.__dict__["resume"] = _FieldFile("cv.pdf")
        path = upload_path(instance, "cv.pdf")
        self.assertTrue(path.startswith("testapp/testmodel/resume/"))

    def test_two_uploads_of_the_same_original_name_get_different_paths(self):
        instance = _FakeInstance()
        p1 = upload_path(instance, "resume.pdf")
        p2 = upload_path(instance, "resume.pdf")
        self.assertNotEqual(p1, p2)


class SensitiveUploadPathTests(SimpleTestCase):
    def test_original_filename_does_not_appear_anywhere_in_the_path(self):
        instance = _FakeInstance()
        path = sensitive_upload_path(
            instance, "John Doe - Aadhaar Card - Confidential.pdf"
        )
        self.assertNotIn("john", path.lower())
        self.assertNotIn("aadhaar", path.lower())
        self.assertNotIn("confidential", path.lower())
        self.assertTrue(path.endswith(".pdf"))

    def test_name_is_a_full_uuid_not_a_short_suffix(self):
        instance = _FakeInstance()
        path = sensitive_upload_path(instance, "service-account.json")
        filename = path.rsplit("/", 1)[-1]
        stem = filename.removesuffix(".json")
        self.assertEqual(len(stem), 32)  # uuid4().hex, not the 8-char short suffix
        int(stem, 16)

    def test_includes_field_name_when_resolvable(self):
        instance = _FakeInstance()
        instance.__dict__["document"] = _FieldFile("passport.pdf")
        path = sensitive_upload_path(instance, "passport.pdf")
        self.assertTrue(path.startswith("testapp/testmodel/document/"))

    def test_extensionless_filename_does_not_crash(self):
        instance = _FakeInstance()
        path = sensitive_upload_path(instance, "credentials")
        self.assertTrue(path.endswith(".bin"))

    def test_two_uploads_of_the_same_original_name_get_different_paths(self):
        instance = _FakeInstance()
        p1 = sensitive_upload_path(instance, "offer_letter.pdf")
        p2 = sensitive_upload_path(instance, "offer_letter.pdf")
        self.assertNotEqual(p1, p2)


class SensitiveFieldsWiringTests(SimpleTestCase):
    """Regression guard: the specific fields identified as sensitive stay
    wired to sensitive_upload_path even if the model is later refactored."""

    def test_facedetection_image(self):
        from facedetection.models import EmployeeFaceDetection

        field = EmployeeFaceDetection._meta.get_field("image")
        self.assertIs(field.upload_to, sensitive_upload_path)

    def test_gdrive_service_account_file(self):
        from horilla_backup.models import GoogleDriveBackup

        field = GoogleDriveBackup._meta.get_field("service_account_file")
        self.assertIs(field.upload_to, sensitive_upload_path)

    def test_horilla_documents_document(self):
        from horilla_documents.models import Document

        field = Document._meta.get_field("document")
        self.assertIs(field.upload_to, sensitive_upload_path)

    def test_onboarding_offer_letter(self):
        from onboarding.models import OnboardingCandidateDetail

        field = OnboardingCandidateDetail._meta.get_field("offer_letter")
        self.assertIs(field.upload_to, sensitive_upload_path)

    def test_onboarding_candidate_profile_uses_standard_upload_path(self):
        # Not classified as "sensitive" the way an ID document or a
        # credential is - just needed to stop using a fixed, predictable
        # folder ("employee/profile") with the original filename kept.
        from onboarding.models import OnboardingPortal

        field = OnboardingPortal._meta.get_field("profile")
        self.assertIs(field.upload_to, upload_path)
