"""
Broader regression coverage for the access leaks fixed in test_access_leaks.py.

The first file pins each reported bug. This one is built to stop the *class*
of bug coming back:

* every single-record API GET is run through the same four-person matrix
  (stranger / owner / reporting manager / permission holder), plus anonymous
  and non-existent ids, so a new endpoint copied from the old pattern shows up
  as a failing row;
* every asset write endpoint is run through every verb as an unprivileged user;
* archiving is checked from every entry point and for what it must NOT touch;
* the disciplinary filter is attacked through its own query parameters.
"""

import datetime
import json

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from base.models import EmployeeShift, ShiftRequest, WorkType, WorkTypeRequest
from employee.models import Actiontype, DisciplinaryAction, Employee
from helpdesk.models import Attachment, Comment, Ticket, TicketType
from horilla.test_access_control import HX, AccessTestCase, grant, make_employee
from horilla_documents.models import DocumentRequest
from leave.models import LeaveAllocationRequest, LeaveRequest, LeaveType
from payroll.models.models import Contract, Reimbursement

TODAY = datetime.date.today()


def api(user=None):
    client = APIClient()
    if user is not None:
        # Re-read the user: Django caches permissions on the instance, so a
        # permission granted after the first request would otherwise be ignored.
        client.force_authenticate(user=type(user).objects.get(pk=user.pk))
    return client


class SingleRecordMatrixTests(AccessTestCase):
    """One row per endpoint: who may read /<thing>/<id>."""

    def setUp(self):
        super().setUp()
        self.cases = []  # (label, url, (app, codename) for the permission holder)

        leave_type = LeaveType.objects.bulk_create([LeaveType(name="Casual")])[0]
        leave = LeaveRequest.objects.bulk_create(
            [
                LeaveRequest(
                    employee_id=self.owner,
                    leave_type_id=leave_type,
                    start_date=TODAY,
                    end_date=TODAY,
                    description="x",
                )
            ]
        )[0]
        self.cases.append(
            ("leave request", f"/api/leave/request/{leave.pk}/", ("leave", "view_leaverequest"))
        )

        alloc = LeaveAllocationRequest.objects.bulk_create(
            [
                LeaveAllocationRequest(
                    employee_id=self.owner, leave_type_id=leave_type, description="x"
                )
            ]
        )[0]
        self.cases.append(
            (
                "leave allocation",
                f"/api/leave/allocation-request/{alloc.pk}/",
                ("leave", "view_leaveallocationrequest"),
            )
        )

        contract = Contract.objects.bulk_create(
            [
                Contract(
                    contract_name="C",
                    employee_id=self.owner,
                    contract_start_date=TODAY,
                )
            ]
        )[0]
        self.cases.append(
            ("contract", f"/api/payroll/contract/{contract.pk}", ("payroll", "view_contract"))
        )

        reimbursement = Reimbursement.objects.bulk_create(
            [
                Reimbursement(
                    title="R", employee_id=self.owner, allowance_on=TODAY
                )
            ]
        )[0]
        self.cases.append(
            (
                "reimbursement",
                f"/api/payroll/reimbusement/{reimbursement.pk}",
                ("payroll", "view_reimbursement"),
            )
        )

        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="Day")])[0]
        shift_request = ShiftRequest.objects.bulk_create(
            [
                ShiftRequest(
                    employee_id=self.owner,
                    shift_id=shift,
                    requested_date=TODAY,
                    requested_till=TODAY,
                )
            ]
        )[0]
        self.cases.append(
            (
                "shift request",
                f"/api/base/shift-requests/{shift_request.pk}/",
                ("base", "view_shiftrequest"),
            )
        )

        work_type = WorkType.objects.bulk_create([WorkType(work_type="Office")])[0]
        wtr = WorkTypeRequest.objects.bulk_create(
            [
                WorkTypeRequest(
                    employee_id=self.owner,
                    work_type_id=work_type,
                    requested_date=TODAY,
                    requested_till=TODAY,
                )
            ]
        )[0]
        self.cases.append(
            (
                "work type request",
                f"/api/base/worktype-requests/{wtr.pk}/",
                ("base", "view_worktyperequest"),
            )
        )

        doc_request = DocumentRequest.objects.bulk_create(
            [DocumentRequest(title="ID proof", format="any")]
        )[0]
        doc_request.employee_id.add(self.owner)
        self.cases.append(
            (
                "document request",
                f"/api/employee/document-request/{doc_request.pk}/",
                ("horilla_documents", "view_documentrequest"),
            )
        )

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
                    created_by=self.owner_user,
                )
            ]
        )[0]
        comment = Comment.objects.bulk_create(
            [Comment(ticket=ticket, employee_id=self.owner, comment="secret")]
        )[0]
        self.cases.append(
            ("ticket comment", f"/api/helpdesk/comment/{comment.pk}/", ("helpdesk", "view_ticket"))
        )
        attachment = Attachment.objects.bulk_create(
            [Attachment(file="x.txt", ticket=ticket, comment=comment)]
        )[0]
        self.cases.append(
            (
                "ticket attachment",
                f"/api/helpdesk/attachment/{attachment.pk}/",
                ("helpdesk", "view_ticket"),
            )
        )

    # Manager-only endpoints answer an ordinary employee with 403 before they
    # look at the record at all - still no data, so 403 is as good as 404.
    MANAGER_ONLY = {"leave request", "leave allocation"}
    OWN_OR_PERMISSION = {"contract", "reimbursement"}

    def test_a_stranger_never_gets_the_record(self):
        for label, url, _perm in self.cases:
            with self.subTest(endpoint=label):
                r = api(self.stranger_user).get(url)
                expected = (403, 404) if label in self.MANAGER_ONLY else (404,)
                self.assertIn(r.status_code, expected, r.content[:200])
                self.assertNotIn(b"description", r.content)

    def test_anonymous_callers_are_refused(self):
        for label, url, _perm in self.cases:
            with self.subTest(endpoint=label):
                self.assertIn(api().get(url).status_code, (401, 403))

    def test_owner_manager_and_permission_holder_can_read(self):
        for label, url, (app, codename) in self.cases:
            grant(self.hr_user, app, codename)
            for user in (self.owner_user, self.mgr_user, self.hr_user):
                if label in self.MANAGER_ONLY and user is self.owner_user:
                    continue  # employees use the web UI for their own leave
                if label in self.OWN_OR_PERMISSION and user is self.mgr_user:
                    continue  # their lists hide them from managers, so by-id does too
                with self.subTest(endpoint=label, user=user.username):
                    r = api(user).get(url)
                    self.assertEqual(r.status_code, 200, r.content[:300])

    def test_missing_and_forbidden_records_look_the_same(self):
        """A 404 for 'not yours' must be indistinguishable from 'no such id',
        otherwise the endpoint still tells a stranger which ids exist."""
        for label, url, _perm in self.cases:
            missing = url.replace(url.rstrip("/").split("/")[-1], "9999999")
            with self.subTest(endpoint=label):
                self.assertEqual(
                    api(self.stranger_user).get(url).status_code,
                    api(self.stranger_user).get(missing).status_code,
                )

    def test_a_peer_with_a_different_manager_is_still_a_stranger(self):
        other_mgr_user, other_mgr = make_employee("acc_other_manager")
        peer_user, _peer = make_employee("acc_peer", manager=other_mgr)
        for label, url, _perm in self.cases:
            with self.subTest(endpoint=label):
                expected = (403, 404) if label in self.MANAGER_ONLY else (404,)
                self.assertIn(api(peer_user).get(url).status_code, expected)


class ListEndpointsStillFilteredTests(AccessTestCase):
    """The single-record fix must not have loosened the lists."""

    def test_resignation_and_feedback_lists_hide_other_peoples_rows(self):
        from offboarding.models import ResignationLetter

        ResignationLetter.objects.create(
            employee_id=self.owner,
            title="Resignation",
            description="<p>bye</p>",
            planned_to_leave_on=TODAY + datetime.timedelta(days=30),
            reason="r",
            initiated_by=self.owner,
        )
        for user, expected in ((self.stranger_user, 0), (self.owner_user, 1)):
            r = api(user).get("/api/offboarding/resignation-letter/")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["count"], expected, user.username)


class ArchiveEntryPointTests(AccessTestCase):
    """Every way of archiving must really disable the login, and un-archiving
    must restore it. Previously none of them saved the user row."""

    def setUp(self):
        super().setUp()
        cache.clear()
        grant(self.hr_user, "employee", "delete_employee")
        self.owner_user.password = make_password("pw-12345")
        self.owner_user.save()

    def user_active(self):
        self.owner_user.refresh_from_db()
        return self.owner_user.is_active

    def login_status(self):
        return Client(REMOTE_ADDR="127.0.0.1").post(
            "/api/auth/login/",
            data=json.dumps({"username": "acc_owner", "password": "pw-12345"}),
            content_type="application/json",
            HTTP_X_REAL_IP="203.0.113.9",
        ).status_code

    def test_web_archive_then_unarchive(self):
        url = reverse("employee-archive", kwargs={"obj_id": self.owner.pk})
        client = self.client_for(self.hr_user)
        client.post(url, **HX)
        self.assertFalse(self.user_active())
        self.assertEqual(self.login_status(), 401)
        client.post(url, **HX)
        self.assertTrue(self.user_active())
        self.assertEqual(self.login_status(), 200)

    def test_web_bulk_archive_then_unarchive(self):
        client = self.client_for(self.hr_user)
        client.post(
            reverse("employee-bulk-archive"),
            {"ids": json.dumps([self.owner.pk])},
            **HX,
        )
        self.assertFalse(self.user_active())
        client.post(
            reverse("employee-bulk-archive") + "?is_active=True",
            {"ids": json.dumps([self.owner.pk])},
            **HX,
        )
        self.assertTrue(self.user_active())

    def test_api_archive_single_and_unarchive(self):
        client = api(self.hr_user)
        client.post(f"/api/employee/employee-archive/{self.owner.pk}/False/")
        self.assertFalse(self.user_active())
        self.assertEqual(self.login_status(), 401)
        client.post(f"/api/employee/employee-archive/{self.owner.pk}/True/")
        self.assertTrue(self.user_active())

    def test_api_bulk_archive_reports_only_real_failures(self):
        r = api(self.hr_user).post(
            reverse("api-employee-bulk-archive", kwargs={"is_active": "False"}),
            {"ids": [self.owner.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])
        self.assertFalse(self.user_active())

    def test_existing_access_token_stops_working_once_archived(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        token = str(RefreshToken.for_user(self.owner_user).access_token)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(client.get("/api/employee/employee-selector/").status_code, 200)
        api(self.hr_user).post(f"/api/employee/employee-archive/{self.owner.pk}/False/")
        self.assertIn(
            client.get("/api/employee/employee-selector/").status_code, (401, 403)
        )

    def test_other_employees_are_left_alone(self):
        api(self.hr_user).post(f"/api/employee/employee-archive/{self.owner.pk}/False/")
        self.stranger_user.refresh_from_db()
        self.mgr_user.refresh_from_db()
        self.assertTrue(self.stranger_user.is_active)
        self.assertTrue(self.mgr_user.is_active)

    def test_a_blocked_account_is_not_unblocked_by_an_unrelated_save(self):
        """sync_user_active must only run on archive/unarchive, not on every
        Employee.save(), or it would undo the separate 'block login' switch."""
        self.owner_user.is_active = False
        self.owner_user.save()
        self.owner.employee_first_name = "Renamed"
        self.owner.save()
        self.assertFalse(self.user_active())

    def test_sync_is_a_no_op_when_already_in_step(self):
        self.owner.sync_user_active()
        self.assertTrue(self.user_active())

    def test_employee_without_a_login_account_does_not_crash(self):
        Employee.objects.filter(pk=self.owner.pk).update(employee_user_id=None)
        emp = Employee.objects.get(pk=self.owner.pk)
        emp.sync_user_active()

    def test_without_the_permission_nothing_is_archived(self):
        r = api(self.stranger_user).post(
            f"/api/employee/employee-archive/{self.owner.pk}/False/"
        )
        self.assertIn(r.status_code, (302, 401, 403))
        self.assertTrue(self.user_active())
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_wrong_password_and_unknown_user_are_still_refused(self):
        for creds in (
            {"username": "acc_owner", "password": "nope"},
            {"username": "nobody", "password": "pw-12345"},
        ):
            r = Client(REMOTE_ADDR="127.0.0.1").post(
                "/api/auth/login/",
                data=json.dumps(creds),
                content_type="application/json",
                HTTP_X_REAL_IP="203.0.113.10",
            )
            self.assertEqual(r.status_code, 401)


class TicketActionMatrixTests(AccessTestCase):
    """status / comment / delete on a ticket, for every kind of caller."""

    def setUp(self):
        super().setUp()
        ticket_type = TicketType.objects.bulk_create(
            [TicketType(title="IT", type="others", prefix="IT")]
        )[0]
        self.ticket_type = ticket_type
        self.ticket = self.make_ticket(self.owner, self.owner_user)
        self.helper_user, self.helper = make_employee("acc_helper")
        self.helper_ticket = self.make_ticket(self.helper, self.helper_user)

    def make_ticket(self, employee, creator, status="new"):
        return Ticket.objects.bulk_create(
            [
                Ticket(
                    title="T",
                    employee_id=employee,
                    ticket_type=self.ticket_type,
                    description="d",
                    assigning_type="individual",
                    raised_on=str(self.hr.pk),
                    created_by=creator,
                    status=status,
                )
            ]
        )[0]

    def post_status(self, user, status="resolved"):
        url = reverse("ticket-status-change", kwargs={"ticket_id": self.ticket.pk})
        self.client_for(user).post(url, {"status": status})
        self.ticket.refresh_from_db()
        return self.ticket.status

    def test_unrelated_people_cannot_change_status(self):
        # stranger has no ticket, helper has raised one: neither may touch it
        for user in (self.stranger_user, self.helper_user):
            with self.subTest(user=user.username):
                self.assertEqual(self.post_status(user), "new")

    def test_someone_assigned_to_a_different_ticket_cannot_change_status(self):
        self.helper_ticket.assigned_to.add(self.stranger)
        self.assertEqual(self.post_status(self.stranger_user), "new")

    def test_the_people_who_should_be_able_to_change_status_can(self):
        grant(self.hr_user, "helpdesk", "change_ticket")
        self.ticket.assigned_to.add(self.stranger)
        for user in (self.owner_user, self.mgr_user, self.hr_user, self.stranger_user):
            Ticket.objects.filter(pk=self.ticket.pk).update(status="new")
            with self.subTest(user=user.username):
                self.assertEqual(self.post_status(user), "resolved")

    def test_delete_is_limited_to_related_people(self):
        url = reverse("ticket-delete", kwargs={"ticket_id": self.ticket.pk})
        for user in (self.stranger_user, self.helper_user):
            self.client_for(user).post(url, **HX)
            self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists(), user.username)
        self.client_for(self.owner_user).post(url, **HX)
        self.assertFalse(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_delete_with_permission_but_not_new_is_still_refused(self):
        grant(self.hr_user, "helpdesk", "delete_ticket")
        Ticket.objects.filter(pk=self.ticket.pk).update(status="in_progress")
        url = reverse("ticket-delete", kwargs={"ticket_id": self.ticket.pk})
        self.client_for(self.hr_user).post(url, **HX)
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_comment_is_limited_to_related_people(self):
        url = reverse("comment-create", kwargs={"ticket_id": self.ticket.pk})
        for user in (self.stranger_user, self.helper_user):
            self.client_for(user).post(url, {"comment": "intruder"}, **HX)
        self.assertEqual(self.ticket.comment.count(), 0)
        self.client_for(self.owner_user).post(url, {"comment": "mine"}, **HX)
        self.assertEqual(self.ticket.comment.count(), 1)

    def test_decorated_views_do_not_crash_on_a_missing_ticket(self):
        for name in ("ticket-status-change", "ticket-delete", "comment-create"):
            url = reverse(name, kwargs={"ticket_id": 9999999})
            r = Client(raise_request_exception=False)
            r.force_login(self.stranger_user)
            self.assertLess(r.post(url, {"status": "x"}, **HX).status_code, 500, name)


class DisciplinaryQueryAttackTests(AccessTestCase):
    """The filter must stay scoped whatever the query string says."""

    def setUp(self):
        super().setUp()
        action = Actiontype.objects.bulk_create(
            [Actiontype(title="Warning", action_type="warning")]
        )[0]
        self.action = action
        self.owner_record = self.record(self.owner, "owner-secret")
        self.stranger_record = self.record(self.stranger, "stranger-own")

    def record(self, employee, description):
        rec = DisciplinaryAction.objects.bulk_create(
            [DisciplinaryAction(action=self.action, description=description, start_date=TODAY)]
        )[0]
        rec.employee_id.add(employee)
        return rec

    def ids(self, user, name, query=""):
        r = self.client_for(user).get(reverse(name) + query, **HX)
        self.assertEqual(r.status_code, 200)
        return sorted(row.pk for row in r.context["data"])

    VIEWS = ("disciplinary-filter-view", "search-disciplinary")

    def test_you_only_ever_see_your_own(self):
        for name in self.VIEWS:
            with self.subTest(view=name):
                self.assertEqual(self.ids(self.stranger_user, name), [self.stranger_record.pk])

    def test_asking_for_someone_elses_employee_id_returns_nothing_of_theirs(self):
        for name in self.VIEWS:
            for query in (
                f"?employee_id={self.owner.pk}",
                f"?search={self.owner.employee_first_name}",
                f"?employee_id={self.owner.pk}&employee_id={self.stranger.pk}",
                f"?action={self.action.pk}",
            ):
                with self.subTest(view=name, query=query):
                    self.assertNotIn(
                        self.owner_record.pk, self.ids(self.stranger_user, name, query)
                    )

    def test_pagination_and_odd_parameters_do_not_widen_the_result(self):
        for name in self.VIEWS:
            for query in ("?page=1", "?page=999", "?dashboard=1", "?click_id=1"):
                with self.subTest(view=name, query=query):
                    self.assertNotIn(
                        self.owner_record.pk, self.ids(self.stranger_user, name, query)
                    )

    def test_manager_sees_reports_but_not_unrelated_people(self):
        for name in self.VIEWS:
            with self.subTest(view=name):
                self.assertEqual(self.ids(self.mgr_user, name), [self.owner_record.pk])

    def test_permission_holder_sees_everything(self):
        grant(self.hr_user, "employee", "view_disciplinaryaction")
        for name in self.VIEWS:
            with self.subTest(view=name):
                self.assertEqual(
                    self.ids(self.hr_user, name),
                    sorted([self.owner_record.pk, self.stranger_record.pk]),
                )

    def test_a_record_shared_by_two_people_appears_once(self):
        shared = self.record(self.owner, "shared")
        shared.employee_id.add(self.mgr)
        grant(self.hr_user, "employee", "view_disciplinaryaction")
        ids = self.ids(self.hr_user, "search-disciplinary")
        self.assertEqual(ids.count(shared.pk), 1)

    def test_anonymous_gets_redirected_to_login(self):
        for name in self.VIEWS:
            r = Client().get(reverse(name), **HX)
            # htmx callers get an empty 204 (plus a client-side redirect header),
            # everyone else a 302 to the login page - never the records.
            self.assertIn(r.status_code, (204, 302))
            self.assertNotIn(b"disciplinary", r.content.lower())

    def test_non_htmx_requests_are_refused(self):
        for name in self.VIEWS:
            r = self.client_for(self.hr_user).get(reverse(name))
            self.assertNotEqual(r.status_code, 200)


class AssetWriteMatrixTests(AccessTestCase):
    """Every write verb on every asset endpoint, as an ordinary employee."""

    def setUp(self):
        super().setUp()
        from asset.models import (
            Asset,
            AssetAssignment,
            AssetCategory,
            AssetLot,
            AssetRequest,
        )

        self.category = AssetCategory.objects.bulk_create(
            [AssetCategory(asset_category_name="Laptops")]
        )[0]
        self.lot = AssetLot.objects.bulk_create([AssetLot(lot_number="L1")])[0]
        self.asset = Asset.objects.bulk_create(
            [
                Asset(
                    asset_name="MBP",
                    asset_tracking_id="A1",
                    asset_status="In use",
                    asset_purchase_cost=1,
                    asset_category_id=self.category,
                    asset_purchase_date=TODAY,
                )
            ]
        )[0]
        self.assignment = AssetAssignment.objects.bulk_create(
            [
                AssetAssignment(
                    asset_id=self.asset,
                    assigned_to_employee_id=self.owner,
                    assigned_by_employee_id=self.hr,
                )
            ]
        )[0]
        self.asset_request = AssetRequest.objects.bulk_create(
            [
                AssetRequest(
                    requested_employee_id=self.owner,
                    asset_category_id=self.category,
                    description="need",
                )
            ]
        )[0]

    def endpoints(self):
        return [
            ("asset", f"/api/asset/assets/{self.asset.pk}", "asset", "asset"),
            ("category", f"/api/asset/asset-categories/{self.category.pk}", "asset", "assetcategory"),
            ("lot", f"/api/asset/asset-lots/{self.lot.pk}", "asset", "assetlot"),
            ("allocation", f"/api/asset/asset-allocations/{self.assignment.pk}", "asset", "assetassignment"),
        ]

    def test_ordinary_employee_cannot_create_change_or_delete(self):
        client = api(self.stranger_user)
        for label, url, _app, _model in self.endpoints():
            list_url = url.rsplit("/", 1)[0] + "/"
            with self.subTest(endpoint=label):
                self.assertEqual(client.post(list_url, {}, format="json").status_code, 403, "post")
                self.assertEqual(client.put(url, {}, format="json").status_code, 403, "put")
                self.assertEqual(client.delete(url).status_code, 403, "delete")

    def test_even_the_record_holder_cannot_delete_their_own_assignment(self):
        r = api(self.owner_user).delete(f"/api/asset/asset-allocations/{self.assignment.pk}")
        self.assertEqual(r.status_code, 403)

    def test_each_permission_unlocks_only_its_own_endpoint(self):
        """delete_assetlot must not let you delete an asset."""
        grant(self.hr_user, "asset", "delete_assetlot")
        client = api(self.hr_user)
        self.assertEqual(client.delete(f"/api/asset/assets/{self.asset.pk}").status_code, 403)
        self.assertEqual(
            client.delete(f"/api/asset/asset-allocations/{self.assignment.pk}").status_code, 403
        )
        self.assertEqual(client.delete(f"/api/asset/asset-lots/{self.lot.pk}").status_code, 204)

    def test_view_permission_alone_does_not_allow_writes(self):
        for model in ("asset", "assetcategory", "assetlot", "assetassignment"):
            grant(self.hr_user, "asset", f"view_{model}")
        client = api(self.hr_user)
        for label, url, _app, _model in self.endpoints():
            with self.subTest(endpoint=label):
                self.assertEqual(client.delete(url).status_code, 403)

    def test_delete_permission_holder_actually_deletes(self):
        from asset.models import Asset, AssetAssignment

        for model in ("asset", "assetassignment"):
            grant(self.hr_user, "asset", f"delete_{model}")
        client = api(self.hr_user)
        self.assertEqual(
            client.delete(f"/api/asset/asset-allocations/{self.assignment.pk}").status_code, 204
        )
        self.assertEqual(client.delete(f"/api/asset/assets/{self.asset.pk}").status_code, 204)
        self.assertFalse(Asset.objects.filter(pk=self.asset.pk).exists())
        self.assertFalse(AssetAssignment.objects.filter(pk=self.assignment.pk).exists())

    def test_requests_only_the_requester_while_pending_or_permission_holder(self):
        from asset.models import AssetRequest

        url = f"/api/asset/asset-requests/{self.asset_request.pk}"
        for call in ("put", "delete"):
            r = getattr(api(self.stranger_user), call)(url, {}, format="json")
            self.assertEqual(r.status_code, 403, call)
        # no longer pending -> even the requester is stopped
        AssetRequest.objects.filter(pk=self.asset_request.pk).update(
            asset_request_status="Approved"
        )
        self.assertEqual(api(self.owner_user).delete(url).status_code, 403)
        grant(self.hr_user, "asset", "delete_assetrequest")
        self.assertEqual(api(self.hr_user).delete(url).status_code, 204)

    def test_approve_reject_and_return_need_the_right_standing(self):
        for name in ("asset-approve", "asset-reject"):
            r = api(self.stranger_user).put(f"/api/asset/{name}/{self.asset_request.pk}")
            self.assertEqual(r.status_code, 403, name)
        from asset.models import AssetAssignment

        r = api(self.stranger_user).put(f"/api/asset/asset-return/{self.assignment.pk}")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(AssetAssignment.objects.get(pk=self.assignment.pk).return_request)
        # the assignee may flag their own asset for return
        self.assertEqual(
            api(self.owner_user).put(f"/api/asset/asset-return/{self.assignment.pk}").status_code,
            200,
        )

    def test_anonymous_cannot_write_anything(self):
        for label, url, _app, _model in self.endpoints():
            with self.subTest(endpoint=label):
                self.assertIn(api().delete(url).status_code, (401, 403))


class RegressionBugTests(AccessTestCase):
    """Defects found while testing the access fixes; each was reproduced first."""

    def test_bulk_archive_api_is_reachable_at_its_documented_url(self):
        """The route used to be declared as 'employee-bulk- archive/...' (a space)."""
        grant(self.hr_user, "employee", "delete_employee")
        r = api(self.hr_user).post(
            "/api/employee/employee-bulk-archive/False/",
            {"ids": [self.owner.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)

    def test_individual_shift_request_without_employee_id_does_not_crash(self):
        """individual_permssion_check() used to dereference a missing employee -> 500."""
        from base.models import EmployeeShift, ShiftRequest

        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="Day")])[0]
        req = ShiftRequest.objects.bulk_create(
            [ShiftRequest(employee_id=self.owner, shift_id=shift,
                          requested_date=TODAY, requested_till=TODAY)]
        )[0]
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.owner_user)
        r = client.get(f"/api/base/individual-shift-request/{req.pk}")
        self.assertEqual(r.status_code, 200)

    def test_deleting_an_asset_category_that_is_in_use_is_refused_cleanly(self):
        """ProtectedError used to go uncaught, so the API answered 500."""
        from asset.models import AssetCategory, AssetRequest

        category = AssetCategory.objects.bulk_create(
            [AssetCategory(asset_category_name="Phones")]
        )[0]
        AssetRequest.objects.bulk_create(
            [AssetRequest(requested_employee_id=self.owner, asset_category_id=category,
                          description="x")]
        )
        grant(self.hr_user, "asset", "delete_assetcategory")
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=type(self.hr_user).objects.get(pk=self.hr_user.pk))
        r = client.delete(f"/api/asset/asset-categories/{category.pk}")
        self.assertIn(r.status_code, (400, 409))

    def test_a_stranger_cannot_approve_a_shift_request_of_an_employee_without_work_info(self):
        """
        is_reportingmanger() returns a (truthy) HttpResponse when the employee has
        no work information, which makes the permission check pass for anyone.
        """
        from base.models import EmployeeShift, ShiftRequest
        from employee.models import EmployeeWorkInformation

        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="Day")])[0]
        req = ShiftRequest.objects.bulk_create(
            [ShiftRequest(employee_id=self.owner, shift_id=shift,
                          requested_date=TODAY, requested_till=TODAY)]
        )[0]
        EmployeeWorkInformation.objects.filter(employee_id=self.owner).delete()
        r = api(self.stranger_user).put(f"/api/base/shift-request-approve/{req.pk}")
        ShiftRequest.objects.get(pk=req.pk)
        self.assertNotEqual(r.status_code, 200)
        self.assertFalse(ShiftRequest.objects.get(pk=req.pk).approved)


class ApprovalPermissionTests(AccessTestCase):
    """The approve/change permissions are checked with their app label, so
    people holding them (e.g. HR) can approve - and ordinary employees cannot."""

    def setUp(self):
        super().setUp()
        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="Day")])[0]
        self.shift_request = ShiftRequest.objects.bulk_create(
            [ShiftRequest(employee_id=self.owner, shift_id=shift,
                          requested_date=TODAY, requested_till=TODAY)]
        )[0]
        work_type = WorkType.objects.bulk_create([WorkType(work_type="Office")])[0]
        self.wt_request = WorkTypeRequest.objects.bulk_create(
            [WorkTypeRequest(employee_id=self.owner, work_type_id=work_type,
                             requested_date=TODAY, requested_till=TODAY)]
        )[0]

    def approve_shift(self, user):
        r = api(user).put(f"/api/base/shift-request-approve/{self.shift_request.pk}")
        return r.status_code, ShiftRequest.objects.get(pk=self.shift_request.pk).approved

    def approve_work_type(self, user):
        r = api(user).put(f"/api/base/worktype-requests-approve/{self.wt_request.pk}/")
        return r.status_code, WorkTypeRequest.objects.get(pk=self.wt_request.pk).approved

    def test_ordinary_employees_cannot_approve(self):
        for user in (self.stranger_user, self.owner_user):
            self.assertEqual(self.approve_shift(user), (400, False), user.username)
            self.assertEqual(self.approve_work_type(user), (400, False), user.username)

    def test_the_reporting_manager_can_approve(self):
        self.assertEqual(self.approve_shift(self.mgr_user), (200, True))
        self.assertEqual(self.approve_work_type(self.mgr_user), (200, True))

    def test_the_approve_permission_is_honoured(self):
        grant(self.hr_user, "base", "approve_shiftrequest", "approve_worktyperequest")
        self.assertEqual(self.approve_shift(self.hr_user), (200, True))
        self.assertEqual(self.approve_work_type(self.hr_user), (200, True))

    def test_the_change_permission_approves_only_pending_requests(self):
        grant(self.hr_user, "base", "change_shiftrequest")
        self.assertEqual(self.approve_shift(self.hr_user), (200, True))

    def test_a_permission_for_the_other_kind_of_request_does_not_help(self):
        grant(self.stranger_user, "base", "approve_shiftrequest")
        self.assertEqual(self.approve_work_type(self.stranger_user), (400, False))
        grant(self.hr_user, "base", "approve_worktyperequest")
        self.assertEqual(self.approve_shift(self.hr_user), (400, False))


class ListScopingTests(AccessTestCase):
    """The asset assignment / asset request / document request lists."""

    def setUp(self):
        super().setUp()
        from asset.models import Asset, AssetAssignment, AssetCategory, AssetRequest

        category = AssetCategory.objects.bulk_create(
            [AssetCategory(asset_category_name="Laptops")]
        )[0]
        assets = Asset.objects.bulk_create(
            [
                Asset(asset_name=f"A{i}", asset_tracking_id=f"T{i}", asset_status="In use",
                      asset_purchase_cost=1, asset_category_id=category,
                      asset_purchase_date=TODAY)
                for i in range(2)
            ]
        )
        AssetAssignment.objects.bulk_create(
            [
                AssetAssignment(asset_id=assets[0], assigned_to_employee_id=self.owner,
                                assigned_by_employee_id=self.hr),
                AssetAssignment(asset_id=assets[1], assigned_to_employee_id=self.stranger,
                                assigned_by_employee_id=self.hr),
            ]
        )
        AssetRequest.objects.bulk_create(
            [
                AssetRequest(requested_employee_id=self.owner, asset_category_id=category,
                             description="owner wants"),
                AssetRequest(requested_employee_id=self.stranger, asset_category_id=category,
                             description="stranger wants"),
            ]
        )
        doc_a, doc_b = DocumentRequest.objects.bulk_create(
            [DocumentRequest(title="owner doc", format="any"),
             DocumentRequest(title="stranger doc", format="any")]
        )
        doc_a.employee_id.add(self.owner)
        doc_b.employee_id.add(self.stranger)
        doc_b.employee_id.add(self.mgr)  # shared with the manager too

    URLS = {
        "asset assignments": ("/api/asset/asset-allocations/", "asset", "view_assetassignment"),
        "asset requests": ("/api/asset/asset-requests/", "asset", "view_assetrequest"),
        "document requests": (
            "/api/employee/document-request/", "horilla_documents", "view_documentrequest",
        ),
    }

    def count(self, user, url):
        r = api(user).get(url)
        self.assertEqual(r.status_code, 200, r.content[:200])
        return r.json()["count"]

    def test_each_person_sees_only_their_own_rows(self):
        for label, (url, _app, _codename) in self.URLS.items():
            with self.subTest(list=label):
                self.assertEqual(self.count(self.owner_user, url), 1)
                self.assertEqual(self.count(self.stranger_user, url), 1)

    def test_a_manager_sees_their_reports_rows(self):
        for label, (url, _app, _codename) in self.URLS.items():
            with self.subTest(list=label):
                # the owner's row, plus the document shared with the manager directly
                expected = 2 if label == "document requests" else 1
                self.assertEqual(self.count(self.mgr_user, url), expected)

    def test_someone_with_nothing_to_do_with_anyone_sees_nothing(self):
        outsider, _ = make_employee("acc_outsider")
        for label, (url, _app, _codename) in self.URLS.items():
            with self.subTest(list=label):
                self.assertEqual(self.count(outsider, url), 0)

    def test_the_view_permission_shows_everything_once(self):
        for label, (url, app, codename) in self.URLS.items():
            grant(self.hr_user, app, codename)
            with self.subTest(list=label):
                self.assertEqual(self.count(self.hr_user, url), 2)


class AssetReturnTargetsTheRightAssetTests(AccessTestCase):
    def test_returning_an_assignment_updates_that_assets_status(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from asset.models import Asset, AssetAssignment, AssetCategory

        category = AssetCategory.objects.bulk_create(
            [AssetCategory(asset_category_name="Laptops")]
        )[0]
        assets = Asset.objects.bulk_create(
            [
                Asset(asset_name=f"A{i}", asset_tracking_id=f"T{i}", asset_status="In use",
                      asset_purchase_cost=1, asset_category_id=category,
                      asset_purchase_date=TODAY)
                for i in range(3)
            ]
        )
        # the assignment's id (1) deliberately differs from its asset's id (3)
        assignment = AssetAssignment.objects.bulk_create(
            [AssetAssignment(asset_id=assets[2], assigned_to_employee_id=self.owner,
                             assigned_by_employee_id=self.hr)]
        )[0]
        self.assertNotEqual(assignment.pk, assets[2].pk)
        grant(self.hr_user, "asset", "change_assetassignment")

        r = api(self.hr_user).put(
            f"/api/asset/asset-return/{assignment.pk}",
            {
                "return_status": "Healthy",
                "return_condition": "fine",
                "image": SimpleUploadedFile("r.png", b"\x89PNG\r\n", content_type="image/png"),
            },
            format="multipart",
        )
        self.assertEqual(r.status_code, 200, r.content[:300])
        statuses = {a.pk: Asset.objects.get(pk=a.pk).asset_status for a in assets}
        self.assertEqual(statuses[assets[2].pk], "Available")
        self.assertEqual(statuses[assets[0].pk], "In use", "the wrong asset was touched")
        self.assertEqual(statuses[assets[1].pk], "In use", "the wrong asset was touched")


class ListAndDetailAgreeTests(AccessTestCase):
    """
    The rule that stops this bug class: for every endpoint that has both a list
    and a single-record read, a record is readable by id exactly when it
    appears in the list - for the owner, a direct manager, a manager two levels
    up, a stranger and a permission holder, with nested visibility on AND off.
    """

    def setUp(self):
        super().setUp()
        from asset.models import Asset, AssetAssignment, AssetCategory, AssetRequest

        # top -> mgr -> owner ; stranger and hr are unrelated
        self.top_user, self.top = make_employee("acc_top")
        from employee.models import EmployeeWorkInformation

        EmployeeWorkInformation.objects.filter(employee_id=self.mgr).update(
            reporting_manager_id=self.top
        )

        category = AssetCategory.objects.bulk_create(
            [AssetCategory(asset_category_name="Laptops")]
        )[0]
        asset = Asset.objects.bulk_create(
            [Asset(asset_name="A", asset_tracking_id="T", asset_status="In use",
                   asset_purchase_cost=1, asset_category_id=category,
                   asset_purchase_date=TODAY)]
        )[0]
        assignment = AssetAssignment.objects.bulk_create(
            [AssetAssignment(asset_id=asset, assigned_to_employee_id=self.owner,
                             assigned_by_employee_id=self.hr)]
        )[0]
        asset_request = AssetRequest.objects.bulk_create(
            [AssetRequest(requested_employee_id=self.owner, asset_category_id=category,
                          description="x")]
        )[0]
        doc_request = DocumentRequest.objects.bulk_create(
            [DocumentRequest(title="doc", format="any")]
        )[0]
        doc_request.employee_id.add(self.owner)
        leave_type = LeaveType.objects.bulk_create([LeaveType(name="Casual")])[0]
        allocation = LeaveAllocationRequest.objects.bulk_create(
            [LeaveAllocationRequest(employee_id=self.owner, leave_type_id=leave_type,
                                    description="x")]
        )[0]
        leave = LeaveRequest.objects.bulk_create(
            [LeaveRequest(employee_id=self.owner, leave_type_id=leave_type,
                          start_date=TODAY, end_date=TODAY, description="x")]
        )[0]
        contract = Contract.objects.bulk_create(
            [Contract(contract_name="C", employee_id=self.owner, contract_start_date=TODAY)]
        )[0]
        reimbursement = Reimbursement.objects.bulk_create(
            [Reimbursement(title="R", employee_id=self.owner, allowance_on=TODAY)]
        )[0]

        # label: (list url, detail url, pk, permission that shows everything)
        self.cases = {
            "asset assignment": ("/api/asset/asset-allocations/",
                                 f"/api/asset/asset-allocations/{assignment.pk}",
                                 assignment.pk, ("asset", "view_assetassignment")),
            "asset request": ("/api/asset/asset-requests/",
                              f"/api/asset/asset-requests/{asset_request.pk}",
                              asset_request.pk, ("asset", "view_assetrequest")),
            "document request": ("/api/employee/document-request/",
                                 f"/api/employee/document-request/{doc_request.pk}/",
                                 doc_request.pk,
                                 ("horilla_documents", "view_documentrequest")),
            "leave allocation": ("/api/leave/allocation-request/",
                                 f"/api/leave/allocation-request/{allocation.pk}/",
                                 allocation.pk, ("leave", "view_leaveallocationrequest")),
            "leave request": ("/api/leave/request/", f"/api/leave/request/{leave.pk}/",
                              leave.pk, ("leave", "view_leaverequest")),
            "contract": ("/api/payroll/contract/", f"/api/payroll/contract/{contract.pk}",
                         contract.pk, ("payroll", "view_contract")),
            "reimbursement": ("/api/payroll/reimbusement/",
                              f"/api/payroll/reimbusement/{reimbursement.pk}",
                              reimbursement.pk, ("payroll", "view_reimbursement")),
        }

    def in_list(self, user, list_url, pk):
        r = api(user).get(list_url)
        if r.status_code != 200:
            return False
        return pk in [row["id"] for row in r.json().get("results", [])]

    def readable(self, user, detail_url):
        return api(user).get(detail_url).status_code == 200

    def check_agreement(self):
        grant(self.hr_user, "asset", "view_assetassignment", "view_assetrequest")
        grant(self.hr_user, "horilla_documents", "view_documentrequest")
        grant(self.hr_user, "leave", "view_leaveallocationrequest", "view_leaverequest")
        grant(self.hr_user, "payroll", "view_contract", "view_reimbursement")
        users = {
            "owner": self.owner_user,
            "direct manager": self.mgr_user,
            "manager two levels up": self.top_user,
            "stranger": self.stranger_user,
            "permission holder": self.hr_user,
        }
        for label, (list_url, detail_url, pk, _perm) in self.cases.items():
            for who, user in users.items():
                with self.subTest(endpoint=label, user=who):
                    self.assertEqual(
                        self.in_list(user, list_url, pk),
                        self.readable(user, detail_url),
                        "listed and readable-by-id must agree",
                    )

    def test_nested_visibility_on(self):
        self.check_agreement()

    def test_nested_visibility_off(self):
        import unittest.mock as mock

        from base.methods import filtersubordinates, get_subordinate_employee_ids
        from django.test import override_settings

        with override_settings(NESTED_SUBORDINATE_VISIBILITY=False), mock.patch.object(
            filtersubordinates, "__defaults__", (None, "employee_id", False)
        ), mock.patch.object(get_subordinate_employee_ids, "__defaults__", (False,)):
            self.check_agreement()

    def test_a_manager_two_levels_up_can_read_when_nested_is_on(self):
        grant(self.hr_user, "asset", "view_assetassignment")
        url = self.cases["asset assignment"][1]
        self.assertEqual(api(self.top_user).get(url).status_code, 200)

    def test_a_manager_two_levels_up_cannot_read_when_nested_is_off(self):
        import unittest.mock as mock

        from base.methods import filtersubordinates, get_subordinate_employee_ids
        from django.test import override_settings

        url = self.cases["asset assignment"][1]
        with override_settings(NESTED_SUBORDINATE_VISIBILITY=False), mock.patch.object(
            filtersubordinates, "__defaults__", (None, "employee_id", False)
        ), mock.patch.object(get_subordinate_employee_ids, "__defaults__", (False,)):
            self.assertEqual(api(self.top_user).get(url).status_code, 404)
            self.assertEqual(api(self.mgr_user).get(url).status_code, 200)

    def test_managers_do_not_get_contracts_the_list_hides_from_them(self):
        url = self.cases["contract"][1]
        self.assertEqual(api(self.mgr_user).get(url).status_code, 404)
        self.assertEqual(api(self.top_user).get(url).status_code, 404)


class ManagerChainTests(AccessTestCase):
    """is_manager_in_chain: the shared rule the by-id reads now use."""

    def setUp(self):
        super().setUp()
        from employee.models import EmployeeWorkInformation

        self.top_user, self.top = make_employee("acc_chain_top")
        EmployeeWorkInformation.objects.filter(employee_id=self.mgr).update(
            reporting_manager_id=self.top
        )

    def test_direct_and_indirect_managers(self):
        from horilla.access import is_manager_in_chain

        self.assertTrue(is_manager_in_chain(self.mgr_user, self.owner, nested=True))
        self.assertTrue(is_manager_in_chain(self.top_user, self.owner, nested=True))
        self.assertFalse(is_manager_in_chain(self.top_user, self.owner, nested=False))
        self.assertTrue(is_manager_in_chain(self.mgr_user, self.owner, nested=False))

    def test_not_your_own_manager_nor_your_report_nor_a_stranger(self):
        from horilla.access import is_manager_in_chain

        self.assertFalse(is_manager_in_chain(self.owner_user, self.owner, nested=True))
        self.assertFalse(is_manager_in_chain(self.owner_user, self.mgr, nested=True))
        self.assertFalse(is_manager_in_chain(self.stranger_user, self.owner, nested=True))
        self.assertFalse(is_manager_in_chain(self.mgr_user, None, nested=True))

    def test_a_reporting_loop_terminates(self):
        from employee.models import EmployeeWorkInformation
        from horilla.access import is_manager_in_chain
        from base.methods import check_manager

        # top reports to owner, who reports to mgr, who reports to top: a loop
        EmployeeWorkInformation.objects.filter(employee_id=self.top).update(
            reporting_manager_id=self.owner
        )
        owner = type(self.owner).objects.get(pk=self.owner.pk)
        self.assertFalse(is_manager_in_chain(self.stranger_user, owner, nested=True))
        self.assertFalse(
            check_manager(self.stranger, type(self.owner).objects.get(pk=self.owner.pk), nested=True)
        )

    def test_missing_work_information_or_account_is_not_a_manager(self):
        from employee.models import EmployeeWorkInformation
        from horilla.access import is_manager_in_chain

        EmployeeWorkInformation.objects.filter(employee_id=self.owner).delete()
        owner = type(self.owner).objects.get(pk=self.owner.pk)
        self.assertFalse(is_manager_in_chain(self.mgr_user, owner, nested=True))
