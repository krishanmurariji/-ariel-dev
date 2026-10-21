"""
Project tasks and timesheets, and the recruitment edit actions, were open to every signed-in
employee: any task or timesheet could be read by id, and any employee could rename a hiring stage,
create or retitle a candidate's document request, and add or delete note files.
"""

import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee

HX = {"HTTP_HX_REQUEST": "true"}
TODAY = datetime.date.today()


def client_for(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class ProjectPages(IsolatedTestCase):
    def setUp(self):
        from project.models import Project, ProjectStage, Task, TimeSheet

        self.member_user, member = make_employee("pp_member")
        self.stranger_user, _stranger = make_employee("pp_stranger")
        self.hr_user, _hr = make_employee("pp_hr")
        grant(self.hr_user, "project", "view_task", "view_timesheet")
        project = Project.objects.bulk_create([Project(title="PP project", start_date=TODAY)])[0]
        stage = ProjectStage.objects.bulk_create([ProjectStage(title="PP stage", project=project, sequence=1)])[0]
        self.task = Task.objects.bulk_create([Task(title="PP-SECRET-TASK", project=project, stage=stage)])[0]
        self.task.task_members.add(member)
        self.timesheet = TimeSheet.objects.bulk_create(
            [TimeSheet(project_id=project, task_id=self.task, employee_id=member, date=TODAY, time_spent="01:00", status="in_progress")]
        )[0]

    def test_a_stranger_cannot_open_a_task(self):
        response = client_for(self.stranger_user).get(reverse("task-details", args=[self.task.pk]))
        self.assertEqual(response.status_code, 404)

    def test_a_task_member_and_the_permission_holder_can(self):
        for user in (self.member_user, self.hr_user):
            response = client_for(user).get(reverse("task-details", args=[self.task.pk]))
            self.assertContains(response, "PP-SECRET-TASK")

    def test_a_stranger_cannot_open_a_timesheet(self):
        response = client_for(self.stranger_user).get(reverse("view-single-time-sheet", args=[self.timesheet.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_owner_and_the_permission_holder_can_open_the_timesheet(self):
        for user in (self.member_user, self.hr_user):
            response = client_for(user).get(reverse("view-single-time-sheet", args=[self.timesheet.pk]))
            self.assertEqual(response.status_code, 200)

    def test_a_stranger_cannot_open_anothers_timesheet_chart(self):
        from employee.models import Employee

        owner = Employee.objects.get(employee_user_id=self.member_user)
        response = client_for(self.stranger_user).get(reverse("personal-time-sheet-view", args=[owner.pk]))
        self.assertEqual(response.status_code, 404)


class RecruitmentEditActions(IsolatedTestCase):
    def setUp(self):
        from recruitment.factories import CandidateFactory
        from recruitment.models import CandidateDocument, Stage

        self.stranger_user, _stranger = make_employee("rr_stranger")
        self.recruiter_user, recruiter = make_employee("rr_recruiter")
        grant(
            self.recruiter_user,
            "recruitment",
            "change_stage",
            "change_candidatedocument",
            "add_candidatedocument",
            "add_stagenote",
        )
        self.candidate = CandidateFactory()
        self.stage = Stage.objects.filter(recruitment_id=self.candidate.recruitment_id).first()
        self.document = CandidateDocument(title="Original title", candidate_id=self.candidate)
        self.document.document.save("d.pdf", SimpleUploadedFile("d.pdf", b"%PDF-1.4"), save=False)
        self.document.save()
        self.recruiter_employee = recruiter

    def stage_title(self):
        from recruitment.models import Stage

        return Stage.objects.get(pk=self.stage.pk).stage

    def document_title(self):
        from recruitment.models import CandidateDocument

        return CandidateDocument.objects.get(pk=self.document.pk).title

    def test_a_stranger_cannot_rename_a_stage(self):
        original = self.stage_title()
        client_for(self.stranger_user).post(reverse("stage-title-update", args=[self.stage.pk]), {"stage": "Hacked"}, **HX)
        self.assertEqual(self.stage_title(), original)

    def test_the_permission_holder_can_rename_a_stage(self):
        client_for(self.recruiter_user).post(reverse("stage-title-update", args=[self.stage.pk]), {"stage": "Renamed"}, **HX)
        self.assertEqual(self.stage_title(), "Renamed")

    def test_a_stranger_cannot_retitle_a_candidate_document(self):
        client_for(self.stranger_user).post(reverse("candidate-update-document-title", args=[self.document.pk]), {"title": "Hacked"})
        self.assertEqual(self.document_title(), "Original title")

    def test_the_permission_holder_can_retitle_it(self):
        client_for(self.recruiter_user).post(reverse("candidate-update-document-title", args=[self.document.pk]), {"title": "Renamed"})
        self.assertEqual(self.document_title(), "Renamed")

    def test_a_stranger_cannot_add_a_note_for_a_candidate(self):
        from recruitment.models import StageNote

        before = StageNote.objects.count()
        client_for(self.stranger_user).post(
            reverse("candidate-add-notes", args=[self.candidate.pk]),
            {"description": "injected", "candidate_id": self.candidate.pk},
        )
        self.assertEqual(StageNote.objects.count(), before)

    def test_a_stranger_cannot_open_the_document_request_form(self):
        response = client_for(self.stranger_user).get(reverse("candidate-document-create", args=[self.candidate.pk]), **HX)
        self.assertNotIn(b"form", response.content.lower().replace(b"permission", b""), "form was shown")
