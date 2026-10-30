from django.test import TestCase
from django.urls import reverse

from base.models import Department, JobPosition
from recruitment.models import Recruitment, Skill


class OpenJobsPageTests(TestCase):
    def get(self):
        return self.client.get(reverse("open-recruitments"))

    def test_empty_state_has_no_create_button_and_no_departments(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No open roles")
        self.assertContains(response, "No jobs published yet")
        self.assertNotContains(response, "Create opening")
        self.assertNotContains(response, "Create your first opening")
        self.assertEqual(response.context["departments"], [])
        self.assertNotContains(response, 'class="oj-tab')

    def test_departments_come_only_from_open_roles(self):
        Department.objects.create(department="Accounts")  # nobody is hiring here
        dev = Department.objects.create(department="Development")
        position = JobPosition.objects.create(job_position="Django Dev", department_id=dev)
        skill = Skill.objects.create(title="Python")
        recruitment = Recruitment.objects.create(
            title="Senior Django Developer",
            description="Build services.",
            vacancy=2,
            is_published=True,
        )
        recruitment.open_positions.add(position)
        recruitment.skills.add(skill)

        response = self.get()
        self.assertEqual(response.context["departments"], ["Development"])
        self.assertEqual(response.context["open_count"], 1)
        self.assertEqual(response.context["positions_to_fill"], 2)
        self.assertContains(response, "Senior Django Developer")
        self.assertContains(response, "Build services.")
        self.assertContains(response, "Python")
        self.assertContains(response, 'data-dept="Development"')
        self.assertNotContains(response, "Accounts")
