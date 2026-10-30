"""
Follow-up to test_manager_scope.py: the bulk actions, exports and single-record reads. A manager (or
any signed-in employee) may only touch or read the records of the people they manage.
"""

import io
import json

from django.test import Client
from django.urls import reverse
from openpyxl import load_workbook

from attendance.forms import AttendanceExportForm
from attendance.models import Attendance
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from leave.models import AvailableLeave, LeaveRequest, LeaveType

HX = {"HTTP_HX_REQUEST": "true"}


def client_for(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class Setup(IsolatedTestCase):
    def setUp(self):
        self.manager_user, self.manager = make_employee("bk_manager")
        _, self.report = make_employee("bk_report", manager=self.manager)
        _, self.outsider = make_employee("bk_outsider")
        self.stranger_user, self.stranger = make_employee("bk_stranger")
        self.manager_client = client_for(self.manager_user)

    def attendance_for(self, employee, day="2030-01-01"):
        return Attendance.objects.bulk_create(
            [
                Attendance(
                    employee_id=employee,
                    attendance_date=day,
                    attendance_clock_in_date=day,
                    attendance_clock_in="09:00",
                    shift_id=None,
                )
            ]
        )[0]

    def leave_for(self, employee):
        leave_type = LeaveType.objects.bulk_create([LeaveType(name=f"bk type {employee.pk}")])[0]
        AvailableLeave.objects.bulk_create(
            [
                AvailableLeave(
                    employee_id=employee,
                    leave_type_id=leave_type,
                    available_days=10,
                    total_leave_days=10,
                )
            ]
        )
        return LeaveRequest.objects.bulk_create(
            [
                LeaveRequest(
                    employee_id=employee,
                    leave_type_id=leave_type,
                    start_date="2030-02-01",
                    end_date="2030-02-01",
                    requested_days=1,
                    description="bk leave",
                )
            ]
        )[0]


def sheet_text(response):
    workbook = load_workbook(io.BytesIO(b"".join(response.streaming_content) if response.streaming else response.content))
    return "\n".join(
        " ".join(str(cell) for cell in row) for sheet in workbook for row in sheet.iter_rows(values_only=True)
    )


class AttendanceExport(Setup):
    def export_as(self, user, **extra):
        fields = [name for name, _label in AttendanceExportForm().fields["selected_fields"].choices]
        params = {"selected_fields": [f for f in fields if "date" in f][:1], **extra}
        return client_for(user).get(reverse("attendance-info-export"), params)

    def setUp(self):
        super().setUp()
        self.attendance_for(self.report, day="2031-04-04")
        self.attendance_for(self.outsider, day="2031-05-05")

    def test_a_plain_employee_does_not_get_other_peoples_attendance(self):
        text = sheet_text(self.export_as(self.stranger_user))
        self.assertNotIn("May. 05, 2031", text)
        self.assertNotIn("Apr. 04, 2031", text)

    def test_a_manager_gets_their_own_team_and_nobody_else(self):
        text = sheet_text(self.export_as(self.manager_user))
        self.assertIn("Apr. 04, 2031", text)
        self.assertNotIn("May. 05, 2031", text)

    def test_chosen_ids_do_not_widen_the_export(self):
        outsider_row = Attendance.objects.filter(employee_id=self.outsider).first()
        response = client_for(self.stranger_user).get(
            reverse("attendance-info-export"), {"ids": json.dumps([outsider_row.pk])}
        )
        self.assertNotIn("May. 05, 2031", sheet_text(response))


class BulkActions(Setup):
    def test_a_manager_cannot_bulk_delete_leave_requests_that_include_a_stranger(self):
        mine, theirs = self.leave_for(self.report), self.leave_for(self.outsider)
        self.manager_client.post(
            reverse("leave-request-bulk-delete"), {"ids": json.dumps([mine.pk, theirs.pk])}, **HX
        )
        self.assertTrue(LeaveRequest.objects.entire().filter(pk=theirs.pk).exists())
        self.assertTrue(LeaveRequest.objects.entire().filter(pk=mine.pk).exists())

    def test_a_manager_can_bulk_delete_the_leave_requests_of_their_team(self):
        mine = self.leave_for(self.report)
        self.manager_client.post(
            reverse("leave-request-bulk-delete"), {"ids": json.dumps([mine.pk])}, **HX
        )
        self.assertFalse(LeaveRequest.objects.entire().filter(pk=mine.pk).exists())

    def test_a_manager_cannot_bulk_approve_a_strangers_leave(self):
        theirs = self.leave_for(self.outsider)
        self.manager_client.post(reverse("leave-requests-bulk-approve"), {"ids": [theirs.pk]}, **HX)
        theirs.refresh_from_db()
        self.assertNotEqual(theirs.status, "approved")

    def test_a_manager_can_bulk_approve_their_teams_leave(self):
        mine = self.leave_for(self.report)
        self.manager_client.post(reverse("leave-requests-bulk-approve"), {"ids": [mine.pk]}, **HX)
        mine.refresh_from_db()
        self.assertEqual(mine.status, "approved")

    def test_a_manager_cannot_bulk_validate_a_strangers_attendance(self):
        theirs = self.attendance_for(self.outsider)
        self.manager_client.post(
            reverse("validate-bulk-attendance"), {"ids": json.dumps([theirs.pk])}, **HX
        )
        theirs.refresh_from_db()
        self.assertFalse(theirs.attendance_validated)


class SingleRecordRead(Setup):
    def test_a_manager_cannot_open_a_strangers_leave_request(self):
        theirs = self.leave_for(self.outsider)
        response = self.manager_client.get(reverse("one-request-view", args=[theirs.pk]), {"instances_ids": json.dumps([theirs.pk])}, **HX)
        self.assertNotContains(response, "bk leave", status_code=response.status_code)

    def test_a_manager_can_open_their_teams_leave_request(self):
        mine = self.leave_for(self.report)
        response = self.manager_client.get(reverse("one-request-view", args=[mine.pk]), {"instances_ids": json.dumps([mine.pk])}, **HX)
        self.assertContains(response, "bk leave")


class BulkAttendanceBehaviour(Setup):
    """What the three bulk attendance views do for a manager acting on their own team (must not change)."""

    def post_ids(self, name, ids):
        return self.manager_client.post(reverse(name), {"ids": json.dumps(ids)}, **HX)

    def test_validate_bulk_marks_the_teams_attendance_validated(self):
        a, b = self.attendance_for(self.report, "2031-01-01"), self.attendance_for(self.report, "2031-01-02")
        self.post_ids("validate-bulk-attendance", [a.pk, b.pk])
        for row in (a, b):
            row.refresh_from_db()
            self.assertTrue(row.attendance_validated)

    def test_validate_bulk_skips_a_missing_id_and_still_does_the_rest(self):
        a = self.attendance_for(self.report, "2031-01-03")
        response = self.post_ids("validate-bulk-attendance", [a.pk, 987654321])
        a.refresh_from_db()
        self.assertTrue(a.attendance_validated)
        self.assertEqual(response.status_code, 200)

    def test_approve_bulk_overtime_approves_the_teams_overtime(self):
        a = self.attendance_for(self.report, "2031-01-04")
        self.post_ids("approve-bulk-overtime", [a.pk])
        a.refresh_from_db()
        self.assertTrue(a.attendance_overtime_approve)

    def test_approve_bulk_overtime_survives_a_missing_id(self):
        a = self.attendance_for(self.report, "2031-01-05")
        response = self.post_ids("approve-bulk-overtime", [a.pk, 987654321])
        a.refresh_from_db()
        self.assertTrue(a.attendance_overtime_approve)
        self.assertEqual(response.status_code, 200)

    def test_bulk_approve_attendance_request_validates_the_teams_attendance(self):
        a = self.attendance_for(self.report, "2031-01-06")
        self.post_ids("bulk-approve-attendance-request", [a.pk])
        a.refresh_from_db()
        self.assertTrue(a.attendance_validated)
        self.assertTrue(a.is_validate_request_approved)

    def test_a_manager_never_acts_on_their_own_attendance_in_bulk(self):
        own = self.attendance_for(self.manager, "2031-01-07")
        self.post_ids("validate-bulk-attendance", [own.pk])
        own.refresh_from_db()
        self.assertFalse(own.attendance_validated)

    def test_the_lookup_is_one_query_for_the_whole_list_not_two_per_id(self):
        import re

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        rows = [self.attendance_for(self.report, f"2031-02-{day:02d}") for day in range(1, 7)]
        with CaptureQueriesContext(connection) as queries:
            self.post_ids("validate-bulk-attendance", [row.pk for row in rows])
        single_row_selects = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].startswith("SELECT")
            and re.search(r'FROM "attendance_attendance" .*WHERE "attendance_attendance"\."id" = \d+', q["sql"])
        ]
        # the model's own save() still looks up each row's previous values (partial columns); what
        # must be gone are the full-row fetches, two per id before
        full_row = [sql for sql in single_row_selects if '"attendance_attendance"."created_at"' in sql]
        self.assertEqual(full_row, [])
        for row in rows:
            row.refresh_from_db()
            self.assertTrue(row.attendance_validated)
