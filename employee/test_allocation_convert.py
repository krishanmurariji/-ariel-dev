"""
Opening a candidate's allocation page used to create an Employee on GET.
Creating the employee is now a POST; GET only reads.
"""

from django.test import Client
from django.urls import reverse

from employee.models import Employee
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from recruitment.models import Candidate, Recruitment
from base.models import JobPosition, Department

MODEL = "recruitment.models.Candidate"


class AllocationConvertTests(IsolatedTestCase):
    def setUp(self):
        user, _ = make_employee("alloc_hr")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        self.client = Client()
        self.client.force_login(user)
        department = Department.objects.create(department="Alloc dept")
        position = JobPosition.objects.create(
            job_position="Alloc position", department_id=department
        )
        recruitment = Recruitment.objects.create(
            title="Alloc recruitment", description="d", is_active=True
        )
        recruitment.open_positions.add(position)
        self.candidate = Candidate.objects.create(
            name="Alloc Candidate",
            email="alloc.candidate@example.com",
            recruitment_id=recruitment,
            job_position_id=position,
            hired=True,
        )
        self.url = reverse("allocation-view", kwargs={"pk": self.candidate.pk})
        self.query = {"model": MODEL}

    def employees(self):
        return Employee.objects.entire().filter(email="alloc.candidate@example.com")

    def test_get_creates_no_employee_and_links_nothing(self):
        self.client.get(self.url, self.query, HTTP_HX_REQUEST="true")
        self.assertEqual(self.employees().count(), 0)
        self.candidate.refresh_from_db()
        self.assertIsNone(self.candidate.converted_employee_id)

    def test_post_creates_the_employee_and_links_it(self):
        response = self.client.post(
            self.url + "?model=" + MODEL, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.employees().count(), 1)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.converted_employee_id, self.employees().get())

    def test_posting_twice_reuses_the_same_employee(self):
        for _ in range(2):
            self.client.post(self.url + "?model=" + MODEL, HTTP_HX_REQUEST="true")
        self.assertEqual(self.employees().count(), 1)

    def test_get_works_after_conversion(self):
        self.client.post(self.url + "?model=" + MODEL, HTTP_HX_REQUEST="true")
        response = self.client.get(self.url, self.query, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.employees().count(), 1)

    def test_a_candidate_who_cannot_be_allocated_gets_no_employee_from_post(self):
        Candidate.objects.filter(pk=self.candidate.pk).update(hired=False)
        self.client.post(self.url + "?model=" + MODEL, HTTP_HX_REQUEST="true")
        self.assertEqual(self.employees().count(), 0)

    def test_post_without_a_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.client_user())
        response = client.post(self.url + "?model=" + MODEL, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.employees().count(), 0)

    def client_user(self):
        from horilla_auth.models import HorillaUser

        return HorillaUser.objects.get(username="alloc_hr")
