"""
vertex/tests/test_performance.py

Feedback (draft -> submitted -> approved -> published), the weighted performance model, the report
generator and the automatic improvement plan, ported from ArielVertex.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from vertex import access, performance
from vertex.models import (
    CodeReview,
    Feedback,
    FeedbackCategoryScore,
    FeedbackStatus,
    PerformanceReport,
    Pip,
    PipStatus,
    ProjectReview,
    ReviewRequest,
    StatusUpdate,
    VertexSetting,
)
from vertex.tests.test_projects import (
    HX,
    VertexTestBase,
    grant,
    join,
    make_employee,
    make_project,
)

CATEGORY_KEYS = [c.key for c in performance.CATEGORIES]


def feedback_post(subject, **extra):
    """POST data for the feedback modal with every competency scored."""
    data = {
        "subject": subject.pk,
        "period": "2026-Q4",
        "constructive_summary": "Solid quarter with room to grow in estimation.",
        "strengths": "Debugging",
        "improvement_areas": "Estimation",
        "action_plan": "Pair on estimates",
        "internal_notes": "Confidential remark",
    }
    for key in CATEGORY_KEYS:
        data[f"score_{key}"] = 80
        data[f"comment_{key}"] = ""
    data.update(extra)
    return data


def make_feedback(author, subject, status=FeedbackStatus.DRAFT, scores=None, period="2026-Q4"):
    feedback = Feedback.objects.create(
        author=author, subject=subject, period=period, status=status,
        constructive_summary="Summary", internal_notes="Secret",
    )
    for key, score in (scores or {k: 80 for k in CATEGORY_KEYS}).items():
        FeedbackCategoryScore.objects.create(feedback=feedback, category=key, score=score)
    return feedback


class ScoringModelTests(SimpleTestCase):
    def test_default_weights_add_up_to_100(self):
        self.assertEqual(sum(c.weight for c in performance.CATEGORIES), 100)

    def test_equal_scores_give_that_score(self):
        scores = {key: 80 for key in CATEGORY_KEYS}
        self.assertEqual(performance.weighted(scores), Decimal("80.0"))

    def test_weights_apply(self):
        scores = {"technical": 100, "delivery": 0, "collaboration": 0, "client": 0, "accountability": 0, "availability": 0}
        self.assertEqual(performance.weighted(scores), Decimal("30.0"))

    def test_not_applicable_weight_is_shared_across_the_others(self):
        scores = {key: 60 for key in CATEGORY_KEYS if key != "client"}
        # without redistribution the missing 10% would drag a straight 60 down to 54
        self.assertEqual(performance.weighted(scores, {"client"}), Decimal("60.0"))
        weights = performance.effective_weights({"client"})
        self.assertNotIn("client", weights)
        self.assertAlmostEqual(sum(weights.values()), 100, delta=0.2)

    def test_rating_bands(self):
        from vertex.models import PerformanceRating as R

        for score, expected in (
            (100, R.OUTSTANDING), (90, R.OUTSTANDING), (89.9, R.EXCEEDS), (80, R.EXCEEDS),
            (79.9, R.MEETS), (70, R.MEETS), (69.9, R.NEEDS_IMPROVEMENT), (60, R.NEEDS_IMPROVEMENT),
            (59.9, R.ATTENTION), (0, R.ATTENTION),
        ):
            self.assertEqual(performance.rating_for(score), expected, score)

    def test_periods(self):
        from vertex.models import PerformancePeriodType as T

        self.assertEqual(performance.parse_period(T.MONTHLY, "2026-02"), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(performance.parse_period(T.QUARTERLY, "2026-Q4"), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(performance.normalise_period(T.QUARTERLY, "2026-q3"), "2026-Q3")
        for bad_type, bad in ((T.MONTHLY, "2026-13"), (T.MONTHLY, "2026-Q1"), (T.QUARTERLY, "2026-07"), (T.QUARTERLY, "x")):
            with self.assertRaises(ValueError, msg=bad):
                performance.parse_period(bad_type, bad)

    def test_score_colours_follow_the_bands(self):
        self.assertEqual(performance.score_colour(95), "#15803d")
        self.assertEqual(performance.score_colour(65), "#f59e0b")
        self.assertEqual(performance.score_colour(10), "#dc2626")
        self.assertEqual(performance.score_colour(None), "#9ca3af")


class ReportGenerationTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.person = make_employee("Pat", "pat@example.com", "9400000001")
        self.manager = make_employee("Mia", "mia@example.com", "9400000002")
        self.project = make_project("PERF")
        join(self.project, self.person)

    def generate(self, period="2026-Q4", kind="quarterly"):
        return performance.generate_report(self.person, kind, period)

    def scores(self, report):
        return {c.category: c for c in report.category_scores.all()}

    def test_no_evidence_uses_the_neutral_baseline_and_says_so(self):
        report = self.generate()
        scores = self.scores(report)
        self.assertEqual(scores["technical"].score, performance.NEUTRAL_SCORE)
        self.assertTrue(scores["client"].not_applicable)  # no client evidence: not penalised
        self.assertEqual(report.overall_score, Decimal("72.0"))
        self.assertIn("Limited evidence", report.data_sources)
        self.assertFalse(report.is_published)

    def test_code_review_drives_technical(self):
        review = ReviewRequest.objects.create(project=self.project, subject=self.person, review_type="code_review")
        CodeReview.objects.create(
            review_request=review, reviewer=self.manager,
            code_quality_rating=5, architecture_rating=5, testing_rating=5,
        )
        report = self.generate(period=f"{timezone.localdate().year}-Q{(timezone.localdate().month - 1) // 3 + 1}")
        self.assertEqual(self.scores(report)["technical"].score, 100)
        self.assertIn("Code review (1)", report.data_sources)

    def test_project_review_drives_delivery_ownership_and_collaboration(self):
        review = ReviewRequest.objects.create(project=self.project, subject=self.person)
        ProjectReview.objects.create(
            review_request=review, reviewer=self.manager,
            delivery_rating=4, ownership_rating=2, collaboration_rating=5,
        )
        today = timezone.localdate()
        report = self.generate(period=f"{today.year}-{today.month:02d}", kind="monthly")
        scores = self.scores(report)
        self.assertEqual(scores["delivery"].score, 80)
        self.assertEqual(scores["accountability"].score, 40)
        self.assertEqual(scores["collaboration"].score, 100)

    def test_only_approved_or_published_feedback_counts(self):
        make_feedback(self.manager, self.person, FeedbackStatus.DRAFT, {"technical": 10})
        make_feedback(self.manager, self.person, FeedbackStatus.SUBMITTED, {"technical": 10}, period="2026-Q4")
        self.assertEqual(self.scores(self.generate())["technical"].score, performance.NEUTRAL_SCORE)
        make_feedback(self.manager, self.person, FeedbackStatus.PUBLISHED, {"technical": 90, "client": 70})
        report = self.generate()
        scores = self.scores(report)
        self.assertEqual(scores["technical"].score, 90)
        self.assertFalse(scores["client"].not_applicable)  # real client feedback counts
        self.assertEqual(scores["client"].score, 70)
        self.assertIn("Feedback (1)", report.data_sources)

    def test_status_update_discipline_and_blockers(self):
        today = timezone.localdate()
        month = f"{today.year}-{today.month:02d}"
        for offset in range(3):
            StatusUpdate.objects.create(
                project=self.project, employee=self.person, update_date=today - timedelta(days=offset),
                work_completed="x", status="blocked" if offset == 0 else "on_track",
            )
        report = self.generate(period=month, kind="monthly")
        self.assertIn("Status updates", report.data_sources)
        accountability = self.scores(report)["accountability"].score
        self.assertLess(accountability, 100)  # a blocked update lowers accountability

    def test_regenerating_updates_the_same_report_and_returns_it_to_draft(self):
        report = self.generate()
        report.is_published = True
        report.approved_by = self.manager
        report.published_at = timezone.now()
        report.save()
        again = self.generate()
        self.assertEqual(again.pk, report.pk)
        self.assertEqual(PerformanceReport.objects.filter(subject=self.person).count(), 1)
        self.assertFalse(again.is_published)
        self.assertIsNone(again.approved_by)
        self.assertEqual(again.category_scores.count(), len(CATEGORY_KEYS))

    def test_category_rows_report_effective_weights(self):
        rows = {r["key"]: r for r in performance.category_rows(self.generate())}
        self.assertTrue(rows["client"]["not_applicable"])
        self.assertIsNone(rows["client"]["weight"])
        self.assertAlmostEqual(rows["technical"]["weight"], 33.3, delta=0.1)


class PipRuleTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.person = make_employee("Low", "low@example.com", "9400000011")
        self.hr = make_employee("Hr", "hr@example.com", "9400000012")
        grant(self.hr, "add_pip", "view_pip")

    def test_below_threshold_starts_a_pip_and_tells_hr_and_the_employee(self):
        pip = performance.evaluate_and_trigger_pip(self.person, 45.5, "2026-Q4", self.hr)
        self.assertIsNotNone(pip)
        self.assertTrue(pip.is_auto)
        self.assertEqual(pip.trigger_score, Decimal("45.5"))
        self.assertIn("45.5", pip.reason)
        self.assertEqual(pip.status, PipStatus.OPEN)
        self.assertEqual(Notification.objects.filter(recipient=self.person.employee_user_id).count(), 1)

    def test_at_or_above_threshold_does_nothing(self):
        self.assertIsNone(performance.evaluate_and_trigger_pip(self.person, 50, "2026-Q4", self.hr))
        self.assertFalse(Pip.objects.exists())

    def test_plans_do_not_stack(self):
        self.assertIsNotNone(performance.evaluate_and_trigger_pip(self.person, 30, "2026-Q3", self.hr))
        self.assertIsNone(performance.evaluate_and_trigger_pip(self.person, 20, "2026-Q4", self.hr))
        self.assertEqual(Pip.objects.count(), 1)
        Pip.objects.update(status=PipStatus.COMPLETED)
        self.assertIsNotNone(performance.evaluate_and_trigger_pip(self.person, 20, "2026-Q4", self.hr))

    def test_threshold_and_switch_are_configurable(self):
        VertexSetting.objects.create(key="pip.thresholdPercent", value="70")
        self.assertIsNotNone(performance.evaluate_and_trigger_pip(self.person, 65, "2026-Q4", self.hr))
        Pip.objects.all().delete()
        VertexSetting.objects.create(key="pip.enabled", value="false")
        self.assertIsNone(performance.evaluate_and_trigger_pip(self.person, 10, "2026-Q4", self.hr))


class FeedbackWorkflowTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.author = make_employee("Author", "author@example.com", "9400000021")
        self.subject = make_employee("Subject", "subject@example.com", "9400000022")
        self.hr = make_employee("Hr", "hr2@example.com", "9400000023")
        self.other = make_employee("Other", "other@example.com", "9400000024")
        grant(self.author, "view_feedback", "add_feedback", "change_feedback")
        grant(
            self.hr, "view_feedback", "add_feedback", "change_feedback", "change_feedbackapproval",
            "add_pip", "view_pip",
        )
        grant(self.subject, "view_myperformance")
        grant(self.other, "view_feedback", "add_feedback")
        self.author_client = self.login(self.author)
        self.hr_client = self.login(self.hr)

    def create(self, **extra):
        return self.author_client.post(reverse("vertex-feedback-new"), feedback_post(self.subject, **extra), **HX)

    # ---- creating ----

    def test_author_creates_a_draft_with_scores(self):
        self.create()
        feedback = Feedback.objects.get()
        self.assertEqual(feedback.author, self.author)
        self.assertEqual(feedback.status, FeedbackStatus.DRAFT)
        self.assertEqual(feedback.category_scores.count(), len(CATEGORY_KEYS))
        self.assertEqual(feedback.get_average_score(), 80)

    def test_submit_now_notifies_the_approvers(self):
        self.create(submit_now="on")
        self.assertEqual(Feedback.objects.get().status, FeedbackStatus.SUBMITTED)
        self.assertEqual(Notification.objects.filter(recipient=self.hr.employee_user_id).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.subject.employee_user_id).count(), 0)

    def test_nobody_can_write_feedback_about_themselves(self):
        self.author_client.post(reverse("vertex-feedback-new"), feedback_post(self.author), **HX)
        self.assertFalse(Feedback.objects.exists())

    def test_submitting_needs_every_applicable_score(self):
        data = feedback_post(self.subject, submit_now="on")
        data["score_technical"] = ""
        page = self.author_client.post(reverse("vertex-feedback-new"), data, **HX)
        self.assertFalse(Feedback.objects.exists())
        self.assertContains(page, "Score every applicable competency")
        # a draft may be incomplete
        data.pop("submit_now")
        self.author_client.post(reverse("vertex-feedback-new"), data, **HX)
        self.assertEqual(Feedback.objects.get().category_scores.count(), len(CATEGORY_KEYS) - 1)

    def test_client_competency_can_be_marked_not_applicable(self):
        self.create(score_client="", na_client="on", submit_now="on")
        client = Feedback.objects.get().category_scores.get(category="client")
        self.assertTrue(client.not_applicable)

    def test_scores_are_limited_to_0_100_and_the_period_is_checked(self):
        self.create(score_technical=101)
        self.create(period="next year")
        self.assertFalse(Feedback.objects.exists())

    def test_needs_the_add_permission(self):
        client = self.login(self.subject)
        client.post(reverse("vertex-feedback-new"), feedback_post(self.other), **HX)
        self.assertFalse(Feedback.objects.exists())

    # ---- who sees what ----

    def test_authors_see_only_their_own_and_hr_sees_everything(self):
        mine = make_feedback(self.author, self.subject)
        theirs = make_feedback(self.other, self.subject)
        mine_page = self.author_client.get(reverse("vertex-feedback-list"), **HX)
        self.assertContains(mine_page, mine.get_absolute_url())
        self.assertNotContains(mine_page, theirs.get_absolute_url())
        self.assertEqual(self.author_client.get(theirs.get_absolute_url()).status_code, 404)
        hr_page = self.hr_client.get(reverse("vertex-feedback-list"), **HX)
        self.assertContains(hr_page, theirs.get_absolute_url())
        self.assertEqual(self.hr_client.get(theirs.get_absolute_url()).status_code, 200)

    def test_the_subject_cannot_open_feedback_about_them(self):
        feedback = make_feedback(self.author, self.subject, FeedbackStatus.PUBLISHED)
        self.assertNotEqual(self.login(self.subject).get(feedback.get_absolute_url()).status_code, 200)

    # ---- editing ----

    def test_only_the_author_edits_and_only_while_draft_or_sent_back(self):
        feedback = make_feedback(self.author, self.subject)
        url = reverse("vertex-feedback-edit", args=[feedback.pk])
        self.assertContains(self.author_client.get(url, **HX), "vm__head")
        self.assertNotContains(self.hr_client.get(url, **HX), "vm__head")  # HR is not the author
        feedback.status = FeedbackStatus.SUBMITTED
        feedback.save()
        self.assertNotContains(self.author_client.get(url, **HX), "vm__head")
        feedback.status = FeedbackStatus.REVISION_REQUESTED
        feedback.save()
        data = feedback_post(self.subject, constructive_summary="Revised text")
        self.author_client.post(url, data, **HX)
        feedback.refresh_from_db()
        self.assertEqual(feedback.constructive_summary, "Revised text")

    # ---- approval workflow ----

    def act(self, client, name, feedback, **data):
        return client.post(reverse(name, args=[feedback.pk]), data)

    def test_full_flow_draft_to_published_hides_internal_notes_from_the_employee(self):
        feedback = make_feedback(self.author, self.subject)
        self.act(self.author_client, "vertex-feedback-submit", feedback)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.SUBMITTED)
        # nothing is employee visible yet
        page = self.login(self.subject).get(reverse("vertex-my-performance"))
        self.assertNotContains(page, "Summary")
        self.act(self.hr_client, "vertex-feedback-approve", feedback)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.APPROVED)
        self.assertEqual(feedback.approved_by, self.hr)
        self.act(self.hr_client, "vertex-feedback-publish", feedback)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.PUBLISHED)
        self.assertIsNotNone(feedback.published_at)
        self.assertEqual(Notification.objects.filter(recipient=self.subject.employee_user_id).count(), 1)
        page = self.login(self.subject).get(reverse("vertex-my-performance"))
        self.assertContains(page, "Summary")
        self.assertNotContains(page, "Secret")  # internal notes never reach the employee

    def test_submit_is_refused_without_all_scores(self):
        feedback = make_feedback(self.author, self.subject, scores={"technical": 80})
        self.act(self.author_client, "vertex-feedback-submit", feedback)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.DRAFT)

    def test_only_approvers_approve_or_publish(self):
        feedback = make_feedback(self.author, self.subject, FeedbackStatus.SUBMITTED)
        self.act(self.author_client, "vertex-feedback-approve", feedback)
        self.act(self.author_client, "vertex-feedback-publish", feedback)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.SUBMITTED)

    def test_cannot_publish_before_approving(self):
        feedback = make_feedback(self.author, self.subject, FeedbackStatus.SUBMITTED)
        self.act(self.hr_client, "vertex-feedback-publish", feedback)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.SUBMITTED)

    def test_request_revision_needs_a_reason_and_tells_the_author(self):
        feedback = make_feedback(self.author, self.subject, FeedbackStatus.SUBMITTED)
        self.act(self.hr_client, "vertex-feedback-revision", feedback, reason="")
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.SUBMITTED)
        self.act(self.hr_client, "vertex-feedback-revision", feedback, reason="Be more specific")
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, FeedbackStatus.REVISION_REQUESTED)
        self.assertEqual(feedback.revision_reason, "Be more specific")
        self.assertEqual(Notification.objects.filter(recipient=self.author.employee_user_id).count(), 1)
        # the author sees why, and resubmitting clears it
        page = self.author_client.get(feedback.get_absolute_url())
        self.assertContains(page, "Be more specific")
        self.act(self.author_client, "vertex-feedback-submit", feedback)
        feedback.refresh_from_db()
        self.assertEqual((feedback.status, feedback.revision_reason), (FeedbackStatus.SUBMITTED, ""))

    def test_workflow_actions_refuse_get(self):
        feedback = make_feedback(self.author, self.subject)
        for name in ("vertex-feedback-submit", "vertex-feedback-approve", "vertex-feedback-publish", "vertex-feedback-revision"):
            self.assertEqual(self.hr_client.get(reverse(name, args=[feedback.pk])).status_code, 405, name)

    def test_publishing_low_feedback_starts_an_improvement_plan(self):
        low = {key: 30 for key in CATEGORY_KEYS}
        feedback = make_feedback(self.author, self.subject, FeedbackStatus.APPROVED, low)
        self.act(self.hr_client, "vertex-feedback-publish", feedback)
        pip = Pip.objects.get()
        self.assertEqual(pip.subject, self.subject)
        self.assertTrue(pip.is_auto)
        self.assertEqual(pip.trigger_period, "2026-Q4")

    def test_publishing_healthy_feedback_starts_no_plan(self):
        feedback = make_feedback(self.author, self.subject, FeedbackStatus.APPROVED)
        self.act(self.hr_client, "vertex-feedback-publish", feedback)
        self.assertFalse(Pip.objects.exists())


class ReportPagesTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.hr = make_employee("Hr", "hr3@example.com", "9400000031")
        self.person = make_employee("Person", "person@example.com", "9400000032")
        self.peer = make_employee("Peer", "peer@example.com", "9400000033")
        grant(
            self.hr, "view_performancereport", "add_performancereport", "view_allperformancereports",
            "change_performancepublication", "view_pip", "add_pip", "change_pip",
        )
        grant(self.person, "view_performancereport", "view_myperformance")
        grant(self.peer, "view_performancereport")
        self.hr_client = self.login(self.hr)

    def generate(self, subject=None, **extra):
        data = {"subject": (subject or self.person).pk, "period_type": "quarterly", "period": "2026-Q4"}
        data.update(extra)
        return self.hr_client.post(reverse("vertex-performance-generate"), data, **HX)

    def test_generate_creates_a_draft_and_opens_it(self):
        page = self.generate()
        report = PerformanceReport.objects.get()
        self.assertFalse(report.is_published)
        self.assertContains(page, report.get_absolute_url())

    def test_period_is_validated_against_its_type(self):
        self.generate(period="2026-07")  # a month given for a quarterly report
        self.generate(period_type="monthly", period="2026-Q4")
        self.assertFalse(PerformanceReport.objects.exists())

    def test_generating_needs_the_add_permission(self):
        client = self.login(self.peer)
        client.post(
            reverse("vertex-performance-generate"),
            {"subject": self.person.pk, "period_type": "quarterly", "period": "2026-Q4"}, **HX,
        )
        self.assertFalse(PerformanceReport.objects.exists())

    def test_drafts_are_visible_to_hr_only_and_published_reports_to_their_owner(self):
        self.generate()
        report = PerformanceReport.objects.get()
        self.assertEqual(self.hr_client.get(report.get_absolute_url()).status_code, 200)
        self.assertEqual(self.login(self.person).get(report.get_absolute_url()).status_code, 404)  # still a draft
        self.assertEqual(self.login(self.peer).get(report.get_absolute_url()).status_code, 404)
        self.hr_client.post(reverse("vertex-performance-publish", args=[report.pk]))
        report.refresh_from_db()
        self.assertTrue(report.is_published)
        self.assertEqual(report.approved_by, self.hr)
        self.assertEqual(self.login(self.person).get(report.get_absolute_url()).status_code, 200)
        self.assertEqual(self.login(self.peer).get(report.get_absolute_url()).status_code, 404)  # someone else's
        self.assertEqual(Notification.objects.filter(recipient=self.person.employee_user_id).count(), 1)

    def test_only_publishers_can_publish(self):
        self.generate()
        report = PerformanceReport.objects.get()
        self.login(self.person).post(reverse("vertex-performance-publish", args=[report.pk]))
        report.refresh_from_db()
        self.assertFalse(report.is_published)

    def test_publishing_a_low_score_starts_a_plan(self):
        self.generate()
        report = PerformanceReport.objects.get()
        PerformanceReport.objects.filter(pk=report.pk).update(overall_score=Decimal("42.0"))
        self.hr_client.post(reverse("vertex-performance-publish", args=[report.pk]))
        self.assertEqual(Pip.objects.get().subject, self.person)
        # publishing a healthy report does not
        self.generate(subject=self.peer)
        healthy = PerformanceReport.objects.get(subject=self.peer)
        self.hr_client.post(reverse("vertex-performance-publish", args=[healthy.pk]))
        self.assertEqual(Pip.objects.count(), 1)

    def test_report_list_is_scoped(self):
        self.generate()
        self.generate(subject=self.peer)
        PerformanceReport.objects.filter(subject=self.person).update(is_published=True)
        hr_list = self.hr_client.get(reverse("vertex-performance-list"), **HX)
        self.assertContains(hr_list, "Person")
        self.assertContains(hr_list, "Peer")
        own = self.login(self.person).get(reverse("vertex-performance-list"), **HX)
        self.assertContains(own, "Person")
        self.assertNotContains(own, "Peer")

    def test_my_performance_shows_only_published_results_and_my_plans(self):
        self.generate()
        report = PerformanceReport.objects.get()
        client = self.login(self.person)
        self.assertContains(client.get(reverse("vertex-my-performance")), "No performance report has been published")
        self.hr_client.post(reverse("vertex-performance-publish", args=[report.pk]))
        page = client.get(reverse("vertex-my-performance"))
        self.assertContains(page, "2026-Q4")
        self.assertContains(page, "Technical Competency")
        Pip.objects.create(subject=self.person, reason="Work on estimates", start_date=date.today())
        Pip.objects.create(subject=self.peer, reason="Someone else's plan", start_date=date.today())
        page = client.get(reverse("vertex-my-performance"))
        self.assertContains(page, "Work on estimates")
        self.assertNotContains(page, "Someone else")

    def test_my_performance_needs_its_permission(self):
        self.assertNotEqual(self.login(self.peer).get(reverse("vertex-my-performance")).status_code, 200)


class PipPageTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.hr = make_employee("Hr", "hr4@example.com", "9400000041")
        self.person = make_employee("Person", "person2@example.com", "9400000042")
        self.viewer = make_employee("Viewer", "viewer@example.com", "9400000043")
        grant(self.hr, "view_pip", "add_pip", "change_pip")
        grant(self.viewer, "view_pip")
        self.hr_client = self.login(self.hr)

    def test_hr_creates_a_plan_and_the_employee_is_told(self):
        self.hr_client.post(
            reverse("vertex-pip-new"),
            {"subject": self.person.pk, "start_date": "2026-11-01", "reason": "Missed deadlines",
             "expected_improvement": "Deliver on time", "support_provided": "Weekly check-in"}, **HX,
        )
        pip = Pip.objects.get()
        self.assertFalse(pip.is_auto)
        self.assertEqual(pip.created_by, self.hr)
        self.assertEqual(Notification.objects.filter(recipient=self.person.employee_user_id).count(), 1)

    def test_a_reason_is_required(self):
        self.hr_client.post(reverse("vertex-pip-new"), {"subject": self.person.pk, "start_date": "2026-11-01", "reason": " "}, **HX)
        self.assertFalse(Pip.objects.exists())

    def test_update_records_the_outcome_but_not_who_or_when(self):
        pip = Pip.objects.create(subject=self.person, reason="r", start_date=date(2026, 11, 1))
        url = reverse("vertex-pip-edit", args=[pip.pk])
        self.hr_client.post(url, {"subject": self.hr.pk, "start_date": "2027-01-01", "reason": "r",
                                  "status": "completed", "outcome": "improved", "review_notes": "Good progress"}, **HX)
        pip.refresh_from_db()
        self.assertEqual((pip.status, pip.outcome, pip.review_notes), ("completed", "improved", "Good progress"))
        self.assertEqual((pip.subject, pip.start_date), (self.person, date(2026, 11, 1)))

    def test_an_outcome_needs_a_finished_status(self):
        pip = Pip.objects.create(subject=self.person, reason="r", start_date=date(2026, 11, 1))
        self.hr_client.post(reverse("vertex-pip-edit", args=[pip.pk]),
                            {"reason": "r", "status": "open", "outcome": "improved"}, **HX)
        pip.refresh_from_db()
        self.assertEqual(pip.outcome, "pending")

    def test_permissions(self):
        pip = Pip.objects.create(subject=self.person, reason="r", start_date=date(2026, 11, 1))
        viewer = self.login(self.viewer)
        self.assertContains(viewer.get(reverse("vertex-pip-list"), **HX), "Person")
        self.assertNotContains(viewer.get(reverse("vertex-pip-edit", args=[pip.pk]), **HX), "vm__head")
        viewer.post(reverse("vertex-pip-new"), {"subject": self.hr.pk, "start_date": "2026-11-01", "reason": "x"}, **HX)
        self.assertEqual(Pip.objects.count(), 1)
        nothing = self.login(self.person)
        self.assertNotEqual(nothing.get(reverse("vertex-pip")).status_code, 200)

    def test_lists_and_modals_render_in_the_vertex_design(self):
        grant(self.hr, "view_feedback", "add_feedback", "view_performancereport", "add_performancereport")
        client = self.login(self.hr)
        for url in (reverse("vertex-pip-new"), reverse("vertex-feedback-new"), reverse("vertex-performance-generate")):
            page = client.get(url, **HX)
            self.assertContains(page, "vm__head", msg_prefix=url)
