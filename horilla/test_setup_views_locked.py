"""
The first-run setup screens have no login. On a system that is already set up they used to
answer anyone: an anonymous visitor could take a CSRF token from the login page, POST to
/initialize-database-user/ and be handed a new superuser. They now answer only while the
system is empty (or to the signed-in administrator the wizard just created).

The LDAP settings page used to be open to every signed-in employee.
"""

from django.test import Client
from django.urls import reverse

from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from horilla_auth.models import HorillaUser

HX = {"HTTP_HX_REQUEST": "true"}
SETUP_URLS = [
    ("initialize-database-user", []),
    ("initialize-database-company", []),
    ("initialize-database-department", []),
    ("initialize-department-edit", [1]),
    ("initialize-department-delete", [1]),
    ("initialize-database-job-position", []),
    ("initialize-job-position-edit", [1]),
    ("initialize-job-position-delete", [1]),
]
ATTACK = {
    "username": "attacker",
    "password": "Passw0rd!x",
    "confirm_password": "Passw0rd!x",
    "firstname": "a",
    "lastname": "b",
    "badge_id": "X1",
    "email": "attacker@example.com",
    "phone": "1234567890",
}


def superuser(username):
    user, _ = make_employee(username)
    user.is_superuser = True
    user.is_staff = True
    user.save()
    return user


def csrf_client():
    client = Client(enforce_csrf_checks=True)
    client.get(reverse("login"))
    return client, client.cookies["csrftoken"].value


class SetupScreensOnALiveSystem(IsolatedTestCase):
    def setUp(self):
        superuser("setup_admin")

    def test_an_anonymous_visitor_cannot_create_a_superuser(self):
        client, token = csrf_client()
        before = HorillaUser.objects.filter(is_superuser=True).count()
        response = client.post(reverse("initialize-database-user"), {**ATTACK, "csrfmiddlewaretoken": token}, **HX)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(HorillaUser.objects.filter(is_superuser=True).count(), before)
        self.assertFalse(HorillaUser.objects.filter(username="attacker").exists())

    def test_every_setup_screen_is_closed_to_an_anonymous_visitor(self):
        client = Client()
        for name, args in SETUP_URLS:
            for method in ("get", "post"):
                response = getattr(client, method)(reverse(name, args=args), **HX)
                self.assertIn(response.status_code, (404, 405), f"{method} {name}")

    def test_every_setup_screen_is_closed_to_an_ordinary_employee(self):
        user, _ = make_employee("setup_plain")
        client = Client()
        client.force_login(user)
        for name, args in SETUP_URLS:
            response = client.get(reverse(name, args=args), **HX)
            self.assertIn(response.status_code, (404, 405), name)

    def test_the_signed_in_administrator_can_still_finish_setup(self):
        client = Client()
        client.force_login(HorillaUser.objects.get(username="setup_admin"))
        for name in ("initialize-database-company", "initialize-database-department", "initialize-database-job-position"):
            self.assertEqual(client.get(reverse(name), **HX).status_code, 200, name)

    def test_the_user_step_stays_closed_even_to_a_superuser(self):
        client = Client()
        client.force_login(HorillaUser.objects.get(username="setup_admin"))
        self.assertEqual(client.get(reverse("initialize-database-user"), **HX).status_code, 404)


class SetupScreensOnAnEmptySystem(IsolatedTestCase):
    def test_the_wizard_still_works_before_anyone_exists(self):
        HorillaUser.objects.all().delete()
        client, token = csrf_client()
        self.assertEqual(client.get(reverse("initialize-database-user"), **HX).status_code, 200)
        response = client.post(reverse("initialize-database-user"), {**ATTACK, "csrfmiddlewaretoken": token}, **HX)
        self.assertEqual(response.status_code, 200)
        created = HorillaUser.objects.get(username="attacker")
        self.assertTrue(created.is_superuser)
        # and the wizard's next step is open to the administrator it just signed in
        self.assertEqual(client.get(reverse("initialize-database-company"), **HX).status_code, 200)


class LdapSettingsPage(IsolatedTestCase):
    def setUp(self):
        from horilla_ldap.models import LDAPSettings

        LDAPSettings.objects.all().delete()
        LDAPSettings.objects.create(ldap_server="ldap://original", bind_dn="cn=orig", bind_password="pw", base_dn="ou=o")
        user, _ = make_employee("ldap_plain")
        self.plain = Client()
        self.plain.force_login(user)
        self.admin = Client()
        self.admin.force_login(superuser("ldap_admin"))

    def test_an_ordinary_employee_cannot_read_the_page(self):
        body = self.plain.get("/settings/ldap-settings/").content.decode()
        self.assertNotIn('name="bind_dn"', body)
        self.assertNotIn("cn=orig", body)

    def test_an_ordinary_employee_cannot_change_the_settings(self):
        from horilla_ldap.models import LDAPSettings

        self.plain.post(
            "/settings/ldap-settings/",
            {"ldap_server": "ldap://evil", "bind_dn": "cn=evil", "bind_password": "x", "base_dn": "ou=evil"},
        )
        self.assertEqual(LDAPSettings.objects.first().ldap_server, "ldap://original")

    def test_an_administrator_still_can(self):
        body = self.admin.get("/settings/ldap-settings/").content.decode()
        self.assertIn('name="bind_dn"', body)
