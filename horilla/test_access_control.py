"""
Object-level access control.

These views fetched a record by id behind ``@login_required`` only, so any
employee could read or change another employee's data just by changing the id
in the URL. Each of them now allows only the owner, the owner's reporting
manager, or a holder of the relevant permission.

Four kinds of user are used throughout:
    stranger  - an ordinary employee with no relationship to the record
    owner     - the employee the record belongs to
    manager   - the owner's reporting manager
    hr        - holds the relevant permission
"""

import datetime

from django.contrib.auth.models import Permission
from django.http import Http404
from django.test import Client
from django.urls import reverse

from base.models import (
    Announcement,
    EmployeeShift,
    HorillaMailTemplate,
    ShiftRequest,
    WorkType,
    WorkTypeRequest,
)
from employee.models import Employee, EmployeeWorkInformation
from helpdesk.access import can_view_ticket
from helpdesk.models import Ticket, TicketType
from horilla.access import (
    can_access_employee,
    check_employee_access,
    is_reporting_manager_of,
)
from horilla.isolated_testcase import IsolatedTestCase
from horilla_auth.models import HorillaUser
from horilla_documents.models import Document

HX = {"HTTP_HX_REQUEST": "true"}


def make_employee(username, manager=None):
    user = HorillaUser.objects.create(username=username, is_active=True)
    emp = Employee.objects.create(
        employee_first_name=username,
        employee_last_name="Test",
        employee_user_id=user,
        email=f"{username}@example.com",
    )
    # Creating an Employee already creates its work-information record.
    work_info, _ = EmployeeWorkInformation.objects.get_or_create(employee_id=emp)
    work_info.reporting_manager_id = manager
    work_info.save()
    return user, Employee.objects.get(pk=emp.pk)


def grant(user, app_label, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=app_label, codename=codename
            )
        )


class AccessTestCase(IsolatedTestCase):
    def setUp(self):
        self.mgr_user, self.mgr = make_employee("acc_manager")
        self.owner_user, self.owner = make_employee("acc_owner", manager=self.mgr)
        self.stranger_user, self.stranger = make_employee("acc_stranger")
        self.hr_user, self.hr = make_employee("acc_hr")

    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client


class EmployeeHelperTests(AccessTestCase):
    def test_reporting_manager_detection(self):
        self.assertTrue(is_reporting_manager_of(self.mgr_user, self.owner))
        self.assertFalse(is_reporting_manager_of(self.stranger_user, self.owner))
        self.assertFalse(is_reporting_manager_of(self.owner_user, self.mgr))

    def test_can_access_employee(self):
        grant(self.hr_user, "employee", "view_employee")
        self.assertTrue(can_access_employee(self.owner_user, self.owner))
        self.assertTrue(can_access_employee(self.mgr_user, self.owner))
        self.assertTrue(can_access_employee(self.hr_user, self.owner))
        self.assertFalse(can_access_employee(self.stranger_user, self.owner))
        self.assertFalse(can_access_employee(self.stranger_user, None))

    def test_permission_is_checked_per_call(self):
        grant(self.stranger_user, "leave", "view_leaverequest")
        self.assertFalse(can_access_employee(self.stranger_user, self.owner))
        self.assertTrue(
            can_access_employee(
                self.stranger_user, self.owner, perm="leave.view_leaverequest"
            )
        )


class EmployeeProfileTabTests(AccessTestCase):
    """about_tab shows personal details, contracts and leave balances."""

    def url(self):
        return reverse("about-tab", kwargs={"pk": self.owner.pk})

    def test_stranger_cannot_read_another_employees_tab(self):
        r = self.client_for(self.stranger_user).get(self.url(), **HX)
        self.assertEqual(r.status_code, 404)

    def test_owner_manager_and_hr_can(self):
        grant(self.hr_user, "employee", "view_employee")
        for user in (self.owner_user, self.mgr_user, self.hr_user):
            r = self.client_for(user).get(self.url(), **HX)
            self.assertEqual(r.status_code, 200, user.username)

    def test_missing_employee_is_404_not_500(self):
        url = reverse("about-tab", kwargs={"pk": 999999})
        self.assertEqual(
            self.client_for(self.hr_user).get(url, **HX).status_code, 404
        )


class DocumentTitleTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        self.doc = Document.objects.create(title="Original", employee_id=self.owner)
        self.url = reverse("update-document-title", kwargs={"id": self.doc.pk})

    def test_stranger_cannot_rename_someone_elses_document(self):
        r = self.client_for(self.stranger_user).post(self.url, {"title": "Hacked"})
        self.assertEqual(r.status_code, 404)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.title, "Original")

    def test_owner_and_manager_and_permission_holder_can(self):
        grant(self.hr_user, "horilla_documents", "change_document")
        for i, user in enumerate((self.owner_user, self.mgr_user, self.hr_user)):
            r = self.client_for(user).post(self.url, {"title": f"Renamed {i}"})
            self.assertEqual(r.status_code, 200, user.username)
            self.doc.refresh_from_db()
            self.assertEqual(self.doc.title, f"Renamed {i}")


class AnnouncementDeleteTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        self.ann = Announcement.objects.create(title="Notice", description="<p>x</p>")
        self.url = reverse("delete-announcement", kwargs={"anoun_id": self.ann.pk})

    def test_ordinary_employee_cannot_delete_an_announcement(self):
        self.client_for(self.stranger_user).get(self.url, **HX)
        self.assertTrue(Announcement.objects.filter(pk=self.ann.pk).exists())

    def test_permission_holder_can(self):
        grant(self.hr_user, "base", "delete_announcement")
        self.client_for(self.hr_user).get(self.url, **HX)
        self.assertFalse(Announcement.objects.filter(pk=self.ann.pk).exists())


class TicketVisibilityTests(AccessTestCase):
    def make_ticket(self, **kwargs):
        # save() on these models expects to run inside a web request, so insert
        # the rows directly.
        ticket_type = TicketType.objects.bulk_create(
            [TicketType(title="IT", type="others", prefix="IT")]
        )[0]
        defaults = dict(
            title="Printer",
            employee_id=self.owner,
            ticket_type=ticket_type,
            description="broken",
            assigning_type="individual",
            raised_on=str(self.hr.pk),
        )
        defaults.update(kwargs)
        return Ticket.objects.bulk_create([Ticket(**defaults)])[0]

    def test_unrelated_employee_cannot_view(self):
        ticket = self.make_ticket()
        self.assertFalse(can_view_ticket(self.stranger_user, ticket))

    def test_raiser_manager_and_forward_target_can(self):
        ticket = self.make_ticket()
        self.assertTrue(can_view_ticket(self.owner_user, ticket))
        self.assertTrue(can_view_ticket(self.mgr_user, ticket))
        self.assertTrue(can_view_ticket(self.hr_user, ticket))  # forwarded to them

    def test_assignee_and_permission_holder_can(self):
        ticket = self.make_ticket()
        ticket.assigned_to.add(self.stranger)
        self.assertTrue(can_view_ticket(self.stranger_user, ticket))
        other_user, _ = make_employee("acc_helpdesk_admin")
        grant(other_user, "helpdesk", "view_ticket")
        self.assertTrue(can_view_ticket(other_user, ticket))

    def test_view_returns_404_for_unrelated_employee(self):
        ticket = self.make_ticket()
        url = reverse("ticket-individual-view", kwargs={"ticket_id": ticket.pk})
        self.assertEqual(self.client_for(self.stranger_user).get(url).status_code, 404)
        self.assertEqual(self.client_for(self.owner_user).get(url).status_code, 200)


# ---------------------------------------------------------------------------
# Second batch: shift / work-type requests, and the permission-gated views.
# ---------------------------------------------------------------------------


class CheckEmployeeAccessTests(AccessTestCase):
    def test_raises_404_for_a_stranger_and_not_for_the_allowed(self):
        grant(self.hr_user, "base", "view_shiftrequest")
        with self.assertRaises(Http404):
            check_employee_access(self.stranger_user, self.owner, "base.view_shiftrequest")
        for user in (self.owner_user, self.mgr_user, self.hr_user):
            check_employee_access(user, self.owner, "base.view_shiftrequest")

    def test_involved_party_listed_in_also_allow_is_let_in(self):
        check_employee_access(
            self.stranger_user, self.owner, "base.view_shiftrequest", also_allow=[self.stranger]
        )
        with self.assertRaises(Http404):
            check_employee_access(
                self.stranger_user, self.owner, "base.view_shiftrequest", also_allow=[None, self.hr]
            )

    def test_missing_record_owner_is_refused(self):
        with self.assertRaises(Http404):
            check_employee_access(self.hr_user, None, "base.view_shiftrequest")


class RequestDetailViewTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        self.partner_user, self.partner = make_employee("acc_swap_partner")
        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="Day")])[0]
        today = datetime.date.today()
        self.shift_request = ShiftRequest.objects.bulk_create(
            [
                ShiftRequest(
                    employee_id=self.owner,
                    shift_id=shift,
                    requested_date=today,
                    requested_till=today,
                    reallocate_to=self.partner,
                )
            ]
        )[0]
        work_type = WorkType.objects.bulk_create([WorkType(work_type="Office")])[0]
        self.work_type_request = WorkTypeRequest.objects.bulk_create(
            [
                WorkTypeRequest(
                    employee_id=self.owner,
                    work_type_id=work_type,
                    requested_date=today,
                    requested_till=today,
                )
            ]
        )[0]

    def get(self, user, url):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        return client.get(url, **HX)

    def test_shift_request_details(self):
        url = reverse("shift-request-details", kwargs={"id": self.shift_request.pk})
        self.assertEqual(self.get(self.stranger_user, url).status_code, 404)
        # Allowed users are not stopped by the check (the page itself may need
        # more data than this test builds, so only "not 404" is asserted).
        for user in (self.owner_user, self.mgr_user, self.partner_user):
            self.assertNotEqual(self.get(user, url).status_code, 404, user.username)

    def test_work_type_request_details(self):
        url = reverse(
            "work-type-request-single-view", kwargs={"obj_id": self.work_type_request.pk}
        )
        self.assertEqual(self.get(self.stranger_user, url).status_code, 404)
        self.assertEqual(self.get(self.partner_user, url).status_code, 404)  # not involved here
        for user in (self.owner_user, self.mgr_user):
            self.assertNotEqual(self.get(user, url).status_code, 404, user.username)


class PermissionGatedViewTests(AccessTestCase):
    def test_mail_template_body_needs_the_employee_change_permission(self):
        template = HorillaMailTemplate.objects.bulk_create(
            [HorillaMailTemplate(title="Secret", body="<p>confidential</p>")]
        )[0]
        url = reverse("get-template-employee", kwargs={"emp_id": template.pk})
        grant(self.hr_user, "employee", "change_employee")
        stranger = self.client_for(self.stranger_user).get(url, **HX)
        self.assertNotIn(b"confidential", stranger.content)
        hr = self.client_for(self.hr_user).get(url, **HX)
        self.assertIn(b"confidential", hr.content)
