"""
The AI summary of an exit's history in the Manage window: it is built only from the
recorded history, shown only to people who can open that exit, written again only when
the history changes, and never breaks the window when the AI is down.
"""

import json
from unittest import mock

from django.core.cache import cache
from django.urls import reverse

from offboarding import exit_ai
from offboarding.models import ExitActivity
from offboarding.test_exit_pages import HX
from offboarding.test_exit_manage import ManageBase
from recruitment import ai_client

def add_step(employee, comment, action="reminder"):
    return ExitActivity.objects.bulk_create([ExitActivity(employee_id=employee, action=action, comment=comment)])[0]


REPLY = json.dumps({"summary": "The exit was accepted and one asset is outstanding.", "highlights": ["Asset pending", "Survey not sent"]})


class SummaryBase(ManageBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = reverse("exit-history-summary", args=[self.case.pk])

    def post(self, user, data=None, **extra):
        self.client.force_login(user)
        return self.client.post(self.url, data or {}, **HX, **extra)

    def patched(self, reply=REPLY):
        return mock.patch.object(ai_client, "chat", return_value=reply)


class SummaryTests(SummaryBase):
    def test_the_summary_is_shown_and_built_from_the_history_only(self):
        with self.patched() as chat:
            html = self.post(self.hr_user).content.decode()
        self.assertIn("The exit was accepted and one asset is outstanding.", html)
        self.assertIn("Asset pending", html)
        prompt = chat.call_args[0][0]
        self.assertIn("Accepted", prompt)  # the recorded step
        self.assertIn("use ONLY facts", prompt)
        self.assertEqual(chat.call_args.kwargs["temperature"], 0)

    def test_a_second_request_reuses_it_until_the_history_changes_or_a_new_one_is_asked_for(self):
        with self.patched() as chat:
            self.post(self.hr_user)
            self.post(self.hr_user)
            self.assertEqual(chat.call_count, 1)
            self.post(self.hr_user, {"fresh": "1"})
            self.assertEqual(chat.call_count, 2)
            add_step(self.emp, "chased")
            self.post(self.hr_user)
            self.assertEqual(chat.call_count, 3)

    def test_what_the_ai_writes_is_shown_as_text_never_as_html(self):
        reply = json.dumps({"summary": "<script>alert(1)</script> done", "highlights": ["<b>x</b>"]})
        with self.patched(reply):
            html = self.post(self.hr_user).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_an_unreachable_ai_gives_a_friendly_message_not_an_error_page(self):
        with mock.patch.object(ai_client, "chat", side_effect=ai_client.AIProviderError("down")):
            response = self.post(self.hr_user)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Could not summarise", response.content.decode())
        self.assertIn("Try again", response.content.decode())

    def test_an_unreadable_answer_is_reported_not_shown(self):
        for bad in ("not json", "[]", json.dumps({"summary": ""}), json.dumps({"x": 1})):
            with self.subTest(bad=bad), self.patched(bad):
                html = self.post(self.hr_user).content.decode()
                self.assertIn("Could not summarise", html)

    def test_no_history_means_nothing_to_summarise_and_no_ai_call(self):
        ExitActivity.objects.entire().filter(employee_id=self.emp).delete()
        with self.patched() as chat:
            html = self.post(self.hr_user).content.decode()
        chat.assert_not_called()
        self.assertIn("no history to summarise", html)

    def test_only_the_latest_steps_are_sent(self):
        for n in range(exit_ai.MAX_EVENTS + 10):
            add_step(self.emp, f"note {n}")
        rows = exit_ai._events(self.case)
        self.assertEqual(len(rows), exit_ai.MAX_EVENTS)
        self.assertEqual(rows[-1].comment, f"note {exit_ai.MAX_EVENTS + 9}")  # newest kept, oldest first


class SummaryAccessTests(SummaryBase):
    def test_someone_who_cannot_open_the_exit_gets_nothing_and_no_ai_call(self):
        with self.patched() as chat:
            response = self.post(self.emp_user)
        self.assertNotIn("AI summary", response.content.decode())
        chat.assert_not_called()

    def test_it_needs_a_post_and_an_htmx_request(self):
        self.client.force_login(self.hr_user)
        with self.patched() as chat:
            self.assertEqual(self.client.get(self.url, **HX).status_code, 405)
            self.assertNotEqual(self.client.post(self.url).status_code, 200)
        chat.assert_not_called()

    def test_the_button_is_on_the_summary_tab(self):
        self.client.force_login(self.hr_user)
        html = self.client.get(reverse("exit-manage", args=[self.case.pk, "summary"]), **HX).content.decode()
        self.assertIn("Summarise with AI", html)
        self.assertIn(self.url, html)
