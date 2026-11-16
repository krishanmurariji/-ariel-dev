"""
vertex/tests/test_calls.py

Scheduling a project call: field caps, and the meeting link that the application generates through
the Recruitment meeting provider (Google Meet or Teams) instead of being typed in. The providers are
never contacted: every test replaces them.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from vertex import meetings
from vertex.models import ProjectCall, ProjectRole
from vertex.tests.test_projects import HX, VertexTestBase, grant, join, make_employee, make_project

OK = {"success": True, "created": True, "provider": "google_meet", "object_id": 7, "url": "https://meet.test/abc"}
UPDATED = {**OK, "created": False}


def when(days=2):
    return (timezone.localtime() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


class CallTestBase(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@calls.example", "9600000001")
        self.dev = make_employee("Dev", "dev@calls.example", "9600000002")
        self.project = make_project("CALLS")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev, ProjectRole.DEVELOPER)
        grant(self.pm, "view_project", "change_project", "add_projectcall")
        grant(self.dev, "view_project", "add_projectcall")  # may add calls, but does not manage the project
        self.add = reverse("vertex-call-add", args=[self.project.pk])

    def data(self, **extra):
        data = {
            "title": "Kickoff", "agenda": "Agree scope", "call_type": "customer", "scheduled_at": when(),
            "duration_minutes": 30, "attendees": "client@example.com, lead@example.com",
        }
        data.update(extra)
        return data

    def schedule(self, result, **extra):
        with patch("vertex.meetings._via_google", return_value=result):
            self.login(self.pm).post(self.add, self.data(**extra), **HX)

    def link_url(self, call):
        return reverse("vertex-call-link", args=[self.project.pk, call.pk])


class CallFormTests(CallTestBase):
    def test_meeting_link_is_no_longer_typed_in(self):
        page = self.login(self.pm).get(self.add, **HX).content.decode()
        self.assertNotIn('name="meeting_url"', page)
        self.assertIn("created for you", page)

    def test_title_and_description_are_capped(self):
        page = self.login(self.pm).get(self.add, **HX)
        self.assertContains(page, 'maxlength="120"')
        self.assertContains(page, 'maxlength="500"')
        self.schedule(OK, title="t" * 121)
        self.schedule(OK, agenda="a" * 501)
        self.assertFalse(ProjectCall.objects.exists())
        self.schedule(OK, title="t" * 120, agenda="a" * 500)
        self.assertEqual(ProjectCall.objects.count(), 1)

    def test_attendees_must_be_valid_emails_and_are_tidied(self):
        self.schedule(OK, attendees="client@example.com, not-an-email")
        self.assertFalse(ProjectCall.objects.exists())
        self.schedule(OK, attendees="a@example.com; b@example.com , a@example.com")
        self.assertEqual(ProjectCall.objects.get().attendees, "a@example.com, b@example.com")

    def test_too_many_attendees_and_odd_durations_are_refused(self):
        self.schedule(OK, attendees=",".join(f"p{i}@example.com" for i in range(21)))
        self.schedule(OK, duration_minutes=1)
        self.schedule(OK, duration_minutes=900)
        self.assertFalse(ProjectCall.objects.exists())

    def test_a_blank_title_is_refused(self):
        self.schedule(OK, title="   ")
        self.assertFalse(ProjectCall.objects.exists())


class CallMeetingTests(CallTestBase):
    def test_the_link_is_generated_when_the_call_is_scheduled(self):
        self.schedule(OK)
        call = ProjectCall.objects.get()
        self.assertEqual((call.meeting_url, call.meeting_provider, call.meeting_object_id), ("https://meet.test/abc", "google_meet", 7))
        page = self.login(self.pm).get(self.project.get_absolute_url(), {"tab": "calls"})
        self.assertContains(page, "https://meet.test/abc")
        self.assertContains(page, "Join")

    def test_a_provider_problem_keeps_the_call_and_shows_why(self):
        self.schedule({"error": "Google Meet integration is disabled."})
        call = ProjectCall.objects.get()
        self.assertEqual(call.meeting_url, "")
        self.assertEqual(call.meeting_error, "Google Meet integration is disabled.")
        page = self.login(self.pm).get(self.project.get_absolute_url(), {"tab": "calls"})
        self.assertContains(page, "Generate link")
        self.assertContains(page, "Google Meet integration is disabled.")

    def test_a_provider_that_crashes_never_loses_the_call(self):
        with patch("vertex.meetings._via_google", side_effect=RuntimeError("boom")):
            self.login(self.pm).post(self.add, self.data(), **HX)
        call = ProjectCall.objects.get()
        self.assertEqual(call.meeting_error, "boom")

    def test_not_connected_is_reported_as_a_warning_not_an_error(self):
        self.schedule({"needs_auth": True, "message": "Your Google account is not connected yet.", "redirect": "/connect/"})
        call = ProjectCall.objects.get()
        self.assertIn("not connected", call.meeting_error)

    def test_generate_link_again_fills_the_link_and_clears_the_error(self):
        self.schedule({"error": "Temporary problem"})
        call = ProjectCall.objects.get()
        with patch("vertex.meetings._via_google", return_value=OK):
            response = self.login(self.pm).post(self.link_url(call))
        self.assertEqual(response.status_code, 302)
        call.refresh_from_db()
        self.assertEqual((call.meeting_url, call.meeting_error), ("https://meet.test/abc", ""))

    def test_updating_reuses_the_same_meeting(self):
        self.schedule(OK)
        call = ProjectCall.objects.get()
        seen = {}

        def fake(call_, request, data):
            seen["reuse"] = call_.meeting_object_id if call_.meeting_provider == "google_meet" else None
            return UPDATED

        with patch("vertex.meetings._via_google", side_effect=fake):
            self.login(self.pm).post(self.link_url(call))
        self.assertEqual(seen["reuse"], 7)

    def test_not_connected_sends_the_user_to_connect_when_they_retry(self):
        self.schedule({"error": "x"})
        call = ProjectCall.objects.get()
        with patch("vertex.meetings._via_google", return_value={"needs_auth": True, "message": "Connect Google", "redirect": "/connect/"}):
            response = self.login(self.pm).post(self.link_url(call))
        self.assertEqual(response["Location"], "/connect/")

    def test_a_link_with_no_url_from_the_provider_counts_as_a_failure(self):
        with patch("vertex.meetings._provider", return_value="google_meet"), patch(
            "vertex.meetings._via_google", return_value={**OK, "url": ""}
        ):
            self.login(self.pm).post(self.add, self.data(), **HX)
        call = ProjectCall.objects.get()
        self.assertEqual(call.meeting_url, "")
        self.assertIn("did not return a meeting link", call.meeting_error)

    def test_only_people_who_manage_the_project_can_generate_a_link(self):
        self.schedule({"error": "x"})
        call = ProjectCall.objects.get()
        self.assertEqual(self.login(self.pm).get(self.link_url(call)).status_code, 405)
        with patch("vertex.meetings._via_google", return_value=OK) as provider:
            self.login(self.dev).post(self.link_url(call))
            outsider = make_employee("Out", "out@calls.example", "9600000003")
            grant(outsider, "view_project", "add_projectcall")
            self.assertEqual(self.login(outsider).post(self.link_url(call)).status_code, 404)
        provider.assert_not_called()
        call.refresh_from_db()
        self.assertEqual(call.meeting_url, "")

    def test_buttons_follow_the_permission(self):
        self.schedule(OK)
        self.assertContains(self.login(self.pm).get(self.project.get_absolute_url(), {"tab": "calls"}), "Update link")
        page = self.login(self.dev).get(self.project.get_absolute_url(), {"tab": "calls"})
        self.assertContains(page, "Join")
        self.assertNotContains(page, "Update link")


class ProviderChoiceTests(CallTestBase):
    """The Teams path goes through the existing Outlook adapter; the Graph call itself is replaced."""

    def test_teams_is_used_when_recruitment_is_set_to_teams(self):
        from recruitment.models import RecruitmentGeneralSetting

        RecruitmentGeneralSetting.objects.update_or_create(
            company_id=None, defaults={"default_meeting_provider": "teams"}
        )
        api = SimpleNamespace(token="t")
        event = {"id": "EV1", "onlineMeeting": {"joinUrl": "https://teams.test/join/1"}}
        with patch("outlook_auth.methods.get_azure_api", return_value=api), patch(
            "outlook_auth.signals.get_azure_api", return_value=api
        ), patch("outlook_auth.signals.create_teams_calendar_event", return_value=event) as graph:
            self.login(self.pm).post(self.add, self.data(), **HX)
        call = ProjectCall.objects.get()
        self.assertEqual((call.meeting_provider, call.meeting_url), ("teams", "https://teams.test/join/1"))
        sent = graph.call_args[0][1]
        self.assertEqual(sent["attendees"], ["client@example.com", "lead@example.com"])
        self.assertTrue(sent["is_online"])
        self.assertIn(self.project.name, sent["description"])

    def test_teams_not_connected_is_reported(self):
        from recruitment.models import RecruitmentGeneralSetting

        RecruitmentGeneralSetting.objects.update_or_create(
            company_id=None, defaults={"default_meeting_provider": "teams"}
        )
        with patch("outlook_auth.methods.get_azure_api", return_value=None):
            self.login(self.pm).post(self.add, self.data(), **HX)
        call = ProjectCall.objects.get()
        self.assertEqual(call.meeting_url, "")
        self.assertIn("Outlook is not connected", call.meeting_error)

    def test_google_is_the_default_without_a_setting(self):
        self.assertEqual(meetings._provider(SimpleNamespace(session={})), "google_meet")
