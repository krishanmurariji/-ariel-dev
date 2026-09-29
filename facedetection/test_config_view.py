"""
Two bugs in FaceDetectionConfigAPIView / face_detection_config, found while
checking the security review's M-10 finding against the current code:

* DELETE crashed every time: get_facedetection() returns the (instance,
  created) tuple get_or_create() gives back, and delete() was called on
  that tuple instead of the instance.
* face_detection_config required "geofencing.add_localbackup" - a
  copy-paste from a different feature's permission - instead of
  "facedetection.add_facedetection", the permission every other action in
  this file already uses.
"""

from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from base.models import Company
from employee.models import Employee, EmployeeWorkInformation
from facedetection.models import FaceDetection
from horilla.isolated_testcase import IsolatedTestCase
from horilla_auth.models import HorillaUser


def make_user_with_perm(username, codename, company):
    user = HorillaUser.objects.create(username=username, is_active=True)
    employee = Employee.objects.create(
        employee_first_name="Test",
        employee_last_name=username,
        employee_user_id=user,
        email=f"{username}@example.com",
    )
    # get_company() (facedetection/views.py) resolves through
    # employee_work_info.company_id - without this, every request fails at
    # get_company() with a 400 before the delete()/permission bug is even
    # reached. Employee.save() already auto-creates a blank work-info row
    # (a signal), so this updates it rather than creating a second one.
    EmployeeWorkInformation.objects.update_or_create(
        employee_id=employee, defaults={"company_id": company}
    )
    user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def bearer_header(user):
    # FaceDetectionConfigAPIView is JWT-only (DEFAULT_AUTHENTICATION_CLASSES
    # has no SessionAuthentication) - force_login()'s session cookie does
    # not satisfy IsAuthenticated here.
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class DeleteNoLongerCrashesTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Acme", address="a", country="c", state="s", city="c", zip="1"
        )

    def test_delete_succeeds_and_removes_the_row(self):
        user = make_user_with_perm("fd_delete", "delete_facedetection", self.company)
        FaceDetection.objects.create(company_id=self.company, start=True)

        client = Client()
        response = client.delete("/api/facedetection/config/", **bearer_header(user))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FaceDetection.objects.filter(company_id=self.company).exists())

    def test_delete_when_no_row_exists_yet_still_does_not_crash(self):
        # get_or_create() creates one on the fly - covers that path too.
        user = make_user_with_perm("fd_delete_empty", "delete_facedetection", self.company)
        client = Client()
        response = client.delete("/api/facedetection/config/", **bearer_header(user))
        self.assertEqual(response.status_code, 200)


class ConfigPagePermissionTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Acme2", address="a", country="c", state="s", city="c", zip="1"
        )

    def _url(self):
        return reverse("face-config")

    def test_the_geofencing_permission_no_longer_grants_access(self):
        # permission_required() (Django's own decorator, no
        # raise_exception=True) redirects rather than 403s when the caller
        # is logged in but lacks the permission - what matters is that this
        # unrelated permission no longer lets them through to the page.
        user = make_user_with_perm("fd_wrong_perm", "add_localbackup", self.company)
        client = Client()
        client.force_login(user)
        response = client.get(self._url(), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 302)

    def test_the_facedetection_permission_grants_access(self):
        user = make_user_with_perm("fd_right_perm", "add_facedetection", self.company)
        client = Client()
        client.force_login(user)
        response = client.get(self._url(), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
