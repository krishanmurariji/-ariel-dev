"""
Interview page: the "This week" strip (Monday to Sunday, only what the user may see) and the
redesigned list (candidate / description / when / mode / interviewers / status + three-dot menu).
"""

import re
from datetime import time, timedelta

from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from base.models import Company, Department
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee
from recruitment.models import Candidate, InterviewSchedule, JobPosition, Recruitment, Stage


def monday():
    today = timezone.localdate()
    return today - timedelta(days=today.weekday())


class InterviewPageTests(IsolatedTestCase):
    def setUp(self):
        company = Company.objects.create(
            company="Iv Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        department = Department.objects.create(department="Engineering")
        job = JobPosition.objects.create(job_position="Backend Dev", department_id=department)
        recruitment = Recruitment.objects.create(
            title="Iv Recruitment", company_id=company, job_position_id=job
        )
        recruitment.open_positions.add(job)
        self.candidate = Candidate.objects.create(
            name="Ishaan Malhotra",
            email="ishaan@example.com",
            recruitment_id=recruitment,
            job_position_id=job,
        )
        stage = Stage.objects.create(
            recruitment_id=recruitment, stage="Technical Screen", stage_type="interview", sequence=3
        )
        Candidate.objects.filter(pk=self.candidate.pk).update(stage_id=stage)
        self.hr_user, self.hr = make_employee("iv_hr")
        grant(
            self.hr_user,
            "recruitment",
            "view_recruitment",
            "view_interviewschedule",
            "change_interviewschedule",
            "delete_interviewschedule",
            "add_interviewschedule",
        )
        self.interviewer_user, self.interviewer = make_employee("iv_interviewer")
        grant(self.interviewer_user, "recruitment", "view_recruitment")
        self.stranger_user, self.stranger = make_employee("iv_stranger")
        grant(self.stranger_user, "recruitment", "view_recruitment")

        self.today = timezone.localdate()
        self.in_week = self.make_interview(monday() + timedelta(days=2), "Technical round 2")
        self.out_of_week = self.make_interview(monday() + timedelta(days=9), "Next week round")

    def make_interview(self, day, description, interviewers=None):
        interview = InterviewSchedule.objects.create(
            candidate_id=self.candidate,
            interview_date=day,
            interview_time=time(11, 0),
            interview_end_time=time(12, 0),
            description=description,
        )
        interview.employee_id.add(*(interviewers or [self.interviewer]))
        return interview

    def client_for(self, user):
        client = Client(raise_request_exception=False)
        client.force_login(type(user).objects.get(pk=user.pk))
        return client

    def week(self, user):
        return self.client_for(user).get(reverse("interview-week")).content.decode()

    def test_week_strip_lists_seven_days_with_this_weeks_interviews(self):
        html = self.week(self.hr_user)
        self.assertIn("This week", html)
        self.assertEqual(len(re.findall(r'class="iv-day( iv-day--today)?"', html)), 7)
        self.assertIn("Technical round 2", html)
        self.assertNotIn("Next week round", html)
        self.assertIn("Ishaan Malhotra", html)
        self.assertIn("11:00", html)

    def test_week_strip_marks_today(self):
        html = self.week(self.hr_user)
        self.assertEqual(html.count("iv-day--today"), 1)

    def test_empty_days_say_free(self):
        html = self.week(self.hr_user)
        self.assertEqual(html.count("iv-day__free"), 6)

    def test_chip_colour_follows_status(self):
        InterviewSchedule.objects.filter(pk=self.in_week.pk).update(completed=True)
        self.assertIn("iv-chip--done", self.week(self.hr_user))
        InterviewSchedule.objects.filter(pk=self.in_week.pk).update(
            completed=False, interview_date=monday() - timedelta(days=1)
        )
        # last week is outside the strip; move it to earlier this week when that is in the past
        if monday() < self.today:
            InterviewSchedule.objects.filter(pk=self.in_week.pk).update(interview_date=monday())
            self.assertIn("iv-chip--late", self.week(self.hr_user))

    def test_user_only_sees_their_own_interviews_without_view_permission(self):
        self.assertIn("Technical round 2", self.week(self.interviewer_user))
        self.assertNotIn("Technical round 2", self.week(self.stranger_user))

    def list_html(self, user=None):
        return self.client_for(user or self.hr_user).get(reverse("interview-list-view")).content.decode()

    def test_list_uses_the_new_columns(self):
        html = self.list_html()
        for header in ("Candidate", "Stage", "When", "Mode"):
            self.assertIn(header, html)
        self.assertNotIn(">Description<", html)
        for old in ("Interview End Time", "Job Opening"):
            self.assertNotIn(old, html)
        self.assertIn('class="iv-person__name"', html)
        self.assertIn("Backend Dev", html)  # job position under the name
        self.assertIn("Technical Screen", html)  # the candidate's current stage
        self.assertIn('class="iv-when"', html)
        self.assertIn("60 min", html)
        self.assertIn("iv-mode", html)
        self.assertIn("iv-pill", html, "")

    def test_list_actions_are_a_three_dot_menu(self):
        html = self.list_html()
        self.assertEqual(html.count('class="row-action-btn"'), 2)  # one per interview
        self.assertIn("<span>Edit</span>", html)
        self.assertIn("<span>Delete</span>", html)

    def test_interviewer_without_delete_permission_gets_edit_only(self):
        html = self.list_html(self.interviewer_user)
        self.assertIn("<span>Edit</span>", html)
        self.assertNotIn("<span>Delete</span>", html)

    def ids_for(self, **params):
        html = self.client_for(self.hr_user).get(reverse("interview-list-view"), params).content.decode()
        return set(map(int, re.findall(r'data-instance-id="(\d+)"', html)))

    def test_status_filter_splits_scheduled_completed_cancelled(self):
        past = self.make_interview(self.today - timedelta(days=3), "Past, not done")
        future = self.make_interview(self.today + timedelta(days=3), "Future")
        done = self.make_interview(self.today - timedelta(days=2), "Done")
        InterviewSchedule.objects.filter(pk=done.pk).update(completed=True)

        cancelled = self.ids_for(status="cancelled")
        self.assertIn(past.pk, cancelled)
        self.assertNotIn(future.pk, cancelled)
        self.assertNotIn(done.pk, cancelled)

        scheduled = self.ids_for(status="scheduled")
        self.assertIn(future.pk, scheduled)
        self.assertNotIn(past.pk, scheduled)
        self.assertNotIn(done.pk, scheduled)

        completed = self.ids_for(status="completed")
        self.assertEqual(completed, {done.pk})

    def test_expired_interview_is_labelled_cancelled(self):
        past = self.make_interview(self.today - timedelta(days=3), "Past, not done")
        html = self.list_html()
        self.assertIn('iv-pill iv-pill--late">Cancelled<', html)
        self.assertNotIn("Expired Interview", html)
        self.assertIn(f'data-instance-id="{past.pk}"', html)

    def test_status_chips_sit_beside_the_select_button(self):
        html = self.list_html()
        for dot, label in (
            ("iv-dot--cancelled", "Cancelled"),
            ("iv-dot--scheduled", "Scheduled"),
            ("iv-dot--completed", "Completed"),
        ):
            self.assertIn(dot, html)
            self.assertIn(label, html)
        self.assertIn("[name=status]", html)

    def test_nav_has_icon_only_filter_and_schedule_interview_button(self):
        html = self.client_for(self.hr_user).get(
            reverse("interview-nav-view"), HTTP_HX_REQUEST="true"
        ).content.decode()
        self.assertIn('aria-label="Filter"', html)
        self.assertNotIn('title="Filter"', html)  # a tooltip would sit on top of the pop-up
        self.assertIn("clearNavFilter", html)
        self.assertIn("Schedule Interview", html)

    def test_interview_today_badge_is_blue(self):
        today = self.make_interview(self.today, "Today round")
        html = self.list_html()
        self.assertIn('iv-pill iv-pill--today">Interview Today<', html)
        self.assertIn(f'data-instance-id="{today.pk}"', html)
        css = render_to_string("cbv/interview/interview_style.html")
        self.assertIn(".iv-pill--today { background: #d4e6ff; color: #1450b8; }", css)

    def test_applied_filter_shows_a_dot_instead_of_the_filter_tags(self):
        nav = self.client_for(self.hr_user).get(
            reverse("interview-nav-view"), HTTP_HX_REQUEST="true"
        ).content.decode()
        self.assertIn('class="nav-filter-dot"', nav)
        self.assertIn("filterApplied", nav)
        self.assertNotIn("Filters:", self.client_for(self.hr_user).get(
            reverse("interview-list-view"), {"status": "cancelled"}
        ).content.decode())
