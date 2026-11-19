"""
vertex/tests/test_review_meetings.py

The meeting link of a review is generated through the Recruitment meeting provider (Google Meet or
Teams), like the link of a call, instead of being typed in. Group reviews share one calendar event.
The providers are never contacted: every test replaces them.
"""

from unittest.mock import patch

from django.urls import reverse

from vertex.models import ProjectRole, ReviewMeeting, ReviewRequest, ReviewRequestStatus
from vertex.tests.test_projects import HX, VertexTestBase, grant, join, make_employee, make_project

OK = {"success": True, "created": True, "provider": "google_meet", "object_id": 9, "url": "https://meet.test/rev"}
DENIED = "You Dont Have Permission"


class ReviewMeetingBase(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.hr = make_employee("Hr", "hr@rm.example", "9650000001")
        self.pm = make_employee("Pm", "pm@rm.example", "9650000002")
        self.ann = make_employee("Ann", "ann@rm.example", "9650000003")
        self.bob = make_employee("Bob", "bob@rm.example", "9650000004")
        self.project = make_project("RMEET")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.ann)
        join(self.project, self.bob)
        grant(self.hr, "view_reviewrequest", "add_reviewrequest", "change_reviewrequest", "change_project")
        grant(self.pm, "view_reviewrequest", "change_reviewrequest", "change_project")
        grant(self.ann, "view_reviewrequest")

    def group(self, **extra):
        data = {
            "project": self.project.pk, "subjects": [self.ann.pk, self.bob.pk], "review_type": "project_review",
            "assigned_to": self.pm.pk, "notes": "", "title": "Quarter review", "scheduled_at": "2026-12-01T10:00",
            "duration_minutes": 45, "extra_attendees": "guest@example.com",
        }
        data.update(extra)
        return data

    def single(self, **extra):
        data = {
            "title": "Review", "scheduled_at": "2026-12-02T09:30", "duration_minutes": 30,
            "assigned_to": self.pm.pk, "extra_attendees": "x@example.com", "description": "Agenda",
        }
        data.update(extra)
        return data

    def make_review(self):
        return ReviewRequest.objects.create(project=self.project, subject=self.ann, requested_by=self.hr, assigned_to=self.pm)

    def link(self, review):
        return reverse("vertex-review-link", args=[review.pk])


class ReviewFormTests(ReviewMeetingBase):
    def test_the_link_is_not_typed_in_any_more(self):
        self.assertNotIn('name="meeting_url"', self.login(self.hr).get(reverse("vertex-review-new"), **HX).content.decode())
        review = self.make_review()
        page = self.login(self.pm).get(review.get_absolute_url()).content.decode()
        self.assertNotIn('name="meeting_url"', page)

    def test_title_description_and_attendees_are_checked(self):
        review = self.make_review()
        client = self.login(self.pm)
        with patch("vertex.meetings._via_google", return_value=OK):
            client.post(reverse("vertex-review-schedule", args=[review.pk]), self.single(title="t" * 121))
            client.post(reverse("vertex-review-schedule", args=[review.pk]), self.single(description="d" * 501))
            client.post(reverse("vertex-review-schedule", args=[review.pk]), self.single(extra_attendees="nope"))
        self.assertFalse(ReviewMeeting.objects.exists())
        with patch("vertex.meetings._via_google", return_value=OK):
            self.login(self.hr).post(reverse("vertex-review-new"), self.group(extra_attendees="bad address"), **HX)
        self.assertFalse(ReviewRequest.objects.exclude(pk=review.pk).exists())


class GroupReviewTests(ReviewMeetingBase):
    def test_one_calendar_event_for_the_whole_group(self):
        with patch("vertex.meetings._via_google", return_value=OK) as provider:
            self.login(self.hr).post(reverse("vertex-review-new"), self.group(), **HX)
        provider.assert_called_once()
        meetings = list(ReviewMeeting.objects.all())
        self.assertEqual(len(meetings), 2)
        self.assertEqual({(m.meeting_url, m.meeting_object_id, m.meeting_provider) for m in meetings},
                         {("https://meet.test/rev", 9, "google_meet")})

    def test_the_provider_is_given_everyone_who_should_be_invited(self):
        seen = {}

        def fake(obj, request, data):
            seen.update(data)
            return OK

        with patch("vertex.meetings._via_google", side_effect=fake):
            self.login(self.hr).post(reverse("vertex-review-new"), self.group(), **HX)
        self.assertEqual(
            set(seen["attendees"]),
            {"ann@rm.example", "bob@rm.example", "pm@rm.example", "hr@rm.example", "guest@example.com"},
        )
        self.assertEqual((seen["title"], seen["duration"], seen["is_online"]), ("Quarter review", 45, True))
        self.assertIn(self.project.name, seen["description"])

    def test_a_provider_problem_still_creates_the_reviews(self):
        with patch("vertex.meetings._via_google", return_value={"error": "Google Meet integration is disabled."}):
            self.login(self.hr).post(reverse("vertex-review-new"), self.group(), **HX)
        self.assertEqual(ReviewRequest.objects.count(), 2)
        self.assertEqual({r.status for r in ReviewRequest.objects.all()}, {ReviewRequestStatus.SCHEDULED})
        for meeting in ReviewMeeting.objects.all():
            self.assertEqual(meeting.meeting_url, "")
            self.assertIn("disabled", meeting.meeting_error)

    def test_no_meeting_time_means_no_provider_call(self):
        with patch("vertex.meetings._via_google", return_value=OK) as provider:
            self.login(self.hr).post(reverse("vertex-review-new"), self.group(title="", scheduled_at=""), **HX)
        provider.assert_not_called()
        self.assertFalse(ReviewMeeting.objects.exists())


class ScheduleReviewTests(ReviewMeetingBase):
    def schedule(self, review, who=None, **extra):
        return (who or self.login(self.pm)).post(reverse("vertex-review-schedule", args=[review.pk]), self.single(**extra))

    def test_scheduling_generates_the_link(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value=OK):
            self.schedule(review)
        meeting = ReviewMeeting.objects.get()
        self.assertEqual((meeting.meeting_url, meeting.meeting_error), ("https://meet.test/rev", ""))
        page = self.login(self.pm).get(review.get_absolute_url())
        self.assertContains(page, "https://meet.test/rev")
        self.assertContains(page, "Update link")

    def test_subject_reviewer_and_scheduler_are_invited_automatically(self):
        review = self.make_review()
        seen = {}

        def fake(obj, request, data):
            seen.update(data)
            return OK

        with patch("vertex.meetings._via_google", side_effect=fake):
            self.schedule(review)
        self.assertEqual(set(seen["attendees"]), {"ann@rm.example", "pm@rm.example", "x@example.com"})

    def test_a_failure_keeps_the_meeting_shows_why_and_can_be_retried(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value={"error": "Temporary problem"}):
            self.schedule(review)
        meeting = ReviewMeeting.objects.get()
        self.assertEqual((meeting.meeting_url, meeting.meeting_error), ("", "Temporary problem"))
        review.refresh_from_db()
        self.assertEqual(review.status, ReviewRequestStatus.SCHEDULED)
        page = self.login(self.pm).get(review.get_absolute_url())
        self.assertContains(page, "Generate link")
        self.assertContains(page, "Temporary problem")
        with patch("vertex.meetings._via_google", return_value=OK):
            response = self.login(self.pm).post(self.link(review))
        self.assertEqual(response.status_code, 302)
        meeting.refresh_from_db()
        self.assertEqual((meeting.meeting_url, meeting.meeting_error), ("https://meet.test/rev", ""))

    def test_a_provider_that_crashes_never_loses_the_review(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", side_effect=RuntimeError("boom")):
            self.schedule(review)
        self.assertEqual(ReviewMeeting.objects.get().meeting_error, "boom")

    def test_rescheduling_updates_the_same_calendar_event(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value=OK):
            self.schedule(review)
        seen = {}

        def fake(obj, request, data):
            seen["reuse"] = obj.meeting_object_id if obj.meeting_provider == "google_meet" else None
            return {**OK, "created": False}

        with patch("vertex.meetings._via_google", side_effect=fake):
            self.schedule(review, title="Moved")
        self.assertEqual(seen["reuse"], 9)
        self.assertEqual(ReviewMeeting.objects.count(), 1)

    def test_not_connected_sends_the_user_to_connect_when_they_retry(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value={"error": "x"}):
            self.schedule(review)
        needs = {"needs_auth": True, "message": "Connect Google", "redirect": "/connect/"}
        with patch("vertex.meetings._via_google", return_value=needs):
            response = self.login(self.pm).post(self.link(review))
        self.assertEqual(response["Location"], "/connect/")

    def test_generating_needs_a_scheduled_review_and_the_right_person(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value=OK) as provider:
            self.login(self.pm).post(self.link(review))  # nothing scheduled yet
            self.assertFalse(ReviewMeeting.objects.exists())
            self.schedule(review)
            provider.reset_mock()
            self.assertEqual(self.login(self.pm).get(self.link(review)).status_code, 405)
            self.login(self.ann).post(self.link(review))  # the person reviewed cannot
            provider.assert_not_called()

    def test_a_completed_review_cannot_get_a_new_link(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value=OK):
            self.schedule(review)
        review.status = ReviewRequestStatus.COMPLETED
        review.save()
        with patch("vertex.meetings._via_google", return_value=OK) as provider:
            self.login(self.pm).post(self.link(review))
        provider.assert_not_called()

    def test_only_those_who_may_schedule_see_the_buttons(self):
        review = self.make_review()
        with patch("vertex.meetings._via_google", return_value=OK):
            self.schedule(review)
        self.assertNotContains(self.login(self.ann).get(review.get_absolute_url()), "Update link")
        self.assertContains(self.login(self.ann).get(review.get_absolute_url()), "Join")
