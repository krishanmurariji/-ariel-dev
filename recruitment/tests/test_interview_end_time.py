"""
The interview scheduling form takes an optional end time next to the start time.
An end time must be later than the start; leaving it empty keeps the old behaviour,
and when it is set the meeting invite gets the real length instead of a fixed hour.
"""

import datetime

from base.models import Company, Department, JobPosition
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from recruitment.forms import ScheduleInterviewForm
from recruitment.models import Candidate, InterviewSchedule, Recruitment

T = datetime.time
FUTURE = datetime.date.today() + datetime.timedelta(days=3)


class InterviewEndTimeBase(IsolatedTestCase):
    def setUp(self):
        company = Company.objects.create(
            company="Co", address="x", country="x", state="x", city="x", zip="0"
        )
        department = Department.objects.create(department="Engineering")
        job = JobPosition.objects.create(job_position="Engineer", department_id=department)
        recruitment = Recruitment.objects.create(
            title="Engineer", closed=False, is_active=True, company_id=company, job_position_id=job
        )
        recruitment.open_positions.add(job)
        self.candidate = Candidate.objects.create(
            name="Jane", email="jane@example.com", recruitment_id=recruitment, job_position_id=job
        )
        _user, self.interviewer = make_employee("iv_panel")

    def data(self, **overrides):
        data = {
            "candidate_id": self.candidate.pk,
            "employee_id": [self.interviewer.pk],
            "interview_date": FUTURE.isoformat(),
            "interview_time": "10:00",
            "interview_end_time": "11:30",
            "interview_mode": "offline",
            "description": "first round",
        }
        data.update(overrides)
        return data

    def form(self, **overrides):
        return ScheduleInterviewForm(data=self.data(**overrides))

    def make_interview(self, start=T(10, 0), end=None):
        interview = InterviewSchedule.objects.create(
            candidate_id=self.candidate,
            interview_date=FUTURE,
            interview_time=start,
            interview_end_time=end,
            interview_mode="offline",
        )
        interview.employee_id.add(self.interviewer)
        return interview


class FormTests(InterviewEndTimeBase):
    def test_the_form_offers_an_end_time_field_after_the_start(self):
        form = self.form()
        names = list(form.fields)
        self.assertIn("interview_end_time", names)
        self.assertEqual(form.fields["interview_end_time"].widget.input_type, "time")
        self.assertIn('type="time"', str(form["interview_end_time"]))
        self.assertFalse(form.fields["interview_end_time"].required)

    def test_a_valid_start_and_end_saves_both(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        interview = form.save()
        interview.refresh_from_db()
        self.assertEqual(interview.interview_time, T(10, 0))
        self.assertEqual(interview.interview_end_time, T(11, 30))

    def test_the_end_time_is_optional(self):
        for empty in ("", None):
            with self.subTest(empty=empty):
                data = self.data()
                data["interview_end_time"] = empty or ""
                form = ScheduleInterviewForm(data=data)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertIsNone(form.save().interview_end_time)

    def test_an_end_before_or_equal_to_the_start_is_refused(self):
        for end in ("09:59", "10:00"):
            with self.subTest(end=end):
                form = self.form(interview_end_time=end)
                self.assertFalse(form.is_valid())
                self.assertIn("interview_end_time", form.errors)
                self.assertIn("later than the start", form.errors["interview_end_time"][0])

    def test_one_minute_after_the_start_is_fine(self):
        self.assertTrue(self.form(interview_end_time="10:01").is_valid())

    def test_garbage_in_the_end_time_is_a_normal_field_error(self):
        form = self.form(interview_end_time="soon")
        self.assertFalse(form.is_valid())
        self.assertIn("interview_end_time", form.errors)

    def test_twelve_hour_input_is_understood(self):
        form = self.form(interview_end_time="11:30 AM")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["interview_end_time"], T(11, 30))

    def test_the_start_time_rules_still_apply(self):
        form = self.form(interview_time="", interview_end_time="11:00")
        self.assertFalse(form.is_valid())
        self.assertIn("interview_time", form.errors)

    def test_editing_shows_the_saved_end_time_and_can_change_or_clear_it(self):
        interview = self.make_interview(end=T(11, 0))
        edit = ScheduleInterviewForm(instance=interview)
        self.assertIn('value="11:00"', str(edit["interview_end_time"]))

        change = ScheduleInterviewForm(
            data=self.data(interview_end_time="12:15"), instance=interview
        )
        self.assertTrue(change.is_valid(), change.errors)
        change.save()
        interview.refresh_from_db()
        self.assertEqual(interview.interview_end_time, T(12, 15))

        clear = ScheduleInterviewForm(
            data=self.data(interview_end_time=""), instance=interview
        )
        self.assertTrue(clear.is_valid(), clear.errors)
        clear.save()
        interview.refresh_from_db()
        self.assertIsNone(interview.interview_end_time)

    def test_an_old_interview_without_an_end_time_still_edits(self):
        interview = self.make_interview(end=None)
        form = ScheduleInterviewForm(
            data=self.data(interview_end_time=""), instance=interview
        )
        self.assertTrue(form.is_valid(), form.errors)


class ModelTests(InterviewEndTimeBase):
    def test_duration_is_start_to_end(self):
        self.assertEqual(self.make_interview(T(10, 0), T(11, 30)).duration_minutes(), 90)
        self.assertEqual(self.make_interview(T(9, 15), T(9, 45)).duration_minutes(), 30)

    def test_without_an_end_time_the_default_length_is_kept(self):
        interview = self.make_interview(end=None)
        self.assertEqual(interview.duration_minutes(), 60)
        self.assertEqual(interview.duration_minutes(default=45), 45)

    def test_a_nonsense_stored_range_falls_back_to_the_default(self):
        interview = self.make_interview(T(10, 0), T(9, 0))
        self.assertEqual(interview.duration_minutes(), 60)

    def test_the_end_column_shows_a_dash_when_empty(self):
        self.assertEqual(self.make_interview(end=None).get_end_time(), "-")
        self.assertEqual(self.make_interview(end=T(11, 0)).get_end_time(), T(11, 0))
