"""
vertex/tests/test_resources.py

Hiring requests and Resource Visibility (ArielVertex ResourceRequestsController and
ResourcesController), on Groups and Permissions.
"""

from datetime import date, timedelta

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from vertex import access
from vertex.models import (
    ProjectMember,
    ProjectRole,
    ResourceRequest,
    ResourceRequestComment,
)
from vertex.tests.test_projects import (
    HX,
    VertexTestBase,
    grant,
    join,
    make_employee,
    make_project,
)


def notified(employee):
    return Notification.objects.filter(recipient=employee.employee_user_id).count()


class AvailabilityBucketTests(SimpleTestCase):
    def test_thresholds_match_arielvertex(self):
        cases = {0: "Free", 1: "Partially Available", 59: "Partially Available",
                 60: "Fully Allocated", 100: "Fully Allocated", 101: "Overloaded", 250: "Overloaded"}
        for total, expected in cases.items():
            self.assertEqual(access.availability_for(total), expected, total)


class HiringRequestTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@example.com", "9400000001")
        self.hr = make_employee("Hr", "hr@example.com", "9400000002")
        self.other = make_employee("Other", "other@example.com", "9400000003")
        self.project = make_project("HIRE")
        self.elsewhere = make_project("ELSE")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        grant(self.pm, "view_project", "view_resourcerequest", "add_resourcerequest")
        grant(self.hr, "view_resourcerequest", "change_resourcerequest")
        grant(self.other, "view_resourcerequest", "add_resourcerequest")

    def data(self, **extra):
        data = {
            "project": self.project.pk, "role_title": "Senior React Developer", "count": 2,
            "priority": 2, "skills": "React, TypeScript", "reason": "Scope grew",
        }
        data.update(extra)
        return data

    def raise_request(self, person=None, **extra):
        return self.login(person or self.pm).post(reverse("vertex-hiring-new"), self.data(**extra), **HX)

    def make_request(self, requester=None, **extra):
        extra.setdefault("role_title", "Tester")
        return ResourceRequest.objects.create(
            project=self.project, requested_by=requester or self.pm, **extra
        )

    # ---- access ----
    def test_no_group_sees_nothing(self):
        nobody = make_employee("Nobody", "n@example.com", "9400000004")
        client = self.login(nobody)
        self.assertNotEqual(client.get(reverse("vertex-hiring")).status_code, 200)
        client.post(reverse("vertex-hiring-new"), self.data(), **HX)
        self.assertFalse(ResourceRequest.objects.exists())

    def test_admin_needs_no_permission(self):
        user = self.other.employee_user_id
        user.is_superuser = True
        user.save()
        request = self.make_request()
        client = self.login(self.other)
        self.assertEqual(client.get(reverse("vertex-hiring")).status_code, 200)
        self.assertEqual(client.get(reverse("vertex-hiring-nav"), **HX).status_code, 200)
        self.assertContains(client.get(reverse("vertex-hiring-list"), **HX), "Tester")
        self.assertEqual(client.get(request.get_absolute_url()).status_code, 200)
        self.assertEqual(client.get(reverse("vertex-resources")).status_code, 200)

    # ---- raising ----
    def test_a_member_raises_a_request(self):
        self.raise_request()
        rr = ResourceRequest.objects.get()
        self.assertEqual((rr.requested_by, rr.project, rr.count, rr.status), (self.pm, self.project, 2, "open"))

    def test_the_raise_button_needs_the_add_permission(self):
        grant(self.hr, "view_project")
        self.raise_request(self.hr)
        self.assertFalse(ResourceRequest.objects.exists())

    def test_only_for_a_project_the_person_can_open(self):
        self.raise_request(project=self.elsewhere.pk)
        self.assertFalse(ResourceRequest.objects.exists())

    def test_validation(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        for bad in ({"count": 0}, {"count": 101}, {"role_title": "   "}, {"expected_start_date": yesterday}):
            self.raise_request(**bad)
        self.assertFalse(ResourceRequest.objects.exists())
        self.raise_request(expected_start_date=timezone.localdate().isoformat())
        self.assertEqual(ResourceRequest.objects.count(), 1)

    def test_hr_is_told_about_a_new_request(self):
        self.raise_request()
        self.assertEqual(notified(self.hr), 1)
        self.assertEqual(notified(self.pm), 0)
        self.assertEqual(notified(self.other), 0)

    def test_the_form_lists_only_my_projects(self):
        page = self.login(self.pm).get(reverse("vertex-hiring-new"), {"project": self.project.pk}, **HX)
        self.assertContains(page, "HIRE")
        self.assertNotContains(page, "ELSE")

    # ---- scoping ----
    def test_a_requester_sees_only_their_own(self):
        mine = self.make_request()
        theirs = self.make_request(requester=self.other, role_title="Designer")
        page = self.login(self.pm).get(reverse("vertex-hiring-list"), **HX)
        self.assertContains(page, "Tester")
        self.assertNotContains(page, "Designer")
        self.assertEqual(self.login(self.pm).get(mine.get_absolute_url()).status_code, 200)
        self.assertEqual(self.login(self.pm).get(theirs.get_absolute_url()).status_code, 404)

    def test_the_change_permission_and_the_all_switch_see_everything(self):
        self.make_request()
        self.make_request(requester=self.other, role_title="Designer")
        grant(self.other, "view_allresourcerequests")
        for person in (self.hr, self.other):
            page = self.login(person).get(reverse("vertex-hiring-list"), **HX)
            self.assertContains(page, "Tester")
            self.assertContains(page, "Designer")

    # ---- status flow ----
    def test_hr_moves_a_request_through_the_flow_with_a_comment(self):
        rr = self.make_request()
        self.login(self.hr).post(
            reverse("vertex-resource-request-status", args=[rr.pk]),
            {"status": "approved", "comment": "Budget cleared"},
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, "approved")
        comment = ResourceRequestComment.objects.get()
        self.assertEqual((comment.message, comment.author), ("Budget cleared", self.hr))
        self.assertEqual(notified(self.pm), 1)

    def test_every_status_can_be_set(self):
        rr = self.make_request()
        client = self.login(self.hr)
        for status in ("under_review", "need_more_information", "approved", "in_hiring_process",
                       "candidate_shared", "fulfilled", "rejected", "closed", "open"):
            client.post(reverse("vertex-resource-request-status", args=[rr.pk]), {"status": status})
            rr.refresh_from_db()
            self.assertEqual(rr.status, status)

    def test_an_invalid_status_is_refused(self):
        rr = self.make_request()
        self.login(self.hr).post(
            reverse("vertex-resource-request-status", args=[rr.pk]), {"status": "bogus"}
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, "open")

    def test_nothing_changed_means_no_notification(self):
        rr = self.make_request()
        self.login(self.hr).post(
            reverse("vertex-resource-request-status", args=[rr.pk]), {"status": "open"}
        )
        self.assertEqual(notified(self.pm), 0)

    def test_the_requester_cannot_change_the_status(self):
        rr = self.make_request()
        self.login(self.pm).post(
            reverse("vertex-resource-request-status", args=[rr.pk]), {"status": "approved"}
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, "open")
        page = self.login(self.pm).get(rr.get_absolute_url())
        self.assertNotContains(page, reverse("vertex-resource-request-status", args=[rr.pk]))

    def test_hr_sees_the_status_form(self):
        rr = self.make_request()
        page = self.login(self.hr).get(rr.get_absolute_url())
        self.assertContains(page, reverse("vertex-resource-request-status", args=[rr.pk]))

    def test_writes_by_get_are_refused(self):
        rr = self.make_request()
        client = self.login(self.hr)
        for name in ("vertex-resource-request-status", "vertex-resource-request-comment"):
            self.assertEqual(client.get(reverse(name, args=[rr.pk])).status_code, 405, name)

    # ---- comments ----
    def test_requester_and_hr_talk_to_each_other(self):
        rr = self.make_request()
        url = reverse("vertex-resource-request-comment", args=[rr.pk])
        self.login(self.pm).post(url, {"message": "Any news?"})
        self.assertEqual(notified(self.hr), 1)
        self.assertEqual(notified(self.pm), 0)
        self.login(self.hr).post(url, {"message": "In review"})
        self.assertEqual(notified(self.pm), 1)
        self.assertEqual(ResourceRequestComment.objects.count(), 2)
        self.assertContains(self.login(self.pm).get(rr.get_absolute_url()), "In review")

    def test_someone_else_cannot_comment(self):
        rr = self.make_request()
        response = self.login(self.other).post(
            reverse("vertex-resource-request-comment", args=[rr.pk]), {"message": "hi"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ResourceRequestComment.objects.exists())

    def test_blank_comment_is_refused(self):
        rr = self.make_request()
        self.login(self.pm).post(
            reverse("vertex-resource-request-comment", args=[rr.pk]), {"message": "  "}
        )
        self.assertFalse(ResourceRequestComment.objects.exists())

    def test_the_project_team_tab_offers_the_request_button(self):
        page = self.login(self.pm).get(self.project.get_absolute_url(), {"tab": "team"})
        self.assertContains(page, reverse("vertex-hiring-new"))
        member = make_employee("Dev", "dev@example.com", "9400000005")
        join(self.project, member)
        grant(member, "view_project")
        page = self.login(member).get(self.project.get_absolute_url(), {"tab": "team"})
        self.assertNotContains(page, reverse("vertex-hiring-new"))


class ResourceVisibilityTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.viewer = make_employee("Viewer", "viewer@example.com", "9500000001")
        grant(self.viewer, "view_resourcevisibility")
        self.alpha = make_project("ALPHA")
        self.beta = make_project("BETA")
        self.free = make_employee("Quincy", "free@example.com", "9500000002")
        self.partial = make_employee("Paula", "partial@example.com", "9500000003")
        self.full = make_employee("Fred", "full@example.com", "9500000004")
        self.over = make_employee("Olaf", "over@example.com", "9500000005")
        for person, pct in ((self.partial, 40), (self.full, 60), (self.over, 70)):
            ProjectMember.objects.create(
                project=self.alpha, employee=person, allocation_pct=pct, start_date=date(2026, 1, 1)
            )
        ProjectMember.objects.create(
            project=self.beta, employee=self.over, allocation_pct=50, start_date=date(2026, 1, 1)
        )

    def rows(self, **kwargs):
        rows, buckets = access.occupancy(**kwargs)
        return {r["employee"].get_full_name(): r for r in rows}, {b["name"]: b["count"] for b in buckets}

    def test_page_needs_the_permission(self):
        nobody = make_employee("Nobody", "n@example.com", "9500000006")
        self.assertNotEqual(self.login(nobody).get(reverse("vertex-resources")).status_code, 200)
        self.assertEqual(self.login(self.viewer).get(reverse("vertex-resources")).status_code, 200)

    def test_everyone_is_listed_and_a_person_without_allocation_is_free(self):
        rows, buckets = self.rows()
        self.assertEqual(rows["Quincy"]["availability"], "Free")
        self.assertEqual(buckets["Free"], 2)  # Quincy and Viewer have no allocation

    def test_totals_and_buckets(self):
        rows, buckets = self.rows()
        self.assertEqual((rows["Paula"]["total"], rows["Paula"]["availability"]), (40, "Partially Available"))
        self.assertEqual((rows["Fred"]["total"], rows["Fred"]["availability"]), (60, "Fully Allocated"))
        self.assertEqual((rows["Olaf"]["total"], rows["Olaf"]["availability"]), (120, "Overloaded"))
        self.assertEqual(len(rows["Olaf"]["projects"]), 2)
        self.assertEqual(buckets["Overloaded"], 1)

    def test_ended_and_inactive_memberships_are_not_counted(self):
        ProjectMember.objects.filter(employee=self.over, project=self.beta).update(
            end_date=timezone.localdate() - timedelta(days=1)
        )
        ProjectMember.objects.filter(employee=self.full).update(is_active=False)
        rows, _ = self.rows()
        self.assertEqual(rows["Olaf"]["total"], 70)
        self.assertEqual(rows["Fred"]["availability"], "Free")

    def test_ordered_by_load(self):
        rows, _ = access.occupancy()
        totals = [r["total"] for r in rows]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_search_and_availability_filters(self):
        page = self.login(self.viewer).get(reverse("vertex-resources"), {"search": "paul"})
        self.assertContains(page, "Paula")
        self.assertNotContains(page, "Olaf")
        page = self.login(self.viewer).get(reverse("vertex-resources"), {"availability": "Overloaded"})
        self.assertContains(page, "Olaf")
        self.assertNotContains(page, "Paula")
        # an unknown value is ignored, not a crash
        self.assertEqual(
            self.login(self.viewer).get(reverse("vertex-resources"), {"availability": "x"}).status_code, 200
        )

    def test_inactive_employees_are_not_listed(self):
        self.free.is_active = False
        self.free.save()
        rows, _ = self.rows()
        self.assertNotIn("Quincy", rows)
