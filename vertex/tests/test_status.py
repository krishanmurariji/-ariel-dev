"""
vertex/tests/test_status.py

Status updates (ArielVertex StatusUpdatesController): submit only for projects the person is
a member of, business people never see internal detail, consolidated summary for managers.
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from vertex import access
from vertex.models import ProjectRole, StatusUpdate
from vertex.tests.test_projects import (
    HX,
    VertexTestBase,
    grant,
    join,
    make_employee,
    make_project,
)


def make_update(project, employee, **extra):
    data = {"update_date": timezone.localdate(), "work_completed": "did things"}
    data.update(extra)
    return StatusUpdate.objects.create(project=project, employee=employee, **data)


class StatusSubmitTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.dev = make_employee("Dev", "dev@example.com", "9300000001")
        self.mine = make_project("MINE")
        self.other = make_project("OTHER")
        join(self.mine, self.dev)
        grant(self.dev, "view_statusupdate", "add_statusupdate")

    def data(self, **extra):
        data = {
            "project": self.mine.pk, "update_date": timezone.localdate().isoformat(),
            "hours_spent": "7.5", "status": "on_track", "work_completed": "Built the thing",
            "client_shareable_summary": "Progress is good",
        }
        data.update(extra)
        return data

    def submit(self, **extra):
        return self.login(self.dev).post(reverse("vertex-status-new"), self.data(**extra), **HX)

    def test_no_group_sees_nothing(self):
        other = make_employee("Nobody", "n@example.com", "9300000002")
        client = self.login(other)
        self.assertNotEqual(client.get(reverse("vertex-status")).status_code, 200)
        client.post(reverse("vertex-status-new"), self.data(), **HX)
        self.assertFalse(StatusUpdate.objects.exists())

    def test_member_submits_an_update(self):
        self.submit()
        update = StatusUpdate.objects.get()
        self.assertEqual((update.employee, update.project, str(update.hours_spent)), (self.dev, self.mine, "7.5"))

    def test_only_projects_the_person_is_on(self):
        self.submit(project=self.other.pk)
        self.assertFalse(StatusUpdate.objects.exists())

    def test_inactive_membership_cannot_submit(self):
        self.mine.members.update(is_active=False)
        self.submit()
        self.assertFalse(StatusUpdate.objects.exists())

    def test_needs_the_add_permission(self):
        grant(self.dev, "view_project")
        other = make_employee("Viewer", "v@example.com", "9300000003")
        join(self.mine, other)
        grant(other, "view_statusupdate")
        self.login(other).post(reverse("vertex-status-new"), self.data(), **HX)
        self.assertFalse(StatusUpdate.objects.exists())

    def test_validation(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        for bad in (
            {"update_date": tomorrow},
            {"hours_spent": "25"},
            {"hours_spent": "-1"},
            {"work_completed": "   "},
            {"status": "great"},
        ):
            self.submit(**bad)
        self.assertFalse(StatusUpdate.objects.exists())

    def test_form_lists_only_my_projects(self):
        page = self.login(self.dev).get(reverse("vertex-status-new"), **HX)
        self.assertContains(page, "MINE")
        self.assertNotContains(page, "OTHER")

    def test_my_updates_list_shows_only_my_own(self):
        colleague = make_employee("Col", "c@example.com", "9300000004")
        join(self.mine, colleague)
        make_update(self.mine, self.dev, work_completed="my own work")
        make_update(self.mine, colleague, work_completed="colleague work")
        page = self.login(self.dev).get(reverse("vertex-status-list"), **HX)
        self.assertContains(page, "my own work")
        self.assertNotContains(page, "colleague work")

    def test_page_and_nav_render(self):
        client = self.login(self.dev)
        self.assertEqual(client.get(reverse("vertex-status")).status_code, 200)
        self.assertEqual(client.get(reverse("vertex-status-nav"), **HX).status_code, 200)


class ProjectStatusTabTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@example.com", "9300000011")
        self.dev = make_employee("Dev", "dev@example.com", "9300000012")
        self.dev2 = make_employee("Quiet", "quiet@example.com", "9300000013")
        self.qa = make_employee("Qa", "qa@example.com", "9300000014")
        self.biz = make_employee("Biz", "biz@example.com", "9300000015")
        self.project = make_project("STAT")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev, ProjectRole.DEVELOPER)
        join(self.project, self.dev2, ProjectRole.DEVELOPER)
        join(self.project, self.qa, ProjectRole.QA)
        join(self.project, self.biz, ProjectRole.BUSINESS_PERSON)
        for person in (self.pm, self.dev, self.dev2, self.qa, self.biz):
            grant(person, "view_project", "view_statusupdate")
        grant(self.pm, "change_project")
        make_update(
            self.project, self.dev, work_completed="secret engineering detail",
            blockers="waiting for api", internal_note="team is tired",
            client_shareable_summary="Feature on track", status="at_risk",
        )
        make_update(self.project, self.qa, client_shareable_summary="")

    def tab(self, person, **params):
        return self.login(person).get(
            self.project.get_absolute_url(), {"tab": "status", **params}
        )

    def test_business_person_sees_only_the_client_summary(self):
        page = self.tab(self.biz)
        self.assertContains(page, "Feature on track")
        self.assertContains(page, "(No client summary provided.)")
        for hidden in ("secret engineering detail", "waiting for api", "team is tired"):
            self.assertNotContains(page, hidden)
        self.assertNotContains(page, "Consolidated summary")

    def test_a_teammate_sees_the_detail_but_not_the_internal_note(self):
        page = self.tab(self.dev2)
        self.assertContains(page, "secret engineering detail")
        self.assertContains(page, "waiting for api")
        self.assertNotContains(page, "team is tired")
        self.assertNotContains(page, "Consolidated summary")

    def test_manager_sees_internal_note_and_the_consolidated_summary(self):
        page = self.tab(self.pm)
        self.assertContains(page, "team is tired")
        self.assertContains(page, "Consolidated summary")
        # 2 of 3 developers / QA reported; Quiet did not
        self.assertContains(page, "2/3")
        self.assertContains(page, "Quiet")

    def test_the_see_all_switch_shows_internal_detail(self):
        grant(self.dev2, "view_allstatusupdates")
        self.assertContains(self.tab(self.dev2), "team is tired")

    def test_a_business_person_with_the_see_all_switch_is_not_restricted(self):
        grant(self.biz, "view_allstatusupdates")
        self.assertContains(self.tab(self.biz), "secret engineering detail")

    def test_the_tab_needs_the_view_permission(self):
        member = make_employee("Plain", "p@example.com", "9300000016")
        join(self.project, member)
        grant(member, "view_project")
        page = self.tab(member)
        self.assertNotContains(page, "secret engineering detail")

    def test_consolidated_summary_window(self):
        make_update(
            self.project, self.dev2, update_date=timezone.localdate() - timedelta(days=20)
        )
        week = access.consolidated_summary(self.project, 7)
        self.assertEqual((week["reporting"], week["expected"], week["missing"]), (2, 3, ["Quiet"]))
        month = access.consolidated_summary(self.project, 30)
        self.assertEqual((month["reporting"], month["missing"]), (3, []))

    def test_consolidated_days_are_clamped(self):
        self.assertEqual(access.consolidated_summary(self.project, 9999)["days"], 60)
        self.assertEqual(access.consolidated_summary(self.project, 0)["days"], 1)
        # a junk value in the URL is ignored, not a crash
        self.assertEqual(self.tab(self.pm, days="abc").status_code, 200)
