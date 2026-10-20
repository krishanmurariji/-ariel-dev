"""
The trail at the top of every Offboarding page ("Company > Offboarding > Exit Process") must
use the page's real name, start fresh on each menu page, and not suggest that "My Exit
Tasks" lives inside "Exit Process".
"""

from django.urls import reverse

from offboarding.test_exit_pages import PageBase, _full_page


class BreadcrumbTests(PageBase):
    def trail(self, url):
        self.client.force_login(self.hr_user)
        response = _full_page(self, url)
        self.assertEqual(response.status_code, 200, url)
        return [crumb["name"] for crumb in response.context["breadcrumbs"]][1:]  # without the company

    def test_pages_show_their_real_name_not_the_address(self):
        self.assertEqual(self.trail(reverse("exit-process")), ["Offboarding", "Exit Process"])
        self.assertEqual(self.trail(reverse("exit-settings")), ["Offboarding", "Exit Settings"])

    def test_the_dashboard_is_named(self):
        self.assertEqual(self.trail(reverse("offboarding-dashboard")), ["Offboarding", "Offboarding Dashboard"])

    def test_my_exit_tasks_is_not_shown_inside_exit_process(self):
        self.assertEqual(self.trail(reverse("exit-my-tasks")), ["Offboarding", "My Exit Tasks"])

    def test_each_menu_page_starts_a_fresh_trail(self):
        self.trail(reverse("offboarding-dashboard"))
        self.trail(reverse("exit-settings"))
        self.assertEqual(self.trail(reverse("exit-process")), ["Offboarding", "Exit Process"])
        self.assertEqual(self.trail(reverse("exit-my-tasks")), ["Offboarding", "My Exit Tasks"])
        self.assertEqual(self.trail(reverse("exit-process")), ["Offboarding", "Exit Process"])

    def test_the_offboarding_crumb_leads_somewhere_real(self):
        self.client.force_login(self.hr_user)
        crumbs = _full_page(self, reverse("exit-process")).context["breadcrumbs"]
        offboarding = next(c for c in crumbs if c["name"] == "Offboarding")
        self.assertTrue(offboarding["found"])
        self.assertTrue(offboarding["clickable"])
