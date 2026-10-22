"""
The User Group page must stay light: a group's permission table is fetched
when its panel is opened, not rendered for every group up front.
"""

from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse

from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee


class GroupPageTests(IsolatedTestCase):
    def setUp(self):
        self.user, _ = make_employee("group_page_viewer")
        grant(self.user, "auth", "view_group")
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.user)
        self.group = Group.objects.create(name="Page group")
        self.group.permissions.add(Permission.objects.get(codename="view_employee"))

    def test_the_list_carries_no_permission_tables(self):
        response = self.client.get(reverse("user-group-view"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('name="permissions"', html)
        self.assertIn(reverse("user-group-permissions", args=[self.group.pk]), html)
        self.assertNotIn("ui-avatars.com/api/?name=Employee", html)

    def test_opening_a_group_loads_its_table_with_its_permissions_ticked(self):
        response = self.client.get(
            reverse("user-group-permissions", args=[self.group.pk]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('value="view_employee"', html)
        self.assertIn('["view_employee"]', html)
        self.assertIn(f"#panel{self.group.pk}", html)

    def test_the_table_needs_the_view_group_permission(self):
        other, _ = make_employee("group_page_outsider")
        client = Client(raise_request_exception=False)
        client.force_login(other)
        response = client.get(
            reverse("user-group-permissions", args=[self.group.pk]), HTTP_HX_REQUEST="true"
        )
        # The no-access handler answers HTMX requests with a page, so check what is missing.
        self.assertNotIn('name="permissions"', response.content.decode())

    def test_an_unknown_group_is_a_404(self):
        response = self.client.get(
            reverse("user-group-permissions", args=[999999]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 404)
