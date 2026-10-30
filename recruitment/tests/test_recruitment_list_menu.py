"""
Recruitment list: the row actions are one three-dot hover menu (as on the candidate list),
the Filter button shows its icon only, and the "Filters: ..." tag strip is gone.
"""

from django.test import Client
from django.urls import reverse

from base.models import Company, Department
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee
from recruitment.models import JobPosition, Recruitment


class RecruitmentListMenuTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Menu Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        department = Department.objects.create(department="Engineering")
        job = JobPosition.objects.create(job_position="Backend", department_id=department)
        self.recruitment = Recruitment.objects.create(
            title="Menu Recruitment", company_id=self.company, job_position_id=job
        )
        self.recruitment.open_positions.add(job)
        user, _ = make_employee("rec_menu_user")
        grant(
            user,
            "recruitment",
            "view_recruitment",
            "change_recruitment",
            "delete_recruitment",
        )
        self.client = Client(raise_request_exception=False)
        self.client.force_login(type(user).objects.get(pk=user.pk))

    def list_html(self, **params):
        return self.client.get(reverse("list-recruitment"), params).content.decode()

    def test_actions_are_one_three_dot_menu(self):
        html = self.list_html()
        self.assertIn("Menu Recruitment", html)
        self.assertEqual(html.count('class="row-action-btn"'), 1)
        for label in ("Create Dynamic Link", "Edit", "Duplicate", "Archive", "Delete"):
            self.assertIn(f"<span>{label}</span>", html)
        self.assertNotIn("Unarchive", html)
        # the old row of icon buttons is gone
        self.assertNotIn('title="Create Dynamic Link"', html)

    def test_menu_entries_point_at_the_recruitment(self):
        html = self.list_html()
        pk = self.recruitment.pk
        self.assertIn(reverse("recruitment-update", kwargs={"pk": pk}), html)
        self.assertIn(reverse("recruitment-duplicate", kwargs={"pk": pk}), html)
        self.assertIn(reverse("recruitment-dynamic-link", kwargs={"pk": pk}), html)
        self.assertIn(reverse("recruitment-archive", kwargs={"rec_id": pk}), html)
        self.assertIn(reverse("recruitment-delete", kwargs={"rec_id": pk}), html)

    def test_archived_recruitment_offers_unarchive_instead(self):
        Recruitment.objects.filter(pk=self.recruitment.pk).update(is_active=False)
        html = self.list_html(is_active="False")
        self.assertIn("<span>Unarchive</span>", html)
        self.assertNotIn("<span>Archive</span>", html)

    def test_without_change_permission_edit_and_link_are_hidden(self):
        user, _ = make_employee("rec_menu_viewer")
        grant(user, "recruitment", "view_recruitment")
        client = Client(raise_request_exception=False)
        client.force_login(type(user).objects.get(pk=user.pk))
        html = client.get(reverse("list-recruitment")).content.decode()
        self.assertIn("<span>Duplicate</span>", html)
        for label in ("Edit", "Create Dynamic Link", "Archive", "Delete"):
            self.assertNotIn(f"<span>{label}</span>", html)

    def test_no_filter_tag_strip(self):
        html = self.list_html(closed="false")
        self.assertNotIn("Filters:", html)

    def test_filter_button_is_icon_only_with_clear(self):
        html = self.client.get(reverse("nav-recruitment"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('aria-label="Filter"', html)
        self.assertNotIn('title="Filter"', html)  # a tooltip would sit on top of the pop-up
        self.assertIn("clearNavFilter", html)

    def test_pipeline_filter_popup_has_a_clear_button(self):
        html = self.client.get(reverse("cbv-pipeline-nav"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn("clearNavFilter", html)
        self.assertIn('class="nav-filter-dot"', html)

    def test_no_filter_tag_strip_on_the_pipeline_views(self):
        # the applied filter is only shown as the red dot on the Filter button
        from recruitment.cbv.pipeline import CandidateCard, CandidateList

        self.assertFalse(CandidateCard.show_filter_tags)
        self.assertFalse(CandidateList.show_filter_tags)
        response = self.client.get(reverse("cbv-pipeline-tab"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_filter_tags"])
