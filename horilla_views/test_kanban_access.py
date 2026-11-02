"""
The generic kanban endpoints (move a card, reorder cards or columns, count cards)
used to take any model and any field name from the browser. They now only serve
the boards listed in horilla_views.kanban_access.BOARDS, and only for people who
may move the card in question.
"""

import json
from types import SimpleNamespace

from django.test import Client, override_settings
from django.urls import reverse

from employee.models import Employee
from helpdesk.models import Ticket, TicketType
from horilla.test_access_control import AccessTestCase, grant
from horilla_views.kanban_access import BOARDS, ChecklistTaskBoard, usable_order_field
from offboarding.models import Offboarding, OffboardingEmployee, OffboardingStage
from recruitment.models import Candidate, Recruitment, Stage


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class KanbanCase(AccessTestCase):
    def client_for(self, user):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        return client

    def move(self, user, model, group_key, group_id, object_id, order, order_by="sequence"):
        return self.client_for(user).post(
            reverse("update-kanban-item-group"),
            {
                "model": model,
                "groupKey": group_key,
                "groupId": group_id,
                "objectId": object_id,
                "order": json.dumps(order),
                "orderBy": order_by,
            },
        )


class RecruitmentBoardTests(KanbanCase):
    def setUp(self):
        super().setUp()
        self.recruitment = Recruitment.objects.bulk_create([Recruitment(title="Dev hiring")])[0]
        other = Recruitment.objects.bulk_create([Recruitment(title="Sales hiring")])[0]
        self.new, self.interview = Stage.objects.bulk_create(
            [
                Stage(recruitment_id=self.recruitment, stage="New", stage_type="initial", sequence=1),
                Stage(recruitment_id=self.recruitment, stage="Interview", stage_type="interview", sequence=2),
            ]
        )
        self.foreign_stage = Stage.objects.bulk_create(
            [Stage(recruitment_id=other, stage="Other", stage_type="initial", sequence=1)]
        )[0]
        self.candidate = Candidate.objects.bulk_create(
            [Candidate(name="Asha", email="asha@example.com", recruitment_id=self.recruitment, stage_id=self.new)]
        )[0]

    def stage_of_candidate(self):
        return Candidate.objects.get(pk=self.candidate.pk).stage_id_id

    def drag(self, user, stage):
        return self.move(user, "recruitment.Candidate", "stage_id", stage.pk, self.candidate.pk, [self.candidate.pk])

    def test_a_stranger_cannot_move_a_candidate(self):
        self.assertEqual(self.drag(self.stranger_user, self.interview).status_code, 403)
        self.assertEqual(self.stage_of_candidate(), self.new.pk)

    def test_someone_with_the_permission_can(self):
        grant(self.hr_user, "recruitment", "change_candidate")
        self.assertEqual(self.drag(self.hr_user, self.interview).status_code, 200)
        self.assertEqual(self.stage_of_candidate(), self.interview.pk)

    def test_a_manager_of_the_candidates_stage_can(self):
        self.new.stage_managers.add(self.mgr)
        self.assertEqual(self.drag(self.mgr_user, self.interview).status_code, 200)
        self.assertEqual(self.stage_of_candidate(), self.interview.pk)

    def test_a_manager_of_another_recruitment_cannot(self):
        self.foreign_stage.stage_managers.add(self.mgr)
        self.assertEqual(self.drag(self.mgr_user, self.interview).status_code, 403)
        self.assertEqual(self.stage_of_candidate(), self.new.pk)

    def test_a_candidate_cannot_be_moved_into_another_recruitments_stage(self):
        grant(self.hr_user, "recruitment", "change_candidate")
        self.assertEqual(self.drag(self.hr_user, self.foreign_stage).status_code, 403)
        self.assertEqual(self.stage_of_candidate(), self.new.pk)

    def test_reordering_cards_is_refused_for_a_stranger(self):
        r = self.client_for(self.stranger_user).post(
            reverse("update-kanban-sequence"),
            {"model": "recruitment.Candidate", "order": json.dumps([self.candidate.pk]), "orderBy": "sequence"},
        )
        self.assertEqual(r.status_code, 403)

    def test_reordering_columns_needs_the_stage_permission(self):
        url = reverse("update-kanban-group-sequence")
        params = {
            "model": "recruitment.Candidate",
            "group_key": "stage_id",
            "sequence": json.dumps([self.interview.pk, self.new.pk]),
            "orderBy": "sequence",
        }
        self.assertEqual(self.client_for(self.stranger_user).post(url, params).status_code, 403)
        self.assertEqual(Stage.objects.get(pk=self.new.pk).sequence, 1)
        grant(self.hr_user, "recruitment", "change_stage")
        self.assertEqual(self.client_for(self.hr_user).post(url, params).status_code, 200)
        self.assertEqual(Stage.objects.get(pk=self.new.pk).sequence, 1)
        self.assertEqual(Stage.objects.get(pk=self.interview.pk).sequence, 0)

    def test_only_people_who_can_open_the_board_may_count_its_cards(self):
        url = reverse("get-kanban-card-count")
        params = {"model": "recruitment.Candidate", "group_key": "stage_id", "group_id": self.new.pk}
        self.assertEqual(self.client_for(self.stranger_user).get(url, params).status_code, 403)
        grant(self.hr_user, "recruitment", "view_recruitment")
        r = self.client_for(self.hr_user).get(url, params)
        self.assertEqual((r.status_code, r.content), (200, b"1"))


class AnythingElseIsRefusedTests(KanbanCase):
    def test_other_tables_cannot_be_reached(self):
        before = list(Employee.objects.values_list("pk", "employee_first_name"))
        for model, key in (
            ("employee.Employee", "employee_first_name"),
            ("auth.User", "is_superuser"),
            ("horilla_auth.HorillaUser", "is_superuser"),
            ("base.Company", "company"),
        ):
            with self.subTest(model):
                r = self.move(self.hr_user, model, key, "x", self.owner.pk, [self.owner.pk], order_by="id")
                self.assertEqual(r.status_code, 400)
        self.assertEqual(list(Employee.objects.values_list("pk", "employee_first_name")), before)

    def test_the_other_endpoints_refuse_unknown_boards_too(self):
        c = self.client_for(self.hr_user)
        seq = c.post(reverse("update-kanban-sequence"), {"model": "employee.Employee", "order": "[1]", "orderBy": "id"})
        self.assertEqual(seq.status_code, 400)
        groups = c.post(
            reverse("update-kanban-group-sequence"),
            {"model": "employee.Employee", "group_key": "x", "sequence": "[1]", "orderBy": "sequence"},
        )
        self.assertNotEqual(groups.status_code, 200)
        count = c.get(reverse("get-kanban-card-count"), {"model": "employee.Employee", "group_key": "pk", "group_id": 1})
        self.assertNotEqual(count.status_code, 200)

    def test_a_card_count_cannot_be_used_to_probe_other_tables(self):
        # the count used to accept any lookup path as the grouping field, so a guess such as
        # "..._user_id__password__startswith" could be tested one letter at a time
        grant(self.hr_user, "recruitment", "view_recruitment")
        r = self.client_for(self.hr_user).get(
            reverse("get-kanban-card-count"),
            {
                "model": "recruitment.Candidate",
                "group_key": "recruitment_id__recruitment_managers__employee_user_id__password__startswith",
                "group_id": "a",
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_the_visitor_who_is_not_logged_in_gets_nothing(self):
        r = Client().get(reverse("get-kanban-card-count"), {"model": "recruitment.Candidate"})
        self.assertEqual(r.status_code, 302)

    def test_a_board_cannot_be_moved_by_a_field_it_does_not_group_by(self):
        r = self.move(self.hr_user, "recruitment.Candidate", "recruitment_id", 1, 1, [1])
        self.assertEqual(r.status_code, 400)

    def test_only_a_sequence_column_is_ever_written_as_the_order(self):
        for board in BOARDS.values():
            model = __import__("django.apps", fromlist=["apps"]).apps.get_model(*board.model_path.split("."))
            self.assertFalse(usable_order_field(model, "id"), board.model_path)
            self.assertFalse(usable_order_field(model, "pk"), board.model_path)
            self.assertFalse(usable_order_field(model, ""), board.model_path)
            self.assertFalse(usable_order_field(model, "F(x)"), board.model_path)


class TicketBoardTests(KanbanCase):
    def setUp(self):
        super().setUp()
        ticket_type = TicketType.objects.bulk_create([TicketType(title="IT", type="others", prefix="IT")])[0]
        self.ticket = Ticket.objects.bulk_create(
            [
                Ticket(
                    title="Printer",
                    employee_id=self.owner,
                    ticket_type=ticket_type,
                    description="broken",
                    assigning_type="individual",
                    raised_on=str(self.hr.pk),
                )
            ]
        )[0]

    def status(self):
        return Ticket.objects.get(pk=self.ticket.pk).status

    def drag(self, user, status):
        return self.move(user, "helpdesk.Ticket", "status", status, self.ticket.pk, [self.ticket.pk])

    def test_a_stranger_cannot_change_a_ticket_status(self):
        self.assertEqual(self.drag(self.stranger_user, "resolved").status_code, 403)
        self.assertEqual(self.status(), "new")

    def test_the_person_who_raised_it_can(self):
        self.assertEqual(self.drag(self.owner_user, "in_progress").status_code, 200)
        self.assertEqual(self.status(), "in_progress")

    def test_a_person_assigned_to_it_can(self):
        self.ticket.assigned_to.add(self.hr)
        self.assertEqual(self.drag(self.hr_user, "on_hold").status_code, 200)

    def test_only_real_statuses_are_accepted(self):
        grant(self.hr_user, "helpdesk", "change_ticket")
        self.assertEqual(self.drag(self.hr_user, "<script>").status_code, 400)
        self.assertEqual(self.status(), "new")


class OffboardingBoardTests(KanbanCase):
    def setUp(self):
        super().setUp()
        self.offboarding = Offboarding.objects.bulk_create([Offboarding(title="Exit", description="x")])[0]
        self.first, self.second = OffboardingStage.objects.bulk_create(
            [
                OffboardingStage(title="Notice", type="notice_period", offboarding_id=self.offboarding),
                OffboardingStage(title="Handover", type="handover", offboarding_id=self.offboarding),
            ]
        )
        self.leaver = OffboardingEmployee.objects.bulk_create(
            [OffboardingEmployee(employee_id=self.owner, stage_id=self.first)]
        )[0]

    def stage(self):
        return OffboardingEmployee.objects.get(pk=self.leaver.pk).stage_id_id

    def drag(self, user):
        return self.move(user, "offboarding.OffboardingEmployee", "stage_id", self.second.pk, self.leaver.pk, [self.leaver.pk])

    def test_a_stranger_cannot_move_someone_through_the_exit_stages(self):
        self.assertEqual(self.drag(self.stranger_user).status_code, 403)
        self.assertEqual(self.stage(), self.first.pk)

    def test_the_stage_manager_can(self):
        self.first.managers.add(self.mgr)
        self.assertEqual(self.drag(self.mgr_user).status_code, 200)
        self.assertEqual(self.stage(), self.second.pk)


class ChecklistRuleTests(KanbanCase):
    def test_a_task_may_be_moved_by_its_assignee_or_a_user_with_the_permission(self):
        board = ChecklistTaskBoard()
        task = SimpleNamespace(assignee_id_id=self.owner.pk)
        self.assertTrue(board.can_move(self.owner_user, task))
        self.assertFalse(board.can_move(self.stranger_user, task))
        grant(self.hr_user, "onboarding", "change_candidatechecklisttask")
        self.assertTrue(board.can_move(self.hr_user, task))
