"""
vertex/tests/test_roles.py

Every Vertex screen and action as each kind of user sees it. Users and groups come from
vertex/roles.py: HR Manager, Project Manager, Technical Lead, two Developers, Business Person,
Leadership (read only) and a user with no group.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from horilla.horilla_middlewares import _thread_locals, set_selected_company
from vertex import performance
from vertex.models import (
    CodeReview,
    Feedback,
    FeedbackCategoryScore,
    FeedbackStatus,
    MeetingMinute,
    PerformanceReport,
    Pip,
    Priority,
    Project,
    ProjectCall,
    ProjectDocument,
    ProjectMember,
    ResourceRequest,
    ReviewRequest,
    StatusUpdate,
    Visibility,
)
from vertex.roles import DEMO_USERS, ROLES, build_groups
from vertex.tests.test_projects import HX, VertexTestBase, make_employee

DENIED = "You Dont Have Permission"
ALL = {"hr", "pm", "lead", "dev", "dev2", "biz", "boss", "none"}
SHORT = {"vx.hr": "hr", "vx.pm": "pm", "vx.lead": "lead", "vx.dev": "dev", "vx.dev2": "dev2",
         "vx.biz": "biz", "vx.boss": "boss", "vx.none": "none"}


class RoleMatrixBase(VertexTestBase):
    @classmethod
    def setUpTestData(cls):
        # a previous test's request leaks its user into signals that run while this data is created
        _thread_locals.request = None
        set_selected_company(None)
        groups = build_groups()
        cls.people = {}
        for number, (username, first, last, group, role) in enumerate(DEMO_USERS):
            employee = make_employee(first, f"{username}@example.com", f"95000000{number:02d}")
            if group:
                employee.employee_user_id.groups.add(groups[group])
            cls.people[SHORT[username]] = employee
        cls.project = Project.objects.create(code="DEMO", name="Demo", start_date=date(2026, 1, 1))
        cls.other = Project.objects.create(code="OTHER", name="Other", start_date=date(2026, 1, 1))
        for username, first, last, group, role in DEMO_USERS:
            if role:
                ProjectMember.objects.create(
                    project=cls.project, employee=cls.people[SHORT[username]], role_on_project=role,
                    start_date=date(2026, 1, 1), allocation_pct=50,
                )
        ProjectMember.objects.create(
            project=cls.other, employee=cls.people["pm"], role_on_project="project_manager",
            start_date=date(2026, 1, 1),
        )

    def setUp(self):
        super().setUp()
        self.clients = {}

    def client_for(self, who):
        if who not in self.clients:
            self.clients[who] = self.login(self.people[who])
        return self.clients[who]

    def get(self, who, url, hx=False):
        return self.client_for(who).get(url, **(HX if hx else {}))

    def can_open(self, who, url, hx=False, marker=None):
        """True when the user really gets the screen (not a redirect, 404 or the no-permission box)."""
        response = self.get(who, url, hx)
        if response.status_code != 200:
            return False
        body = response.content.decode()
        if DENIED in body:
            return False
        return marker in body if marker else True

    def check_matrix(self, name, url, allowed, hx=False, marker=None):
        for who in sorted(ALL):
            with self.subTest(screen=name, user=who):
                self.assertEqual(
                    self.can_open(who, url, hx, marker), who in allowed,
                    f"{name} as {who}: expected {'allowed' if who in allowed else 'denied'}",
                )


class ScreenAccessTests(RoleMatrixBase):
    """Which roles can open which screens, one row per screen."""

    def test_projects(self):
        everyone_with_view = ALL - {"none"}
        self.check_matrix("project list", reverse("vertex-project-list"), everyone_with_view, hx=True)
        self.check_matrix("project page", self.project.get_absolute_url(), everyone_with_view)
        # members only: HR and leadership see every project, the others only their own
        self.check_matrix("other project", self.other.get_absolute_url(), {"hr", "boss", "pm"})
        self.check_matrix("new project", reverse("vertex-project-new"), {"hr"}, hx=True, marker="vm__head")
        self.check_matrix(
            "edit project", reverse("vertex-project-edit", args=[self.project.pk]), {"hr", "pm"},
            hx=True, marker="vm__head",
        )
        self.check_matrix(
            "edit other project", reverse("vertex-project-edit", args=[self.other.pk]), {"hr", "pm"},
            hx=True, marker="vm__head",
        )

    def test_workspace_modals(self):
        pk = self.project.pk
        self.check_matrix("add member", reverse("vertex-member-add", args=[pk]), {"hr", "pm"}, hx=True, marker="vm__head")
        self.check_matrix(
            "add document", reverse("vertex-document-add", args=[pk]), {"hr", "pm", "lead", "dev", "dev2"},
            hx=True, marker="vm__head",
        )
        self.check_matrix("schedule call", reverse("vertex-call-add", args=[pk]), {"hr", "pm"}, hx=True, marker="vm__head")

    def test_status_updates_hiring_and_resources(self):
        self.check_matrix("status list", reverse("vertex-status-list"), ALL - {"none"}, hx=True)
        self.check_matrix(
            "new status update", reverse("vertex-status-new"), {"pm", "lead", "dev", "dev2"}, hx=True, marker="vm__head"
        )
        self.check_matrix("hiring list", reverse("vertex-hiring-list"), {"hr", "pm"}, hx=True)
        self.check_matrix("raise request", reverse("vertex-hiring-new"), {"hr", "pm"}, hx=True, marker="vm__head")
        self.check_matrix("resource visibility", reverse("vertex-resources"), {"hr", "pm", "boss"})

    def test_reviews_and_minutes(self):
        self.check_matrix("review list", reverse("vertex-review-list"), {"hr", "pm", "lead", "dev", "dev2", "boss"}, hx=True)
        self.check_matrix("request review", reverse("vertex-review-new"), {"hr"}, hx=True, marker="vm__head")
        self.check_matrix("minutes list", reverse("vertex-minutes-list"), {"hr", "pm"}, hx=True)
        self.check_matrix("new minutes", reverse("vertex-minute-new"), {"hr", "pm"}, hx=True, marker="vm__head")

    def test_performance(self):
        self.check_matrix("feedback list", reverse("vertex-feedback-list"), {"hr", "pm", "lead"}, hx=True)
        self.check_matrix("new feedback", reverse("vertex-feedback-new"), {"hr", "pm", "lead"}, hx=True, marker="vm__head")
        self.check_matrix("report list", reverse("vertex-performance-list"), {"hr"}, hx=True)
        self.check_matrix("generate report", reverse("vertex-performance-generate"), {"hr"}, hx=True, marker="vm__head")
        self.check_matrix("pip list", reverse("vertex-pip-list"), {"hr"}, hx=True)
        self.check_matrix("new pip", reverse("vertex-pip-new"), {"hr"}, hx=True, marker="vm__head")
        self.check_matrix("my performance", reverse("vertex-my-performance"), {"hr", "pm", "lead", "dev", "dev2"})

    def test_the_sidebar_shows_each_person_only_their_own_screens(self):
        wanted = {
            "hr": {"vertex-projects", "vertex-status", "vertex-hiring", "vertex-resources", "vertex-reviews",
                   "vertex-minutes", "vertex-feedback", "vertex-performance", "vertex-pip", "vertex-my-performance"},
            "dev": {"vertex-projects", "vertex-status", "vertex-reviews", "vertex-my-performance"},
            "biz": {"vertex-projects", "vertex-status"},
            "none": set(),
        }
        every = {"vertex-projects", "vertex-status", "vertex-hiring", "vertex-resources", "vertex-reviews",
                 "vertex-minutes", "vertex-feedback", "vertex-performance", "vertex-pip", "vertex-my-performance"}
        for who, shown in wanted.items():
            page = self.get(who, reverse("vertex-projects") if who != "none" else "/")
            html = page.content.decode() if page.status_code == 200 else self.get(who, "/").content.decode()
            for name in every:
                with self.subTest(user=who, menu=name):
                    present = reverse(name) in html
                    self.assertEqual(present, name in shown, f"{who} / {name}")


class WorkflowAsEachUserTests(RoleMatrixBase):
    """Real work done by the people who would do it, and what the others then see."""

    def post(self, who, url, data=None, hx=False):
        return self.client_for(who).post(url, data or {}, **(HX if hx else {}))

    # ---- projects workspace ----

    def test_pm_manages_the_team_documents_and_calls(self):
        pk = self.project.pk
        extra = make_employee("Extra", "extra@example.com", "9599999901")
        self.post("pm", reverse("vertex-member-add", args=[pk]),
                  {"employee": extra.pk, "role_on_project": "developer", "allocation_pct": 40, "start_date": "2026-01-01"}, hx=True)
        self.assertTrue(ProjectMember.objects.filter(project=self.project, employee=extra, is_active=True).exists())
        self.post("pm", reverse("vertex-call-add", args=[pk]),
                  {"title": "Kickoff", "call_type": "customer", "scheduled_at": "2026-12-01T10:00", "duration_minutes": 30}, hx=True)
        self.assertEqual(ProjectCall.objects.filter(project=self.project, title="Kickoff").count(), 1)
        # the developer can open the team tab but has no remove buttons and cannot add people
        page = self.get("dev", f"{self.project.get_absolute_url()}?tab=team")
        self.assertNotContains(page, "Add member")
        self.assertNotContains(page, "/remove/")
        self.post("dev", reverse("vertex-member-add", args=[pk]),
                  {"employee": extra.pk, "role_on_project": "developer", "allocation_pct": 1, "start_date": "2026-01-01"}, hx=True)
        self.assertEqual(ProjectMember.objects.filter(project=self.project, employee=extra).count(), 1)
        # nor can a developer schedule calls
        self.post("dev", reverse("vertex-call-add", args=[pk]),
                  {"title": "Nope", "call_type": "customer", "scheduled_at": "2026-12-01T10:00", "duration_minutes": 30}, hx=True)
        self.assertFalse(ProjectCall.objects.filter(title="Nope").exists())

    def test_the_pm_cannot_manage_a_project_they_do_not_lead_but_hr_can(self):
        mine = ProjectMember.objects.get(project=self.other, employee=self.people["pm"])
        mine.role_on_project = "developer"
        mine.save()
        url = reverse("vertex-project-edit", args=[self.other.pk])
        self.assertFalse(self.can_open("pm", url, hx=True, marker="vm__head"))
        self.assertTrue(self.can_open("hr", url, hx=True, marker="vm__head"))

    def test_business_person_never_sees_internal_documents_or_internal_status_detail(self):
        ProjectDocument.objects.create(project=self.project, title="Secret spec", visibility=Visibility.INTERNAL,
                                       is_video_link=True, video_url="https://example.com/a")
        ProjectDocument.objects.create(project=self.project, title="Client deck", visibility=Visibility.BUSINESS_VISIBLE,
                                       is_video_link=True, video_url="https://example.com/b")
        StatusUpdate.objects.create(
            project=self.project, employee=self.people["dev"], update_date=date.today(), work_completed="Built login",
            blockers="Waiting on the API", internal_note="Dev is overloaded", client_shareable_summary="Login is done",
        )
        docs = f"{self.project.get_absolute_url()}?tab=documents"
        self.assertContains(self.get("biz", docs), "Client deck")
        self.assertNotContains(self.get("biz", docs), "Secret spec")
        self.assertContains(self.get("pm", docs), "Secret spec")
        status = f"{self.project.get_absolute_url()}?tab=status"
        biz = self.get("biz", status)
        self.assertContains(biz, "Login is done")
        for hidden in ("Built login", "Waiting on the API", "Dev is overloaded"):
            self.assertNotContains(biz, hidden)
        pm = self.get("pm", status)
        for shown in ("Built login", "Waiting on the API", "Dev is overloaded", "Consolidated summary"):
            self.assertContains(pm, shown)
        lead = self.get("lead", status)
        self.assertContains(lead, "Built login")
        self.assertNotContains(lead, "Consolidated summary")  # only managers see the roll-up

    def test_every_member_can_post_a_business_comment(self):
        url = reverse("vertex-comment-add", args=[self.project.pk])
        for who in ("pm", "dev", "biz", "boss", "hr"):
            self.post(who, url, {"message": f"hello from {who}"})
        self.assertEqual(self.project.comments.count(), 5)
        self.post("none", url, {"message": "ghost"})
        self.assertFalse(self.project.comments.filter(message="ghost").exists())

    # ---- status updates ----

    def test_developers_submit_only_for_their_projects_and_see_only_their_own_list(self):
        self.post("dev", reverse("vertex-status-new"),
                  {"project": self.project.pk, "update_date": date.today().isoformat(), "hours_spent": 6,
                   "status": "on_track", "work_completed": "Fixed bugs"}, hx=True)
        self.assertEqual(StatusUpdate.objects.get().employee, self.people["dev"])
        self.post("dev", reverse("vertex-status-new"),
                  {"project": self.other.pk, "update_date": date.today().isoformat(), "hours_spent": 6,
                   "status": "on_track", "work_completed": "Not my project"}, hx=True)
        self.assertEqual(StatusUpdate.objects.count(), 1)
        self.assertContains(self.get("dev", reverse("vertex-status-list"), hx=True), "Fixed bugs")
        self.assertNotContains(self.get("dev2", reverse("vertex-status-list"), hx=True), "Fixed bugs")
        self.post("biz", reverse("vertex-status-new"),
                  {"project": self.project.pk, "update_date": date.today().isoformat(), "hours_spent": 1,
                   "status": "on_track", "work_completed": "Business typed this"}, hx=True)
        self.assertEqual(StatusUpdate.objects.count(), 1)

    # ---- hiring ----

    def test_hiring_request_flow_between_pm_and_hr(self):
        self.post("pm", reverse("vertex-hiring-new"),
                  {"project": self.project.pk, "role_title": "QA", "count": 2, "priority": Priority.MEDIUM.value,
                   "skills": "selenium", "reason": "Need testers"}, hx=True)
        request = ResourceRequest.objects.get()
        url = request.get_absolute_url()
        self.assertEqual(self.get("pm", url).status_code, 200)
        self.assertEqual(self.get("hr", url).status_code, 200)
        self.assertNotEqual(self.get("dev", url).status_code, 200)
        # only HR changes the status; the PM can comment
        self.post("pm", reverse("vertex-resource-request-status", args=[request.pk]), {"status": "approved"})
        request.refresh_from_db()
        self.assertEqual(request.status, "open")
        self.post("hr", reverse("vertex-resource-request-status", args=[request.pk]), {"status": "approved", "comment": "ok"})
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")
        self.post("pm", reverse("vertex-resource-request-comment", args=[request.pk]), {"message": "thanks"})
        self.assertEqual(request.comments.filter(message="thanks").count(), 1)

    # ---- reviews ----

    def test_review_flow_across_hr_pm_lead_and_the_developer(self):
        self.post("hr", reverse("vertex-review-new"),
                  {"project": self.project.pk, "subjects": [self.people["dev"].pk], "review_type": "project_review",
                   "assigned_to": self.people["lead"].pk, "notes": "Q4"}, hx=True)
        review = ReviewRequest.objects.get()
        url = review.get_absolute_url()
        for who, expected in (("hr", 200), ("lead", 200), ("dev", 200), ("boss", 200), ("pm", 404), ("dev2", 404)):
            self.assertEqual(self.get(who, url).status_code, expected, who)
        self.assertEqual(self.get("biz", url).status_code in (302, 403, 404), True)
        # the reviewed person answers the invitation once it is scheduled; nobody else may
        self.post("hr", reverse("vertex-review-schedule", args=[review.pk]),
                  {"title": "Review", "scheduled_at": "2026-12-01T10:00", "duration_minutes": 30})
        review.refresh_from_db()
        self.assertEqual(review.status, "scheduled")
        from vertex.models import ReviewMeeting

        self.post("lead", reverse("vertex-review-respond", args=[review.pk]), {"response": "accepted"})
        self.assertEqual(ReviewMeeting.objects.get(review_request=review).response_status, "no_response")
        self.post("dev", reverse("vertex-review-respond", args=[review.pk]), {"response": "accepted"})
        self.assertEqual(ReviewMeeting.objects.get(review_request=review).response_status, "accepted")
        # only the assigned reviewer (or HR) submits the outcome
        outcome = {"delivery_rating": 4, "ownership_rating": 4, "collaboration_rating": 4}
        self.post("dev", reverse("vertex-review-submit", args=[review.pk]), outcome)
        self.post("dev2", reverse("vertex-review-submit", args=[review.pk]), outcome)
        review.refresh_from_db()
        self.assertNotEqual(review.status, "completed")
        self.post("lead", reverse("vertex-review-submit", args=[review.pk]), outcome)
        review.refresh_from_db()
        self.assertEqual(review.status, "completed")
        # a code review needs the code review permission, which the PM does not have
        code = ReviewRequest.objects.create(project=self.project, subject=self.people["dev"], review_type="code_review",
                                            assigned_to=self.people["pm"], requested_by=self.people["hr"])
        self.post("pm", reverse("vertex-review-submit", args=[code.pk]),
                  {"code_quality_rating": 5, "architecture_rating": 5, "testing_rating": 5})
        code.refresh_from_db()
        self.assertNotEqual(code.status, "completed")

    # ---- minutes ----

    def test_minutes_belong_to_their_author_even_for_hr(self):
        self.post("pm", reverse("vertex-minute-new"),
                  {"title": "PM sync", "meeting_date": "2026-12-01T10:00", "raw_notes": "we agreed to ship"}, hx=True)
        minute = MeetingMinute.objects.get()
        self.assertEqual(self.get("pm", minute.get_absolute_url()).status_code, 200)
        self.assertEqual(self.get("hr", minute.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.get("hr", reverse("vertex-minutes-list"), hx=True), "PM sync")
        self.assertNotEqual(self.get("dev", minute.get_absolute_url()).status_code, 200)

    # ---- performance ----

    def scores(self, value=80):
        return {f"score_{c.key}": value for c in performance.CATEGORIES}

    def feedback_data(self, subject, **extra):
        data = {"subject": subject.pk, "period": "2026-Q4", "constructive_summary": "Good quarter",
                "internal_notes": "Confidential words", **self.scores()}
        data.update(extra)
        return data

    def test_feedback_from_lead_to_employee_through_hr(self):
        dev = self.people["dev"]
        self.post("lead", reverse("vertex-feedback-new"), self.feedback_data(dev, submit_now="on"), hx=True)
        feedback = Feedback.objects.get()
        self.assertEqual(feedback.status, FeedbackStatus.SUBMITTED)
        # lead and PM write feedback independently; each sees only their own, HR sees all
        self.post("pm", reverse("vertex-feedback-new"), self.feedback_data(dev, constructive_summary="PM view"), hx=True)
        pm_feedback = Feedback.objects.get(author=self.people["pm"])
        lead_list = self.get("lead", reverse("vertex-feedback-list"), hx=True)
        self.assertContains(lead_list, feedback.get_absolute_url())
        self.assertNotContains(lead_list, pm_feedback.get_absolute_url())
        self.assertEqual(self.get("pm", feedback.get_absolute_url()).status_code, 404)
        self.assertEqual(self.get("hr", feedback.get_absolute_url()).status_code, 200)
        self.assertEqual(Feedback.objects.count(), 2)
        # the subject sees nothing until it is approved and published
        self.assertNotContains(self.get("dev", reverse("vertex-my-performance")), "Good quarter")
        self.post("lead", reverse("vertex-feedback-approve", args=[feedback.pk]))  # the author cannot approve
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.SUBMITTED)
        self.post("hr", reverse("vertex-feedback-approve", args=[feedback.pk]))
        self.assertNotContains(self.get("dev", reverse("vertex-my-performance")), "Good quarter")
        self.post("hr", reverse("vertex-feedback-publish", args=[feedback.pk]))
        mine = self.get("dev", reverse("vertex-my-performance"))
        self.assertContains(mine, "Good quarter")
        self.assertNotContains(mine, "Confidential words")
        self.assertNotContains(self.get("dev2", reverse("vertex-my-performance")), "Good quarter")

    def test_report_is_a_draft_until_hr_publishes_and_only_its_owner_reads_it(self):
        dev = self.people["dev"]
        review = ReviewRequest.objects.create(project=self.project, subject=dev, review_type="code_review")
        CodeReview.objects.create(review_request=review, reviewer=self.people["lead"],
                                  code_quality_rating=4, architecture_rating=4, testing_rating=4)
        quarter = f"{date.today().year}-Q{(date.today().month - 1) // 3 + 1}"
        self.post("hr", reverse("vertex-performance-generate"),
                  {"subject": dev.pk, "period_type": "quarterly", "period": quarter}, hx=True)
        report = PerformanceReport.objects.get()
        self.assertEqual(report.category_scores.get(category="technical").score, 80)
        url = report.get_absolute_url()
        for who in ("dev", "dev2", "pm", "lead"):
            self.assertNotEqual(self.get(who, url).status_code, 200, who)
        self.post("pm", reverse("vertex-performance-publish", args=[report.pk]))  # not a publisher
        report.refresh_from_db()
        self.assertFalse(report.is_published)
        self.post("hr", reverse("vertex-performance-publish", args=[report.pk]))
        # employees read their own published result on My Performance (the report list is for HR)
        self.assertContains(self.get("dev", reverse("vertex-my-performance")), quarter)
        self.assertNotContains(self.get("dev2", reverse("vertex-my-performance")), quarter)

    def test_a_low_published_score_starts_a_plan_the_employee_can_read_but_not_edit(self):
        dev = self.people["dev"]
        report = performance.generate_report(dev, "quarterly", "2026-Q4")
        PerformanceReport.objects.filter(pk=report.pk).update(overall_score=Decimal("40.0"))
        self.post("hr", reverse("vertex-performance-publish", args=[report.pk]))
        pip = Pip.objects.get()
        self.assertTrue(pip.is_auto)
        mine = self.get("dev", reverse("vertex-my-performance"))
        self.assertContains(mine, "below the 50% threshold")
        self.assertFalse(self.can_open("dev", reverse("vertex-pip-list"), hx=True))
        self.assertFalse(self.can_open("dev", reverse("vertex-pip-edit", args=[pip.pk]), hx=True, marker="vm__head"))
        self.assertTrue(self.can_open("hr", reverse("vertex-pip-edit", args=[pip.pk]), hx=True, marker="vm__head"))
        self.assertEqual(Notification_count(dev), 2)  # published report + plan


def Notification_count(employee):
    from notifications.models import Notification

    return Notification.objects.filter(recipient=employee.employee_user_id).count()


class RoleDefinitionTests(TestCase):
    def test_every_role_uses_only_real_permissions(self):
        groups = build_groups()
        self.assertEqual(set(groups), set(ROLES))
        for name, group in groups.items():
            self.assertEqual(group.permissions.count(), len(set(ROLES[name])), name)

    def test_the_roles_are_separate_groups_ready_for_the_grid(self):
        from django.contrib.auth.models import Group

        build_groups()
        build_groups()  # running it again changes nothing
        self.assertEqual(Group.objects.filter(name__startswith="Vertex - ").count(), len(ROLES))
