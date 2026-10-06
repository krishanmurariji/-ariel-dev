"""
TaxBracketView used to rely on the default IsAuthenticated alone, so any
employee with a JWT could list, create, edit and delete the payroll tax
brackets. Each method now needs the same permission the web screens use.

Permission is checked before the object is looked up, so a user without it
gets 403 even for an id that does not exist, and a user with it gets 404.
"""

from django.contrib.auth.models import Permission
from rest_framework.test import APIClient

from horilla.isolated_testcase import IsolatedTestCase
from horilla_auth.models import HorillaUser

LIST = "/api/payroll/tax-bracket/"
MISSING = "/api/payroll/tax-bracket/999999"


def grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="payroll", codename=codename
            )
        )


class TaxBracketPermissionTests(IsolatedTestCase):
    def setUp(self):
        self.plain = HorillaUser.objects.create(username="tb_plain", is_active=True)
        self.hr = HorillaUser.objects.create(username="tb_hr", is_active=True)
        grant(
            self.hr,
            "view_taxbracket",
            "add_taxbracket",
            "change_taxbracket",
            "delete_taxbracket",
        )

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_user_without_permission_is_refused_on_every_method(self):
        c = self.client_for(self.plain)
        self.assertEqual(c.get(LIST).status_code, 403)
        self.assertEqual(c.get(MISSING).status_code, 403)
        self.assertEqual(c.post(LIST, {}, format="json").status_code, 403)
        self.assertEqual(c.put(MISSING, {}, format="json").status_code, 403)
        self.assertEqual(c.delete(MISSING).status_code, 403)

    def test_anonymous_is_refused(self):
        c = APIClient()
        self.assertIn(c.get(LIST).status_code, (401, 403))
        self.assertIn(c.delete(MISSING).status_code, (401, 403))

    def test_user_with_permission_gets_past_the_check(self):
        c = self.client_for(self.hr)
        self.assertEqual(c.get(LIST).status_code, 200)
        self.assertEqual(c.get(MISSING).status_code, 404)
        self.assertEqual(c.post(LIST, {}, format="json").status_code, 400)
        self.assertEqual(c.put(MISSING, {}, format="json").status_code, 404)
        self.assertEqual(c.delete(MISSING).status_code, 404)
