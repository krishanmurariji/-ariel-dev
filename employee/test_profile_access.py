"""
Opening someone's profile page: people who may not see that employee's details are told so
up front (and sent back), instead of getting a page of empty tabs that each fail with a 404.
"""

from django.test import override_settings
from django.urls import reverse

from horilla.test_access_control import AccessTestCase, grant


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class ProfilePageAccessTests(AccessTestCase):
    def open(self, user, employee, **extra):
        client = self.client_for(user)
        return client.get(reverse("employee-view-individual", kwargs={"obj_id": employee.pk}), **extra)

    def test_a_colleague_without_access_is_sent_back_with_a_message(self):
        response = self.open(self.stranger_user, self.owner)
        self.assertEqual(response.status_code, 302)
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("You do not have permission to view this employee's profile.", messages)

    def test_over_htmx_the_redirect_is_a_header_not_a_page(self):
        response = self.open(self.stranger_user, self.owner, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Redirect", response.headers)

    def test_the_employee_their_manager_and_someone_with_the_permission_can_open_it(self):
        grant(self.hr_user, "employee", "view_employee")
        for user in (self.owner_user, self.mgr_user, self.hr_user):
            with self.subTest(user=user.username):
                response = self.open(user, self.owner)
                self.assertEqual(response.status_code, 200)
