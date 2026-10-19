"""
Regression tests for four access leaks:

* archived employees could still sign in through the API (the archive views
  never saved the login account, and the API login ignored the archive flag)
* helpdesk ticket status/comment/delete were open to anyone who had ever
  created or been assigned any ticket
* API single-record GETs (resignation letters, feedback, attendance) skipped
  the permission filtering the list endpoints apply
* the disciplinary search/filter returned every employee's records
"""

import json

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from employee.models import Actiontype, DisciplinaryAction
from helpdesk.models import Ticket, TicketType
from horilla.test_access_control import HX, AccessTestCase, grant, make_employee
from offboarding.models import ResignationLetter
from pms.models import Feedback, QuestionTemplate


class ArchivedLoginTests(AccessTestCase):
    def login(self):
        return Client(REMOTE_ADDR="127.0.0.1").post(
            "/api/auth/login/",
            data=json.dumps({"username": "acc_owner", "password": "pw-12345"}),
            content_type="application/json",
            HTTP_X_REAL_IP="203.0.113.7",
        )

    def setUp(self):
        super().setUp()
        cache.clear()
        self.owner_user.password = make_password("pw-12345")
        self.owner_user.save()

    def test_active_employee_gets_tokens(self):
        self.assertEqual(self.login().status_code, 200)

    def test_archived_employee_gets_no_tokens_even_if_user_row_is_active(self):
        Employee = type(self.owner)
        Employee.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(self.login().status_code, 401)

    def test_sync_user_active_disables_and_restores_the_login_account(self):
        self.owner.is_active = False
        self.owner.save()
        self.owner.sync_user_active()
        self.owner_user.refresh_from_db()
        self.assertFalse(self.owner_user.is_active)
        self.assertEqual(self.login().status_code, 401)

        self.owner.is_active = True
        self.owner.save()
        self.owner.sync_user_active()
        self.owner_user.refresh_from_db()
        self.assertTrue(self.owner_user.is_active)

    def test_archive_view_really_disables_the_login(self):
        grant(self.hr_user, "employee", "delete_employee")
        self.client_for(self.hr_user).post(
            reverse("employee-archive", kwargs={"obj_id": self.owner.pk}), **HX
        )
        self.owner_user.refresh_from_db()
        self.assertFalse(self.owner_user.is_active)


class TicketActionTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        ticket_type = TicketType.objects.bulk_create(
            [TicketType(title="IT", type="others", prefix="IT")]
        )[0]

        def make(employee, creator):
            return Ticket.objects.bulk_create(
                [
                    Ticket(
                        title="T",
                        employee_id=employee,
                        ticket_type=ticket_type,
                        description="d",
                        assigning_type="individual",
                        raised_on=str(self.hr.pk),
                        created_by=creator,
                    )
                ]
            )[0]

        self.victim_ticket = make(self.owner, self.owner_user)
        # The stranger has raised a ticket of their own, which used to be
        # enough to pass the check on everybody else's tickets.
        self.own_ticket = make(self.stranger, self.stranger_user)

    def test_status_change_needs_a_link_to_that_ticket(self):
        url = reverse("ticket-status-change", kwargs={"ticket_id": self.victim_ticket.pk})
        self.client_for(self.stranger_user).post(url, {"status": "resolved"})
        self.victim_ticket.refresh_from_db()
        self.assertEqual(self.victim_ticket.status, "new")

        self.client_for(self.owner_user).post(url, {"status": "in_progress"})
        self.victim_ticket.refresh_from_db()
        self.assertEqual(self.victim_ticket.status, "in_progress")

    def test_delete_needs_a_link_to_that_ticket(self):
        url = reverse("ticket-delete", kwargs={"ticket_id": self.victim_ticket.pk})
        self.client_for(self.stranger_user).post(url, **HX)
        self.assertTrue(Ticket.objects.filter(pk=self.victim_ticket.pk).exists())

    def test_comment_needs_a_link_to_that_ticket(self):
        url = reverse("comment-create", kwargs={"ticket_id": self.victim_ticket.pk})
        self.client_for(self.stranger_user).post(url, {"comment": "hello"}, **HX)
        self.assertFalse(self.victim_ticket.comment.exists())

    def test_an_assignee_of_that_ticket_can_still_act(self):
        self.victim_ticket.assigned_to.add(self.stranger)
        url = reverse("ticket-status-change", kwargs={"ticket_id": self.victim_ticket.pk})
        self.client_for(self.stranger_user).post(url, {"status": "resolved"})
        self.victim_ticket.refresh_from_db()
        self.assertEqual(self.victim_ticket.status, "resolved")


class ApiRecordByIdTests(AccessTestCase):
    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=type(user).objects.get(pk=user.pk))
        return client

    def test_resignation_letter(self):
        letter = ResignationLetter.objects.create(
            employee_id=self.owner,
            title="Resignation",
            description="<p>bye</p>",
            planned_to_leave_on="2030-01-01",
            reason="Relocating",
            initiated_by=self.owner,
        )
        url = f"/api/offboarding/resignation-letter/{letter.pk}/"
        grant(self.hr_user, "offboarding", "view_resignationletter")
        self.assertEqual(self.api(self.stranger_user).get(url).status_code, 404)
        for user in (self.owner_user, self.mgr_user, self.hr_user):
            self.assertEqual(self.api(user).get(url).status_code, 200, user.username)

    def test_feedback(self):
        template = QuestionTemplate.objects.bulk_create(
            [QuestionTemplate(question_template="T1")]
        )[0]
        feedback = Feedback.objects.bulk_create(
            [
                Feedback(
                    question_template_id=template,
                    review_cycle="Q1",
                    employee_id=self.owner,
                    manager_id=self.mgr,
                    start_date="2030-01-01",
                    end_date="2030-02-01",
                )
            ]
        )[0]
        url = f"/api/pms/feedback/{feedback.pk}/"
        grant(self.hr_user, "pms", "view_feedback")
        self.assertEqual(self.api(self.stranger_user).get(url).status_code, 404)
        self.assertEqual(self.api(self.hr_user).get(url).status_code, 200)
        self.assertEqual(self.api(self.owner_user).get(url).status_code, 200)

    def test_attendance(self):
        from attendance.models import Attendance

        attendance = Attendance.objects.bulk_create(
            [
                Attendance(
                    employee_id=self.owner,
                    attendance_date="2030-01-01",
                    attendance_clock_in_date="2030-01-01",
                    attendance_clock_in="09:00",
                    shift_id=None,
                )
            ]
        )[0]
        url = f"/api/attendance/attendance/{attendance.pk}"
        grant(self.hr_user, "attendance", "view_attendance")
        self.assertEqual(self.api(self.stranger_user).get(url).status_code, 404)
        self.assertEqual(self.api(self.hr_user).get(url).status_code, 200)
        self.assertEqual(self.api(self.owner_user).get(url).status_code, 200)


class DisciplinaryVisibilityTests(AccessTestCase):
    def setUp(self):
        super().setUp()
        action = Actiontype.objects.bulk_create(
            [Actiontype(title="Warning", action_type="warning")]
        )[0]
        self.record = DisciplinaryAction.objects.bulk_create(
            [DisciplinaryAction(action=action, description="private", start_date="2030-01-01")]
        )[0]
        self.record.employee_id.add(self.owner)

    def ids(self, user, name):
        response = self.client_for(user).get(reverse(name), **HX)
        self.assertEqual(response.status_code, 200)
        return [row.pk for row in response.context["data"]]

    def test_filter_and_search_hide_other_peoples_records(self):
        for name in ("disciplinary-filter-view", "search-disciplinary"):
            with self.subTest(view=name):
                self.assertEqual(self.ids(self.stranger_user, name), [])

    def test_owner_manager_and_permission_holder_see_it(self):
        grant(self.hr_user, "employee", "view_disciplinaryaction")
        for name in ("disciplinary-filter-view", "search-disciplinary"):
            for user in (self.owner_user, self.mgr_user, self.hr_user):
                with self.subTest(view=name, user=user.username):
                    self.assertEqual(self.ids(user, name), [self.record.pk])


class ApiRecordByIdBatchTwoTests(AccessTestCase):
    """The remaining owner-bearing single-record GETs."""

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=type(user).objects.get(pk=user.pk))
        return client

    def check(self, url, perm_app, perm_codename, allowed_ok=(200,)):
        grant(self.hr_user, perm_app, perm_codename)
        self.assertEqual(self.api(self.stranger_user).get(url).status_code, 404)
        self.assertIn(self.api(self.hr_user).get(url).status_code, allowed_ok)
        self.assertIn(self.api(self.owner_user).get(url).status_code, allowed_ok)

    def test_shift_request(self):
        from base.models import EmployeeShift, ShiftRequest

        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="Day")])[0]
        req = ShiftRequest.objects.bulk_create(
            [
                ShiftRequest(
                    employee_id=self.owner,
                    shift_id=shift,
                    requested_date="2030-01-01",
                    requested_till="2030-01-02",
                )
            ]
        )[0]
        self.check(f"/api/base/shift-requests/{req.pk}/", "base", "view_shiftrequest")

    def test_helpdesk_comment_and_attachment(self):
        ticket_type = TicketType.objects.bulk_create(
            [TicketType(title="IT", type="others", prefix="IT")]
        )[0]
        ticket = Ticket.objects.bulk_create(
            [
                Ticket(
                    title="T",
                    employee_id=self.owner,
                    ticket_type=ticket_type,
                    description="d",
                    assigning_type="individual",
                    raised_on=str(self.hr.pk),
                )
            ]
        )[0]
        from helpdesk.models import Comment

        comment = Comment.objects.bulk_create(
            [Comment(ticket=ticket, employee_id=self.owner, comment="secret")]
        )[0]
        url = f"/api/helpdesk/comment/{comment.pk}/"
        grant(self.hr_user, "helpdesk", "view_ticket")
        self.assertEqual(self.api(self.stranger_user).get(url).status_code, 404)
        self.assertEqual(self.api(self.owner_user).get(url).status_code, 200)
        self.assertEqual(self.api(self.hr_user).get(url).status_code, 200)


class AssetWriteAccessTests(AccessTestCase):
    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=type(user).objects.get(pk=user.pk))
        return client

    def setUp(self):
        super().setUp()
        from asset.models import Asset, AssetAssignment, AssetCategory, AssetRequest

        category = AssetCategory.objects.bulk_create(
            [AssetCategory(asset_category_name="Laptops")]
        )[0]
        self.asset = Asset.objects.bulk_create(
            [Asset(asset_name="MBP", asset_tracking_id="A1", asset_status="In use",
                   asset_purchase_cost=1, asset_category_id=category,
                   asset_purchase_date="2030-01-01")]
        )[0]
        self.assignment = AssetAssignment.objects.bulk_create(
            [AssetAssignment(asset_id=self.asset, assigned_to_employee_id=self.owner,
                             assigned_by_employee_id=self.hr)]
        )[0]
        self.request_obj = AssetRequest.objects.bulk_create(
            [AssetRequest(requested_employee_id=self.owner, asset_category_id=category,
                          description="need one")]
        )[0]

    def test_ordinary_employee_cannot_delete_assets_or_assignments(self):
        from asset.models import Asset, AssetAssignment

        client = self.api(self.stranger_user)
        self.assertEqual(client.delete(f"/api/asset/assets/{self.asset.pk}").status_code, 403)
        self.assertEqual(
            client.delete(f"/api/asset/asset-allocations/{self.assignment.pk}").status_code, 403
        )
        self.assertTrue(Asset.objects.filter(pk=self.asset.pk).exists())
        self.assertTrue(AssetAssignment.objects.filter(pk=self.assignment.pk).exists())

    def test_permission_holder_can_delete_an_assignment(self):
        from asset.models import AssetAssignment

        grant(self.hr_user, "asset", "delete_assetassignment")
        r = self.api(self.hr_user).delete(f"/api/asset/asset-allocations/{self.assignment.pk}")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(AssetAssignment.objects.filter(pk=self.assignment.pk).exists())

    def test_only_the_requester_or_a_permission_holder_can_delete_a_request(self):
        from asset.models import AssetRequest

        url = f"/api/asset/asset-requests/{self.request_obj.pk}"
        self.assertEqual(self.api(self.stranger_user).delete(url).status_code, 403)
        self.assertTrue(AssetRequest.objects.filter(pk=self.request_obj.pk).exists())
        self.assertEqual(self.api(self.owner_user).delete(url).status_code, 204)

    def test_approve_and_reject_need_the_allocation_permission(self):
        for name in ("asset-approve", "asset-reject"):
            r = self.api(self.stranger_user).put(f"/api/asset/{name}/{self.request_obj.pk}")
            self.assertEqual(r.status_code, 403, name)

    def test_only_the_holder_can_flag_an_assignment_for_return(self):
        from asset.models import AssetAssignment

        url = f"/api/asset/asset-return/{self.assignment.pk}"
        self.assertEqual(self.api(self.stranger_user).put(url).status_code, 403)
        self.assignment.refresh_from_db()
        self.assertFalse(self.assignment.return_request)
        self.assertEqual(self.api(self.owner_user).put(url).status_code, 200)
        self.assignment.refresh_from_db()
        self.assertTrue(self.assignment.return_request)
