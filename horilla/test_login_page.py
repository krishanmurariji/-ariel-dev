"""
The sign-in page: the button starts switched off and only comes on once both fields hold something,
including when the browser fills saved logins in without any typing events.
"""

from django.test import Client
from django.urls import reverse

from horilla.isolated_testcase import IsolatedTestCase


class SignInButton(IsolatedTestCase):
    def page(self):
        return Client(raise_request_exception=False).get(reverse("login")).content.decode()

    def test_the_button_is_there_and_not_hard_coded_off(self):
        html = self.page()
        start = html.index('id="signInButton"')
        tag = html[html.rindex("<button", 0, start) : html.index(">", start) + 1]
        # a hard-coded "disabled" would lock out anyone whose JavaScript fails to load
        self.assertNotIn("disabled", tag)

    def test_the_page_switches_it_by_what_is_in_the_fields(self):
        html = self.page()
        self.assertIn("button.disabled = !ready()", html)
        self.assertIn('user.value.trim() !== "" && pass.value !== ""', html)

    def test_saved_logins_filled_in_silently_are_picked_up(self):
        html = self.page()
        for hook in ('"pageshow"', '"visibilitychange"', "setInterval", '"change"'):
            self.assertIn(hook, html)

    def test_an_empty_form_cannot_be_submitted(self):
        self.assertIn("event.preventDefault()", self.page())
