"""
Performance pages: feedback, meetings and objectives are for the people they involve. An unrelated
employee used to be able to open and answer another person's 360 feedback, read any meeting,
comment on any employee's objective and archive any objective.
"""

import datetime

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


class Feedback360(IsolatedTestCase):
    def setUp(self):
        from pms.models import Feedback, QuestionTemplate

        template = QuestionTemplate.objects.bulk_create([QuestionTemplate(question_template="Q")])[0]
        self.manager_user, manager = make_employee("pa_manager")
        self.subject_user, subject = make_employee("pa_subject", manager=manager)
        self.colleague_user, colleague = make_employee("pa_colleague")
        self.stranger_user, _stranger = make_employee("pa_stranger")
        self.feedback = Feedback.objects.bulk_create(
            [
                Feedback(
                    review_cycle="Cycle",
                    manager_id=manager,
                    employee_id=subject,
                    question_template_id=template,
                    start_date=TODAY,
                    end_date=TODAY + datetime.timedelta(days=10),
                )
            ]
        )[0]
        self.feedback.colleague_id.add(colleague)

    def status(self, user, name):
        return client_for(user).get(reverse(name, args=[self.feedback.pk])).status_code

    def test_an_unrelated_employee_cannot_open_the_answer_page(self):
        self.assertEqual(self.status(self.stranger_user, "feedback-answer-get"), 404)

    def test_an_unrelated_employee_cannot_open_the_answers(self):
        self.assertEqual(self.status(self.stranger_user, "feedback-answer-view"), 404)

    def test_an_unrelated_employee_cannot_submit_answers(self):
        response = client_for(self.stranger_user).post(
            reverse("feedback-answer-post", args=[self.feedback.pk]), {"answer1": "stuffed"}
        )
        self.assertEqual(response.status_code, 404)

    def test_the_people_it_was_sent_to_can_open_it(self):
        for user in (self.manager_user, self.subject_user, self.colleague_user):
            self.assertNotEqual(self.status(user, "feedback-answer-get"), 404, user.username)


class Meetings(IsolatedTestCase):
    def setUp(self):
        from pms.models import Meetings as Meeting, QuestionTemplate

        template = QuestionTemplate.objects.bulk_create([QuestionTemplate(question_template="MQ")])[0]
        self.guest_user, guest = make_employee("pm_guest")
        self.stranger_user, _stranger = make_employee("pm_stranger")
        self.hr_user, _hr = make_employee("pm_hr")
        grant(self.hr_user, "pms", "view_meetings")
        self.meeting = Meeting.objects.bulk_create([Meeting(title="Private meeting", question_template=template)])[0]
        self.meeting.employee_id.add(guest)

    def fetch(self, user):
        # the page is opened from the meeting list, which passes the ids it was listing
        url = reverse("meeting-single-view", args=[self.meeting.pk]) + f"?requests_ids=[{self.meeting.pk}]"
        return client_for(user).get(url)

    def test_a_stranger_cannot_open_the_meeting(self):
        self.assertEqual(self.fetch(self.stranger_user).status_code, 404)

    def test_a_participant_and_the_permission_holder_can(self):
        self.assertEqual(self.fetch(self.guest_user).status_code, 200)
        self.assertEqual(self.fetch(self.hr_user).status_code, 200)


class Objectives(IsolatedTestCase):
    def setUp(self):
        from pms.models import EmployeeObjective, Objective

        self.owner_user, owner = make_employee("po_owner")
        self.stranger_user, _stranger = make_employee("po_stranger")
        self.hr_user, _hr = make_employee("po_hr")
        grant(self.hr_user, "pms", "change_objective")
        self.objective = Objective.objects.bulk_create([Objective(title="Objective", description="d")])[0]
        self.employee_objective = EmployeeObjective.objects.bulk_create(
            [EmployeeObjective(objective_id=self.objective, employee_id=owner, start_date=TODAY, end_date=TODAY)]
        )[0]
        self.model = Objective

    def archived(self):
        return self.model.objects.filter(pk=self.objective.pk, archive=True).exists()

    def test_a_stranger_cannot_archive_an_objective(self):
        client_for(self.stranger_user).post(reverse("objective-archive", args=[self.objective.pk]))
        self.assertFalse(self.archived())

    def test_the_permission_holder_can(self):
        client_for(self.hr_user).post(reverse("objective-archive", args=[self.objective.pk]))
        self.assertTrue(self.archived())

    def test_a_stranger_cannot_comment_on_an_employees_objective(self):
        from pms.models import Comment

        client_for(self.stranger_user).post(
            reverse("objective-detailed-view-comment", args=[self.employee_objective.pk]),
            {"comment": "injected"},
            **HX,
        )
        self.assertFalse(Comment.objects.filter(comment="injected").exists())


class ObjectiveAndFeedbackStatus(IsolatedTestCase):
    def setUp(self):
        from pms.models import EmployeeObjective, Feedback, Objective, QuestionTemplate

        self.manager_user, manager = make_employee("ps_manager")
        self.owner_user, owner = make_employee("ps_owner", manager=manager)
        self.stranger_user, _stranger = make_employee("ps_stranger")
        template = QuestionTemplate.objects.bulk_create([QuestionTemplate(question_template="SQ")])[0]
        objective = Objective.objects.bulk_create([Objective(title="Obj", description="d")])[0]
        self.employee_objective = EmployeeObjective.objects.bulk_create(
            [EmployeeObjective(objective_id=objective, employee_id=owner, start_date=TODAY, end_date=TODAY, status="Not Started")]
        )[0]
        self.feedback = Feedback.objects.bulk_create(
            [
                Feedback(
                    review_cycle="C",
                    manager_id=manager,
                    employee_id=owner,
                    question_template_id=template,
                    start_date=TODAY,
                    end_date=TODAY,
                    status="Not Started",
                )
            ]
        )[0]
        self.EmployeeObjective = EmployeeObjective
        self.Feedback = Feedback

    def test_a_stranger_cannot_change_an_objectives_status(self):
        client_for(self.stranger_user).post(
            reverse("objective-detailed-view-objective-status", args=[self.employee_objective.pk]),
            {"objective_status": "Closed"},
            **HX,
        )
        self.assertEqual(self.EmployeeObjective.objects.get(pk=self.employee_objective.pk).status, "Not Started")

    def test_the_owner_and_the_manager_can(self):
        for user, value in ((self.owner_user, "On Track"), (self.manager_user, "Closed")):
            client_for(user).post(
                reverse("objective-detailed-view-objective-status", args=[self.employee_objective.pk]),
                {"objective_status": value},
                **HX,
            )
            self.assertEqual(self.EmployeeObjective.objects.get(pk=self.employee_objective.pk).status, value)

    def test_a_stranger_cannot_change_a_feedbacks_status(self):
        client_for(self.stranger_user).post(
            reverse("feedback-detailed-view-status", args=[self.feedback.pk]), {"feedback_status": "Closed"}, **HX
        )
        self.assertEqual(self.Feedback.objects.get(pk=self.feedback.pk).status, "Not Started")
