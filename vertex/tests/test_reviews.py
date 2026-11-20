"""
vertex/tests/test_reviews.py

Review requests: access follows Groups and Permissions, scoping and the workflow follow
ArielVertex ReviewsController.
"""

from django.urls import reverse
from notifications.models import Notification

from vertex.models import (
    CodeReview,
    ProjectReview,
    ProjectRole,
    ReviewMeeting,
    ReviewRequest,
    ReviewRequestStatus,
)
from vertex.tests.test_projects import (
    HX,
    VertexTestBase,
    grant,
    join,
    make_employee,
    make_project,
)


def make_review(project, subject, requested_by=None, assigned_to=None, **extra):
    return ReviewRequest.objects.create(
        project=project, subject=subject, requested_by=requested_by,
        assigned_to=assigned_to, **extra,
    )


class ReviewAccessTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.hr = make_employee("Hr", "hr@example.com", "9100000001")
        self.pm = make_employee("Pm", "pm@example.com", "9100000002")
        self.dev = make_employee("Dev", "dev@example.com", "9100000003")
        self.other = make_employee("Other", "other@example.com", "9100000004")
        self.project = make_project("REV")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev)
        self.review = make_review(self.project, self.dev, self.hr, self.pm)
        self.unrelated = make_review(self.project, self.other, self.hr, None)

    def test_no_group_sees_nothing(self):
        client = self.login(self.dev)
        self.assertNotEqual(client.get(reverse("vertex-reviews")).status_code, 200)
        self.assertNotEqual(client.get(self.review.get_absolute_url()).status_code, 200)

    def test_admin_needs_no_permission(self):
        user = self.hr.employee_user_id
        user.is_superuser = True
        user.save()
        client = self.login(self.hr)
        self.assertEqual(client.get(reverse("vertex-reviews")).status_code, 200)
        self.assertEqual(client.get(reverse("vertex-review-new"), **HX).status_code, 200)
        self.assertEqual(client.get(self.review.get_absolute_url()).status_code, 200)

    def test_subject_reviewer_and_requester_see_only_their_own(self):
        for person in (self.dev, self.pm, self.hr, self.other):
            grant(person, "view_reviewrequest")
        # the subject sees the review about them, not the one about someone else
        page = self.login(self.dev).get(reverse("vertex-review-list"), **HX)
        self.assertContains(page, self.dev.get_full_name())
        self.assertNotContains(page, self.other.get_full_name())
        self.assertEqual(self.login(self.dev).get(self.unrelated.get_absolute_url()).status_code, 404)
        # the assigned reviewer sees theirs
        self.assertEqual(self.login(self.pm).get(self.review.get_absolute_url()).status_code, 200)
        self.assertEqual(self.login(self.pm).get(self.unrelated.get_absolute_url()).status_code, 404)

    def test_requesters_and_all_switch_see_every_review(self):
        grant(self.hr, "view_reviewrequest", "add_reviewrequest")
        grant(self.other, "view_reviewrequest", "view_allreviewrequests")
        for person in (self.hr, self.other):
            client = self.login(person)
            self.assertEqual(client.get(self.review.get_absolute_url()).status_code, 200)
            self.assertEqual(client.get(self.unrelated.get_absolute_url()).status_code, 200)


class ReviewCreateTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.hr = make_employee("Hr", "hr@example.com", "9100000011")
        self.pm = make_employee("Pm", "pm@example.com", "9100000012")
        self.a = make_employee("Ann", "ann@example.com", "9100000013")
        self.b = make_employee("Bob", "bob@example.com", "9100000014")
        self.project = make_project("NEWREV")
        grant(self.hr, "view_reviewrequest", "add_reviewrequest")

    def data(self, **extra):
        data = {
            "project": self.project.pk, "subjects": [self.a.pk, self.b.pk],
            "review_type": "project_review", "assigned_to": self.pm.pk, "notes": "please",
        }
        data.update(extra)
        return data

    def test_needs_add_reviewrequest(self):
        grant(self.a, "view_reviewrequest")
        self.login(self.a).post(reverse("vertex-review-new"), self.data(), **HX)
        self.assertFalse(ReviewRequest.objects.exists())

    def test_one_open_request_per_employee(self):
        self.login(self.hr).post(reverse("vertex-review-new"), self.data(), **HX)
        reviews = ReviewRequest.objects.all()
        self.assertEqual(reviews.count(), 2)
        self.assertEqual({r.status for r in reviews}, {ReviewRequestStatus.OPEN})
        self.assertFalse(ReviewMeeting.objects.exists())
        self.assertEqual({r.requested_by for r in reviews}, {self.hr})

    def test_meeting_time_schedules_all_and_invites_everyone(self):
        self.login(self.hr).post(
            reverse("vertex-review-new"),
            self.data(
                title="Q1 review", scheduled_at="2026-12-01T10:00", duration_minutes=45,
                extra_attendees="Guest@Example.com, ann@example.com",
            ),
            **HX,
        )
        reviews = ReviewRequest.objects.all()
        self.assertEqual({r.status for r in reviews}, {ReviewRequestStatus.SCHEDULED})
        meeting = ReviewMeeting.objects.first()
        self.assertEqual(meeting.duration_minutes, 45)
        invited = set(meeting.attendees.split(", "))
        self.assertEqual(
            invited,
            {"ann@example.com", "bob@example.com", "pm@example.com", "hr@example.com", "guest@example.com"},
        )
        self.assertEqual(ReviewMeeting.objects.count(), 2)

    def test_meeting_time_needs_a_title(self):
        self.login(self.hr).post(
            reverse("vertex-review-new"), self.data(scheduled_at="2026-12-01T10:00"), **HX
        )
        self.assertFalse(ReviewRequest.objects.exists())

    def test_subjects_and_reviewer_are_notified(self):
        self.login(self.hr).post(reverse("vertex-review-new"), self.data(), **HX)
        for person in (self.a, self.b, self.pm):
            self.assertEqual(
                Notification.objects.filter(recipient=person.employee_user_id).count(), 1, person
            )
        self.assertEqual(Notification.objects.filter(recipient=self.hr.employee_user_id).count(), 0)

    def test_at_least_one_employee_is_required(self):
        self.login(self.hr).post(reverse("vertex-review-new"), self.data(subjects=[]), **HX)
        self.assertFalse(ReviewRequest.objects.exists())


class ReviewWorkflowTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.hr = make_employee("Hr", "hr@example.com", "9100000021")
        self.pm = make_employee("Pm", "pm@example.com", "9100000022")
        self.dev = make_employee("Dev", "dev@example.com", "9100000023")
        self.other = make_employee("Other", "other@example.com", "9100000024")
        self.project = make_project("FLOW")
        join(self.project, self.pm, ProjectRole.PROJECT_MANAGER)
        join(self.project, self.dev)
        join(self.project, self.other, ProjectRole.PROJECT_MANAGER)
        self.review = make_review(self.project, self.dev, self.hr, self.pm)
        self.schedule = {
            "title": "Review", "scheduled_at": "2026-12-02T09:30", "duration_minutes": 30,
            "assigned_to": self.pm.pk, "extra_attendees": "x@example.com",
        }
        for person in (self.hr, self.pm, self.dev, self.other):
            grant(person, "view_reviewrequest")

    def post(self, person, name, data=None):
        return self.login(person).post(reverse(name, args=[self.review.pk]), data or {})

    def test_schedule_needs_permission_and_to_manage_the_project(self):
        grant(self.pm, "change_reviewrequest")  # PM, but missing change_project
        self.post(self.pm, "vertex-review-schedule", self.schedule)
        self.assertFalse(ReviewMeeting.objects.exists())
        grant(self.pm, "change_project")
        self.post(self.pm, "vertex-review-schedule", self.schedule)
        meeting = ReviewMeeting.objects.get()
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, ReviewRequestStatus.SCHEDULED)
        self.assertIn("dev@example.com", meeting.attendees)
        self.assertIn("x@example.com", meeting.attendees)

    def test_a_developer_cannot_schedule(self):
        grant(self.dev, "change_reviewrequest", "change_project")
        self.post(self.dev, "vertex-review-schedule", self.schedule)
        self.assertFalse(ReviewMeeting.objects.exists())

    def test_rescheduling_updates_the_same_meeting(self):
        grant(self.pm, "change_reviewrequest", "change_project")
        self.post(self.pm, "vertex-review-schedule", self.schedule)
        self.post(self.pm, "vertex-review-schedule", {**self.schedule, "title": "Moved"})
        self.assertEqual(ReviewMeeting.objects.count(), 1)
        self.assertEqual(ReviewMeeting.objects.get().title, "Moved")

    def test_only_the_subject_can_respond(self):
        ReviewMeeting.objects.create(
            review_request=self.review, title="R", scheduled_at="2026-12-02T09:30Z", scheduled_by=self.pm
        )
        self.post(self.pm, "vertex-review-respond", {"response": "accepted"})
        self.assertEqual(ReviewMeeting.objects.get().response_status, "no_response")
        self.post(self.dev, "vertex-review-respond", {"response": "accepted"})
        meeting = ReviewMeeting.objects.get()
        self.assertEqual(meeting.response_status, "accepted")
        self.assertIsNotNone(meeting.responded_at)
        # the requester is told
        self.assertEqual(Notification.objects.filter(recipient=self.hr.employee_user_id).count(), 1)

    def test_invalid_response_and_unscheduled_review_are_refused(self):
        self.post(self.dev, "vertex-review-respond", {"response": "accepted"})  # no meeting yet
        ReviewMeeting.objects.create(
            review_request=self.review, title="R", scheduled_at="2026-12-02T09:30Z", scheduled_by=self.pm
        )
        self.post(self.dev, "vertex-review-respond", {"response": "banana"})
        self.assertEqual(ReviewMeeting.objects.get().response_status, "no_response")

    def test_writes_by_get_are_refused(self):
        grant(self.pm, "change_reviewrequest", "change_project", "add_projectreview")
        client = self.login(self.pm)
        for name in ("vertex-review-schedule", "vertex-review-respond", "vertex-review-submit"):
            self.assertEqual(client.get(reverse(name, args=[self.review.pk])).status_code, 405, name)

    PROJECT_OUTCOME = {
        "delivery_rating": 4, "ownership_rating": 5, "collaboration_rating": 3,
        "strengths": "fast", "observations": "ok", "action_items": "mentor",
    }

    def test_assigned_reviewer_submits_and_completes_the_review(self):
        grant(self.pm, "add_projectreview")
        self.post(self.pm, "vertex-review-submit", self.PROJECT_OUTCOME)
        outcome = ProjectReview.objects.get()
        self.assertEqual((outcome.delivery_rating, outcome.reviewer), (4, self.pm))
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, ReviewRequestStatus.COMPLETED)
        self.assertIsNotNone(self.review.closed_at)
        self.assertEqual(Notification.objects.filter(recipient=self.hr.employee_user_id).count(), 1)

    def test_resubmitting_updates_the_same_outcome(self):
        grant(self.pm, "add_projectreview")
        self.post(self.pm, "vertex-review-submit", self.PROJECT_OUTCOME)
        self.post(self.pm, "vertex-review-submit", {**self.PROJECT_OUTCOME, "delivery_rating": 2})
        self.assertEqual(ProjectReview.objects.count(), 1)
        self.assertEqual(ProjectReview.objects.get().delivery_rating, 2)

    def test_someone_else_cannot_submit(self):
        grant(self.other, "add_projectreview")  # not the assigned reviewer
        self.post(self.other, "vertex-review-submit", self.PROJECT_OUTCOME)
        self.assertFalse(ProjectReview.objects.exists())

    def test_reviewer_without_the_permission_cannot_submit(self):
        self.post(self.pm, "vertex-review-submit", self.PROJECT_OUTCOME)
        self.assertFalse(ProjectReview.objects.exists())

    def test_see_all_can_submit_for_any_review(self):
        grant(self.hr, "add_projectreview", "view_allreviewrequests")
        self.post(self.hr, "vertex-review-submit", self.PROJECT_OUTCOME)
        self.assertEqual(ProjectReview.objects.count(), 1)

    def test_ratings_must_be_between_1_and_5(self):
        grant(self.pm, "add_projectreview")
        for bad in (0, 6, -1):
            self.post(self.pm, "vertex-review-submit", {**self.PROJECT_OUTCOME, "delivery_rating": bad})
        self.assertFalse(ProjectReview.objects.exists())

    def test_code_review_type_uses_the_code_review_form(self):
        ReviewRequest.objects.filter(pk=self.review.pk).update(review_type="code_review")
        grant(self.pm, "add_codereview")
        self.post(
            self.pm, "vertex-review-submit",
            {"code_quality_rating": 5, "architecture_rating": 4, "testing_rating": 2,
             "blockers": "no tests"},
        )
        outcome = CodeReview.objects.get()
        self.assertEqual((outcome.testing_rating, outcome.blockers), (2, "no tests"))
        self.assertFalse(ProjectReview.objects.exists())

    def test_the_wrong_permission_for_the_type_is_refused(self):
        grant(self.pm, "add_projectreview")  # review is code_review below
        ReviewRequest.objects.filter(pk=self.review.pk).update(review_type="code_review")
        self.post(self.pm, "vertex-review-submit", {"code_quality_rating": 5, "architecture_rating": 4, "testing_rating": 2})
        self.assertFalse(CodeReview.objects.exists())
