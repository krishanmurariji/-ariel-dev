"""
The old "Resignations" list page (approve / reject / edit / single view on a list of letters)
is switched off - requests are reviewed under Exit Process > Under Review. Its old
addresses still answer so links do not break, and the employee-side pieces (their own
profile tab, filing and deleting their own resignation) keep working.
"""

from django.urls import reverse

from offboarding import sidebar
from offboarding.models import ResignationLetter
from offboarding.test_exit_pages import HX, PageBase

RETIRED = (
    ("resignation-request-view", []),
    ("list-resignation-request", []),
    ("nav-resignation-request", []),
    ("resignation-request-update", [1]),
    ("resignation-request-single-view", [1]),
    ("update-letter-status", []),
)


class RetiredPageTests(PageBase):
    def test_the_old_page_leads_to_exit_process(self):
        self.client.force_login(self.hr_user)
        r = self.client.get(reverse("resignation-request-view"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], reverse("exit-process") + "?tab=review")

    def test_every_old_address_answers_with_a_pointer_not_an_error(self):
        self.client.force_login(self.hr_user)
        for name, args in RETIRED:
            with self.subTest(name):
                html = self.client.get(reverse(name, args=args), **HX)
                self.assertEqual(html.status_code, 200)
                self.assertIn("Exit Process", html.content.decode())

    def test_the_old_approve_button_no_longer_changes_a_letter(self):
        self.client.force_login(self.hr_user)
        before = self.letter.status
        self.client.get(
            reverse("update-letter-status") + f"?letter_ids={self.letter.pk}&status=approved", **HX
        )
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, before)

    def test_a_visitor_who_is_not_logged_in_is_sent_to_login(self):
        r = self.client.get(reverse("resignation-request-view"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_the_menu_no_longer_offers_it(self):
        names = [str(item["menu"]) for item in sidebar.SUBMENUS]
        self.assertNotIn("Resignation Letters", names)
        self.assertIn("Exit Process", names)

    def test_the_employee_side_still_works(self):
        self.client.force_login(self.emp_user)
        tab = self.client.get(reverse("individual-resignation-tab-list", kwargs={"pk": self.emp.pk}), **HX)
        self.assertEqual(tab.status_code, 200)
        self.assertEqual(self.client.get(reverse("resignation-self-create"), **HX).status_code, 200)
        count = ResignationLetter.objects.count()
        self.client.post(reverse("delete-resignation-request") + f"?letter_ids={self.letter.pk}", **HX)
        self.assertLessEqual(ResignationLetter.objects.count(), count)  # the route is still live

    def test_the_dashboard_still_opens(self):
        self.client.force_login(self.hr_user)
        self.assertEqual(self.client.get(reverse("offboarding-dashboard")).status_code, 200)
