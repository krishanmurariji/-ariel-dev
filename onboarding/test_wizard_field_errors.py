"""
Self-onboarding wizard: a rejected value is explained next to its own input, in plain words -
not as a boxed dump of raw field names ("start_date", "end_date").
"""

from datetime import date, timedelta

from django.test import Client, override_settings
from django.urls import reverse

from base.models import Company, Department, JobPosition
from employee.models import Employee
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from onboarding.models import OnboardingPortal
from recruitment.models import Candidate, Recruitment

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class ExperienceStepErrors(IsolatedTestCase):
    TOKEN = "wizard-errors-tok"

    def setUp(self):
        company = Company.objects.create(company="WE Co", address="x", country="x", state="x", city="x", zip="00000")
        department = Department.objects.create(department="Eng")
        job = JobPosition.objects.create(job_position="Dev", department_id=department)
        recruitment = Recruitment.objects.create(title="WE", company_id=company, job_position_id=job)
        recruitment.open_positions.add(job)
        user, _employee = make_employee("we_candidate")
        self.candidate = Candidate.objects.create(
            name="Wiz", email=user.username, recruitment_id=recruitment, job_position_id=job
        )
        OnboardingPortal.objects.create(candidate_id=self.candidate, token=self.TOKEN, used=False)
        self.url = reverse("employee-experience", kwargs={"token": self.TOKEN})

    def post(self, start, end):
        data = {
            "experience-TOTAL_FORMS": "1", "experience-INITIAL_FORMS": "0",
            "experience-MIN_NUM_FORMS": "0", "experience-MAX_NUM_FORMS": "1000",
            "experience-0-company_name": "Theon", "experience-0-designation": "HR Officer",
            "experience-0-start_date": start, "experience-0-end_date": end, "experience-0-location": "Nalagarh",
        }
        return Client(raise_request_exception=False).post(self.url, data)

    def test_a_future_date_is_explained_under_its_own_field(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        response = self.post("2024-04-06", future)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Date of relieving cannot be in the future.", html)
        self.assertIn('class="oh-swz__error"', html)
        # the raw field names are no longer shown as headings
        self.assertNotIn("<li>end_date", html)
        self.assertNotIn("errorlist", html)
        # a valid joining date gets no message
        self.assertNotIn("Date of joining cannot be in the future.", html)
