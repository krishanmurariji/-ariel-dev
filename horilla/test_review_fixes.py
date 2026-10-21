"""
Fixes from the October review:

* API endpoints that approved reimbursements and overtime, validated attendance, deleted shift
  requests and rotating-shift assignments, answered shift swaps and mailed employees for ANY
  signed-in user now need the owner/manager or the matching permission
* the Allowances and Deductions tab (the URL passed emp_id, the view wanted pk) opens again
* deleting an FAQ no longer crashes after deleting it
* the monthly leave forecast is no longer hidden by a second method of the same name
* automation relation discovery no longer fails on a missing import
"""

import ast
import datetime
import re
from pathlib import Path

from django.conf import settings
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from rest_framework.test import APIClient

from base.models import EmployeeShift, ShiftRequest, WorkType
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee

TODAY = datetime.date.today()


def api_urls():
    found = {}

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + route)
            elif isinstance(pattern, URLPattern):
                cls = getattr(pattern.callback, "cls", None) or getattr(pattern.callback, "view_class", None)
                if cls is not None:
                    found.setdefault(cls.__name__, "/" + prefix + route)

    walk(get_resolver().url_patterns)
    return found


def url(urls, name, **kwargs):
    path = urls[name]
    for key, value in kwargs.items():
        path = re.sub(r"<[^>]*%s>" % key, str(value), path)
    return re.sub(r"<[^>]*>", "1", path)


def api_client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class ApiActions(IsolatedTestCase):
    def setUp(self):
        from attendance.models import Attendance
        from payroll.models.models import Reimbursement

        self.manager_user, manager = make_employee("rf_manager")
        self.victim_user, self.victim = make_employee("rf_victim", manager=manager)
        self.stranger_user, _stranger = make_employee("rf_stranger")
        self.hr_user, _hr = make_employee("rf_hr")
        self.urls = api_urls()
        self.stranger = api_client(self.stranger_user)
        self.reimbursement = Reimbursement.objects.bulk_create(
            [Reimbursement(title="claim", employee_id=self.victim, allowance_on=TODAY, amount=10, status="requested")]
        )[0]
        shift = EmployeeShift.objects.bulk_create([EmployeeShift(employee_shift="RF shift")])[0]
        work_type = WorkType.objects.bulk_create([WorkType(work_type="RF work")])[0]
        self.shift_request = ShiftRequest.objects.bulk_create(
            [
                ShiftRequest(
                    employee_id=self.victim,
                    shift_id=shift,
                    requested_date=TODAY,
                    requested_till=TODAY,
                    description="d",
                    approved=True,
                )
            ]
        )[0]
        self.attendance = Attendance.objects.bulk_create(
            [
                Attendance(
                    employee_id=self.victim,
                    attendance_date=TODAY,
                    shift_id=shift,
                    work_type_id=work_type,
                    attendance_clock_in_date=TODAY,
                    attendance_clock_in=datetime.time(9, 0),
                    attendance_clock_out_date=TODAY,
                    attendance_clock_out=datetime.time(18, 0),
                )
            ]
        )[0]
        self.Attendance = Attendance
        self.Reimbursement = Reimbursement

    def reimbursement_url(self):
        return url(self.urls, "ReimbusementApproveRejectView", pk=self.reimbursement.pk)

    def test_a_stranger_cannot_approve_a_reimbursement_or_set_its_amount(self):
        response = self.stranger.post(self.reimbursement_url(), {"status": "approved", "amount": "99999"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.reimbursement.refresh_from_db()
        self.assertEqual((self.reimbursement.status, self.reimbursement.amount), ("requested", 10))

    def test_the_manager_can_approve_and_a_json_number_amount_works(self):
        response = api_client(self.manager_user).post(
            self.reimbursement_url(), {"status": "approved", "amount": 25}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.reimbursement.refresh_from_db()
        self.assertEqual((self.reimbursement.status, self.reimbursement.amount), ("approved", 25))

    def test_the_permission_holder_can_approve(self):
        grant(self.hr_user, "payroll", "change_reimbursement")
        response = api_client(self.hr_user).post(self.reimbursement_url(), {"status": "rejected"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_the_owner_cannot_approve_their_own_reimbursement(self):
        response = api_client(self.victim_user).post(self.reimbursement_url(), {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_a_stranger_cannot_approve_overtime_or_validate_attendance(self):
        overtime = self.stranger.put(url(self.urls, "OvertimeApproveView", pk=self.attendance.pk))
        validate = self.stranger.put(url(self.urls, "ValidateAttendanceView", pk=self.attendance.pk))
        self.assertEqual((overtime.status_code, validate.status_code), (403, 403))
        self.assertFalse(self.Attendance.objects.filter(pk=self.attendance.pk, attendance_overtime_approve=True).exists())
        self.assertFalse(self.Attendance.objects.filter(pk=self.attendance.pk, attendance_validated=True).exists())

    def test_the_manager_can_approve_overtime_and_validate_attendance(self):
        manager = api_client(self.manager_user)
        self.assertEqual(manager.put(url(self.urls, "OvertimeApproveView", pk=self.attendance.pk)).status_code, 200)
        self.assertEqual(manager.put(url(self.urls, "ValidateAttendanceView", pk=self.attendance.pk)).status_code, 200)
        self.assertTrue(self.Attendance.objects.filter(pk=self.attendance.pk, attendance_validated=True).exists())

    def test_a_stranger_cannot_delete_a_shift_request(self):
        response = self.stranger.delete(url(self.urls, "ShiftRequestDeleteView", pk=self.shift_request.pk))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ShiftRequest.objects.entire().filter(pk=self.shift_request.pk).exists())

    def test_a_stranger_cannot_mass_delete_shift_requests(self):
        self.stranger.delete(url(self.urls, "ShiftRequestDeleteView").replace("/1", "/"), {"ids": [self.shift_request.pk]}, format="json")
        self.assertTrue(ShiftRequest.objects.entire().filter(pk=self.shift_request.pk).exists())

    def test_the_owner_can_delete_their_shift_request(self):
        response = api_client(self.victim_user).delete(url(self.urls, "ShiftRequestDeleteView", pk=self.shift_request.pk))
        self.assertEqual(response.status_code, 200)

    def test_a_stranger_cannot_answer_a_shift_swap(self):
        response = self.stranger.post(url(self.urls, "ShiftRequestAllocationView", id=self.shift_request.pk))
        self.assertEqual(response.status_code, 403)

    def test_a_stranger_cannot_change_rotating_shift_assignments(self):
        delete = self.stranger.delete(url(self.urls, "RotatingShiftAssignBulkDelete"), {"ids": [1]}, format="json")
        archive = self.stranger.put(url(self.urls, "RotatingShiftAssignBulkArchive", status="true"), {"ids": [1]}, format="json")
        self.assertEqual((delete.status_code, archive.status_code), (403, 403))

    def test_a_stranger_cannot_mail_an_employee_from_the_company_address(self):
        from django.core import mail

        mail.outbox.clear()
        response = self.stranger.post(
            self.urls["OfflineEmployeeMailsend"], {"employee_id": self.victim.pk, "subject": "x", "body": "y"}, format="multipart"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)


class ProfileTab(IsolatedTestCase):
    def test_the_allowances_and_deductions_tab_opens(self):
        admin, employee = make_employee("rf_tab_admin")
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        client = Client(raise_request_exception=False)
        client.force_login(admin)
        response = client.get(reverse("allowances-deductions-tab", args=[employee.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)


class SmallBugs(IsolatedTestCase):
    def test_deleting_an_faq_works(self):
        from helpdesk.models import FAQ, FAQCategory

        admin, _employee = make_employee("rf_faq_admin")
        admin.is_superuser = True
        admin.is_staff = True
        admin.save()
        category = FAQCategory.objects.bulk_create([FAQCategory(title="Cat")])[0]
        faq = FAQ.objects.bulk_create([FAQ(question="Q?", answer="A", category=category)])[0]
        client = Client(raise_request_exception=False)
        client.force_login(admin)
        response = client.post(reverse("faq-delete", args=[faq.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FAQ.objects.filter(pk=faq.pk).exists())

    def test_the_monthly_forecast_and_the_dated_forecast_are_separate_methods(self):
        from leave.models import AvailableLeave

        self.assertTrue(callable(AvailableLeave.forcasted_leaves_by_month))
        self.assertTrue(callable(AvailableLeave.forcasted_leaves))
        self.assertEqual(AvailableLeave.forcasted_leaves_by_month.__code__.co_argcount, 1)
        self.assertEqual(AvailableLeave.forcasted_leaves.__code__.co_argcount, 2)

    def test_the_automation_relation_finder_has_every_name_it_uses(self):
        from django.db.models.fields.reverse_related import ForeignObjectRel

        from employee.models import Employee
        from horilla_automations.methods import recursive_relation
        from leave.models import LeaveRequest

        self.assertIs(recursive_relation.ForeignObjectRel, ForeignObjectRel)
        recursive_relation.get_all_relation_paths(Employee, LeaveRequest)

    def test_no_view_function_hides_the_messages_module_with_a_local_variable(self):
        # `messages = "..."` inside a function made every messages.success() in it fail
        offenders = []
        for relative in ("helpdesk/views.py", "recruitment/views/views.py", "leave/views.py", "payroll/views/views.py"):
            tree = ast.parse((Path(settings.BASE_DIR) / relative).read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if isinstance(function, ast.FunctionDef):
                    for node in ast.walk(function):
                        if isinstance(node, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "messages" for t in node.targets
                        ):
                            offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [])
