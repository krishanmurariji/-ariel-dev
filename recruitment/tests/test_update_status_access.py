"""
Update Status (candidate profile menu): who may move a candidate to a stage of any job.

Holding "change candidate" (an HR Manager group, say) is enough. Without it, only the recruitment or stage
managers of the target job may; people who may only view candidates, and everyone else, may not.
"""

from django.test import Client
from django.urls import reverse

from base.models import Company, Department, JobPosition
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee
from recruitment.models import Candidate, Recruitment, Stage

HX = {"HTTP_HX_REQUEST": "true"}


def client_for(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class UpdateStatusAccess(IsolatedTestCase):
    def setUp(self):
        company = Company.objects.create(company="US Co", address="x", country="x", state="x", city="x", zip="00000")
        department = Department.objects.create(department="Eng")
        self.job_a = JobPosition.objects.create(job_position="Job A", department_id=department)
        self.job_b = JobPosition.objects.create(job_position="Job B", department_id=department)
        self.rec_a = Recruitment.objects.create(title="Rec A", company_id=company, job_position_id=self.job_a)
        self.rec_a.open_positions.add(self.job_a)
        self.rec_b = Recruitment.objects.create(title="Rec B", company_id=company, job_position_id=self.job_b)
        self.rec_b.open_positions.add(self.job_b)
        self.candidate = Candidate.objects.create(
            name="Move Me", email="moveme@example.com", recruitment_id=self.rec_a, job_position_id=self.job_a
        )
        self.stage_b = Stage.objects.filter(recruitment_id=self.rec_b).order_by("sequence").first()
        self.stage_a2 = Stage.objects.filter(recruitment_id=self.rec_a).order_by("sequence")[1]

        self.hr_user, _hr = make_employee("us_hr")
        grant(self.hr_user, "recruitment", "view_candidate", "change_candidate")
        self.viewer_user, _viewer = make_employee("us_viewer")
        grant(self.viewer_user, "recruitment", "view_candidate")
        self.plain_user, _plain = make_employee("us_plain")
        self.manager_b_user, manager_b = make_employee("us_manager_b")
        self.rec_b.recruitment_managers.add(manager_b)
        self.manager_a_user, manager_a = make_employee("us_manager_a")
        self.rec_a.recruitment_managers.add(manager_a)

    def update_status(self, user, recruitment, stage):
        return client_for(user).post(
            reverse("candidate-update-status-form", args=[self.candidate.pk]),
            {"recruitment_id": recruitment.pk, "stage_id": stage.pk},
            **HX,
        )

    def moved_to(self):
        self.candidate.refresh_from_db()
        return self.candidate.recruitment_id_id, self.candidate.stage_id_id

    def test_a_holder_of_change_candidate_can_move_a_candidate_into_any_job(self):
        self.update_status(self.hr_user, self.rec_b, self.stage_b)
        self.assertEqual(self.moved_to(), (self.rec_b.pk, self.stage_b.pk))

    def test_a_holder_of_change_candidate_can_change_the_stage_within_the_same_job(self):
        self.update_status(self.hr_user, self.rec_a, self.stage_a2)
        self.assertEqual(self.moved_to(), (self.rec_a.pk, self.stage_a2.pk))

    def test_the_recruitment_manager_of_the_target_job_still_can(self):
        self.update_status(self.manager_b_user, self.rec_b, self.stage_b)
        self.assertEqual(self.moved_to(), (self.rec_b.pk, self.stage_b.pk))

    def test_a_manager_of_a_different_job_still_cannot_move_a_candidate_into_this_one(self):
        before = self.moved_to()
        self.update_status(self.manager_a_user, self.rec_b, self.stage_b)
        self.assertEqual(self.moved_to(), before)

    def test_someone_who_may_only_view_candidates_cannot_move_them(self):
        before = self.moved_to()
        self.update_status(self.viewer_user, self.rec_b, self.stage_b)
        self.assertEqual(self.moved_to(), before)

    def test_a_plain_employee_cannot_move_them(self):
        before = self.moved_to()
        self.update_status(self.plain_user, self.rec_b, self.stage_b)
        self.assertEqual(self.moved_to(), before)

    def test_an_anonymous_visitor_cannot_move_them(self):
        before = self.moved_to()
        Client(raise_request_exception=False).post(
            reverse("candidate-update-status-form", args=[self.candidate.pk]),
            {"recruitment_id": self.rec_b.pk, "stage_id": self.stage_b.pk},
            **HX,
        )
        self.assertEqual(self.moved_to(), before)

    def test_a_stage_that_belongs_to_another_job_is_not_a_way_around_the_check(self):
        # the form names job A but a stage of job B: it must not move the candidate for a plain employee
        before = self.moved_to()
        self.update_status(self.plain_user, self.rec_a, self.stage_b)
        self.assertEqual(self.moved_to(), before)
