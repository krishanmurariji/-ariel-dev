"""
vertex/tests/test_roles.py

The role/capability matrix must match ArielVertex, and only users holding
``roles.manage`` (Super Admin, HR Director) may change roles.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from employee.models import Employee
from horilla.horilla_middlewares import _thread_locals, set_selected_company
from vertex.access import has_cap, portal_role
from vertex.models import VertexProfile
from vertex.roles import ALL_CAPS, Cap, PortalRole, capabilities_for, dashboard_for

PASSWORD = "123qwe"
User = get_user_model()


def reset_request_state():
    """A previous test's request leaks its user/company through context variables."""
    _thread_locals.request = None
    set_selected_company(None)


def make_employee(first_name, email, phone, role=None):
    employee = Employee.objects.create(
        employee_first_name=first_name, email=email, phone=phone
    )
    user = employee.employee_user_id
    user.is_new_employee = False  # else a middleware forces /change-password/
    user.set_password(PASSWORD)
    user.save()
    if role:
        VertexProfile.objects.create(employee=employee, portal_role=role)
    return Employee.objects.get(pk=employee.pk)


class CapabilityMatrixTests(TestCase):
    """Numbers and rules below are read off ArielVertex Permissions.cs."""

    def test_catalogue_has_46_capabilities(self):
        self.assertEqual(len(ALL_CAPS), 46)

    def test_super_admin_and_hr_director_get_everything_except_contributor_caps(self):
        expected = ALL_CAPS - {Cap.STATUS_SUBMIT, Cap.PERFORMANCE_VIEW_OWN}
        self.assertEqual(capabilities_for(PortalRole.SUPER_ADMIN), expected)
        self.assertEqual(capabilities_for(PortalRole.HR_DIRECTOR), expected)

    def test_only_super_admin_and_hr_director_can_manage_roles(self):
        holders = {
            role for role in PortalRole.values if Cap.ROLES_MANAGE in capabilities_for(role)
        }
        self.assertEqual(holders, {PortalRole.SUPER_ADMIN, PortalRole.HR_DIRECTOR})

    def test_hr_manager_cannot_manage_roles_but_owns_the_performance_lifecycle(self):
        caps = capabilities_for(PortalRole.HR_MANAGER)
        self.assertNotIn(Cap.ROLES_MANAGE, caps)
        for cap in (Cap.CYCLES_MANAGE, Cap.APPRAISALS_RELEASE, Cap.PROMOTIONS_MANAGE):
            self.assertIn(cap, caps)

    def test_project_manager_and_coordinator_share_delivery_capabilities(self):
        self.assertEqual(
            capabilities_for(PortalRole.PROJECT_MANAGER),
            capabilities_for(PortalRole.PROJECT_COORDINATOR),
        )
        self.assertIn(Cap.PROJECTS_CREATE, capabilities_for(PortalRole.PROJECT_MANAGER))

    def test_business_roles_only_view_projects(self):
        for role in (
            PortalRole.BUSINESS_DIRECTOR,
            PortalRole.BUSINESS_MANAGER,
            PortalRole.BUSINESS_PERSON,
        ):
            self.assertEqual(capabilities_for(role), {Cap.PROJECTS_VIEW})

    def test_employee_can_submit_status_and_see_own_performance_only(self):
        self.assertEqual(
            capabilities_for(PortalRole.EMPLOYEE),
            {Cap.PROJECTS_VIEW, Cap.STATUS_SUBMIT, Cap.PERFORMANCE_VIEW_OWN},
        )

    def test_unknown_role_falls_back_to_employee(self):
        self.assertEqual(
            capabilities_for("nonsense"), capabilities_for(PortalRole.EMPLOYEE)
        )

    def test_every_role_is_defined(self):
        for role in PortalRole.values:
            self.assertTrue(capabilities_for(role))

    def test_dashboard_landing_per_role(self):
        self.assertEqual(dashboard_for(PortalRole.CEO_ADMIN), "admin")
        self.assertEqual(dashboard_for(PortalRole.HR_MANAGER), "hr")
        self.assertEqual(dashboard_for(PortalRole.PROJECT_MANAGER), "delivery")
        self.assertEqual(dashboard_for(PortalRole.BUSINESS_PERSON), "business")
        self.assertEqual(dashboard_for(PortalRole.FRONTDESK), "employee")


class RoleResolutionTests(TestCase):
    def setUp(self):
        reset_request_state()

    def test_no_profile_means_plain_employee(self):
        employee = make_employee("Plain", "plain@example.com", "9000000001")
        self.assertEqual(portal_role(employee.employee_user_id), PortalRole.EMPLOYEE)

    def test_profile_role_is_used(self):
        employee = make_employee(
            "Pm", "pm@example.com", "9000000002", PortalRole.PROJECT_MANAGER
        )
        self.assertEqual(
            portal_role(employee.employee_user_id), PortalRole.PROJECT_MANAGER
        )
        self.assertTrue(has_cap(employee.employee_user_id, Cap.PROJECTS_CREATE))

    def test_superuser_acts_as_super_admin(self):
        user = User.objects.create_superuser(username="root", email="root@example.com", password=PASSWORD)
        self.assertEqual(portal_role(user), PortalRole.SUPER_ADMIN)

    def test_user_without_employee_is_plain_employee(self):
        user = User.objects.create_user(username="noemp", email="noemp@example.com", password=PASSWORD)
        self.assertEqual(portal_role(user), PortalRole.EMPLOYEE)


class RolesPageAccessTests(TestCase):
    def setUp(self):
        reset_request_state()
        self.director = make_employee(
            "Director", "dir@example.com", "9000000011", PortalRole.HR_DIRECTOR
        )
        self.hr_manager = make_employee(
            "Hr", "hr@example.com", "9000000012", PortalRole.HR_MANAGER
        )
        self.worker = make_employee("Worker", "worker@example.com", "9000000013")

    def login(self, employee):
        client = Client()
        self.assertTrue(
            client.login(username=employee.employee_user_id.username, password=PASSWORD)
        )
        return client

    def test_hr_director_sees_the_roles_page(self):
        response = self.login(self.director).get(reverse("vertex-roles"))
        self.assertEqual(response.status_code, 200)

    def test_hr_manager_and_employee_are_turned_away(self):
        for person in (self.hr_manager, self.worker):
            response = self.login(person).get(reverse("vertex-roles"))
            self.assertNotEqual(response.status_code, 200)

    def test_hr_director_can_change_a_role(self):
        response = self.login(self.director).post(
            reverse("vertex-role-update", args=[self.worker.pk]),
            {"portal_role": PortalRole.TECHNICAL_LEAD},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            VertexProfile.objects.get(employee=self.worker).portal_role,
            PortalRole.TECHNICAL_LEAD,
        )

    def test_unknown_role_value_is_rejected(self):
        self.login(self.director).post(
            reverse("vertex-role-update", args=[self.worker.pk]),
            {"portal_role": "root"},
        )
        self.assertFalse(VertexProfile.objects.filter(employee=self.worker).exists())

    def test_hr_manager_cannot_change_roles(self):
        self.login(self.hr_manager).post(
            reverse("vertex-role-update", args=[self.worker.pk]),
            {"portal_role": PortalRole.SUPER_ADMIN},
        )
        self.assertFalse(VertexProfile.objects.filter(employee=self.worker).exists())

    def test_role_change_by_get_is_refused(self):
        response = self.login(self.director).get(
            reverse("vertex-role-update", args=[self.worker.pk]),
            {"portal_role": PortalRole.SUPER_ADMIN},
        )
        self.assertEqual(response.status_code, 405)
        self.assertFalse(VertexProfile.objects.filter(employee=self.worker).exists())
