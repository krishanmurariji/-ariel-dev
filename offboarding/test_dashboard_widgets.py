"""
The Offboarding dashboard keeps only widgets that do not depend on the old resignation
list or the old offboarding tasks (those are commented out until new widgets replace them).
"""

from django.urls import reverse

from offboarding.test_exit_pages import PageBase, _full_page


class DashboardWidgetTests(PageBase):
    def page(self):
        self.client.force_login(self.hr_user)
        response = _full_page(self, reverse("offboarding-dashboard"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_retired_widgets_are_gone(self):
        html = self.page()
        for gone in (
            'data-chart-id="resignation_status"',
            'data-chart-id="task_completion"',
            'data-chart-id="offboarding_pipeline"',
            'data-chart-id="exit_reasons"',
            'data-chart-id="join_exit_trend"',
            "chart-resignation",
            "offboarding-dashboard-resignation",
            "offboarding-dashboard-tasks",
            "pending_resignations",
            "task_completion",
        ):
            self.assertNotIn(gone, html, gone)

    def test_the_other_widgets_remain(self):
        html = self.page()
        for kept in (
            'data-chart-id="dept_attrition"',
            "Notice Period Tracker",
            "Unreturned Assets",
            "Active Offboarding",
            "Archived",
            "ob-kpi-grid",
        ):
            self.assertIn(kept, html, kept)
        self.assertEqual(html.count('class="ob-skeleton ob-skeleton--kpi"'), 2)

    def test_the_data_calls_that_remain_are_still_wired(self):
        html = self.page()
        for url_name in (
            "offboarding-dashboard-kpi",
            "offboarding-dashboard-dept",
            "offboarding-dashboard-notice",
            "offboarding-dashboard-assets",
        ):
            self.assertIn(reverse(url_name), html, url_name)
