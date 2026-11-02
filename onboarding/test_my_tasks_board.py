"""
My Tasks board (onboarding): moving and re-ordering cards must change nothing but the card's own status.

The board names the task's own `id` as its sort field. The generic save endpoints used to set each card's
`id` to its position in the column (0, 1, 2 ...) and save it, which wrote a copy of the card over the task
that held that id, or inserted a new task with id 0 - so a moved task showed up twice, then three times.
"""

import json

from django.test import Client
from django.urls import reverse

from base.models import Company, Department, JobPosition
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee
from onboarding.models import CandidateChecklistTask
from recruitment.models import Candidate, Recruitment

MODEL = "onboarding.CandidateChecklistTask"


def client_for(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class MyTasksBoardMoves(IsolatedTestCase):
    def setUp(self):
        company = Company.objects.create(company="MT Co", address="x", country="x", state="x", city="x", zip="00000")
        department = Department.objects.create(department="Eng")
        job = JobPosition.objects.create(job_position="Dev", department_id=department)
        recruitment = Recruitment.objects.create(title="MT", company_id=company, job_position_id=job)
        recruitment.open_positions.add(job)
        self.ankit = Candidate.objects.create(name="Ankit", email="ankit@example.com", recruitment_id=recruitment, job_position_id=job)
        self.hitendra = Candidate.objects.create(name="Hitendra", email="hit@example.com", recruitment_id=recruitment, job_position_id=job)
        self.hr_user, self.hr = make_employee("mt_hr")
        grant(self.hr_user, "onboarding", "view_candidatechecklisttask", "change_candidatechecklisttask")
        # small, sequential ids - the ones the old code overwrote
        rows = [
            CandidateChecklistTask(id=1, candidate_id=self.ankit, title="Offer formalities", phase="pre_joining", status="todo"),
            CandidateChecklistTask(id=2, candidate_id=self.ankit, title="Inform department heads", phase="pre_joining", status="todo"),
            CandidateChecklistTask(id=3, candidate_id=self.ankit, title="Share joining details", phase="pre_joining", status="todo"),
            CandidateChecklistTask(id=4, candidate_id=self.hitendra, title="Collect documents", phase="pre_joining", status="todo"),
            CandidateChecklistTask(id=5, candidate_id=self.hitendra, title="Prepare ID card", phase="pre_joining", status="scheduled"),
        ]
        CandidateChecklistTask.objects.bulk_create(rows)
        self.client = client_for(self.hr_user)

    def snapshot(self):
        return {
            task.pk: (task.candidate_id_id, task.title, task.status)
            for task in CandidateChecklistTask.objects.all().order_by("pk")
        }

    def drag(self, task_id, to_status, column):
        """What the board does: ask permission/save, then tell the generic endpoint the column's new order."""
        self.client.get(
            reverse("onboarding-checklist-task-kanban-move"), {"objectId": task_id, "groupId": to_status}
        )
        return self.client.post(
            "/update-kanban-item-group/",
            {
                "model": MODEL,
                "groupKey": "status",
                "groupId": to_status,
                "objectId": task_id,
                "orderBy": "id",
                "order": json.dumps(column),
            },
        )

    def test_moving_a_card_changes_only_that_cards_status(self):
        before = self.snapshot()
        self.drag(3, "scheduled", [3])
        after = self.snapshot()
        self.assertEqual(set(after), set(before), "no task was added or lost")
        self.assertEqual(after[3], (self.ankit.pk, "Share joining details", "scheduled"))
        for pk in (1, 2, 4, 5):
            self.assertEqual(after[pk], before[pk], f"task {pk} was touched")

    def test_moving_a_card_below_another_in_the_column_changes_only_that_card(self):
        before = self.snapshot()
        self.drag(2, "scheduled", [5, 2])  # joins a column that already holds task 5
        after = self.snapshot()
        self.assertEqual(set(after), set(before))
        self.assertEqual(after[2][2], "scheduled")
        self.assertEqual(after[5], before[5])
        self.assertEqual(after[1], before[1])

    def test_moving_cards_over_and_over_never_adds_tasks(self):
        count = CandidateChecklistTask.objects.count()
        self.drag(3, "scheduled", [3])
        self.drag(2, "scheduled", [3, 2])
        self.drag(1, "ongoing", [1])
        self.drag(3, "todo", [3])
        self.assertEqual(CandidateChecklistTask.objects.count(), count)
        self.assertFalse(CandidateChecklistTask.objects.filter(pk=0).exists())

    def test_reordering_inside_a_column_changes_nothing(self):
        before = self.snapshot()
        response = self.client.post(
            "/update-kanban-sequence/",
            {"model": MODEL, "groupKey": "status", "groupId": "todo", "orderBy": "id", "order": json.dumps([3, 1, 2])},
        )
        self.assertLess(response.status_code, 500)
        self.assertEqual(self.snapshot(), before)

    def test_a_task_that_is_not_yours_cannot_be_moved(self):
        plain_user, _plain = make_employee("mt_plain")
        before = self.snapshot()
        client_for(plain_user).get(
            reverse("onboarding-checklist-task-kanban-move"), {"objectId": 3, "groupId": "done"}
        )
        self.assertEqual(self.snapshot(), before)

    def test_a_status_that_does_not_exist_is_refused(self):
        before = self.snapshot()
        self.client.get(reverse("onboarding-checklist-task-kanban-move"), {"objectId": 3, "groupId": "nonsense"})
        self.assertEqual(self.snapshot(), before)


import re


class MyTasksColumnCounts(MyTasksBoardMoves):
    """The number on a column heading is the number of cards that candidate has in it."""

    def heading_counts(self, candidate):
        response = self.client.get(
            reverse("my-checklist-tasks-kanban", kwargs={"pk": candidate.pk}), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        return {
            key: int(re.sub(r"\D", "", number) or 0)
            for key, number in re.findall(r'id="groupCount([a-z]+)"[^>]*>\s*([0-9]+)\s*<', html)
        }

    def count_url(self, candidate, status, extra=""):
        return (
            reverse("my-checklist-tasks-kanban-count", kwargs={"pk": candidate.pk})
            + f"?group_id={status}{extra}"
        )

    def test_headings_show_only_that_candidates_cards(self):
        self.assertEqual(self.heading_counts(self.ankit)["todo"], 3)
        self.assertEqual(self.heading_counts(self.ankit)["scheduled"], 0)
        counts = self.heading_counts(self.hitendra)
        self.assertEqual((counts["todo"], counts["scheduled"]), (1, 1))

    def test_the_board_does_not_overwrite_the_tab_badge_with_the_column_count(self):
        response = self.client.get(
            reverse("my-checklist-tasks-kanban", kwargs={"pk": self.ankit.pk}), HTTP_HX_REQUEST="true"
        )
        self.assertNotIn("badge.html(count)", response.content.decode())

    def test_the_count_after_a_move_matches_the_cards(self):
        self.drag(3, "scheduled", [3])
        self.assertEqual(self.client.get(self.count_url(self.ankit, "todo")).content, b"2")
        self.assertEqual(self.client.get(self.count_url(self.ankit, "scheduled")).content, b"1")
        counts = self.heading_counts(self.ankit)
        self.assertEqual((counts["todo"], counts["scheduled"]), (2, 1))

    def test_other_candidates_tasks_are_not_in_the_count(self):
        # Hitendra's todo and scheduled tasks must not leak into Ankit's numbers
        self.assertEqual(self.client.get(self.count_url(self.ankit, "scheduled")).content, b"0")
        self.assertEqual(self.client.get(self.count_url(self.hitendra, "scheduled")).content, b"1")

    def test_the_boards_filters_apply_to_the_count(self):
        self.assertEqual(self.client.get(self.count_url(self.ankit, "todo", "&search=Offer")).content, b"1")
        self.assertEqual(self.client.get(self.count_url(self.ankit, "todo", "&search=zzz")).content, b"0")

    def test_someone_who_may_only_see_their_own_tasks_counts_only_those(self):
        mine_user, mine = make_employee("mt_mine")
        CandidateChecklistTask.objects.filter(pk=1).update(assignee_id=mine)
        response = client_for(mine_user).get(self.count_url(self.ankit, "todo"))
        self.assertEqual(response.content, b"1")

    def test_a_status_that_does_not_exist_counts_zero(self):
        self.assertEqual(self.client.get(self.count_url(self.ankit, "nonsense")).content, b"0")

    def test_a_column_with_more_cards_than_a_page_shows_its_full_total(self):
        CandidateChecklistTask.objects.bulk_create(
            [
                CandidateChecklistTask(id=100 + n, candidate_id=self.ankit, title=f"Extra {n}", phase="pre_joining", status="ongoing")
                for n in range(30)
            ]
        )
        self.assertEqual(self.heading_counts(self.ankit)["ongoing"], 30)
        self.assertEqual(self.client.get(self.count_url(self.ankit, "ongoing")).content, b"30")

    def test_anonymous_visitors_get_no_count(self):
        response = Client(raise_request_exception=False).get(self.count_url(self.ankit, "todo"))
        self.assertNotEqual(response.content, b"3")


class MyTasksSearchBox(MyTasksBoardMoves):
    """The search box sits beside the Kanban/List buttons, not inside the Filter popup."""

    def page(self, query=""):
        response = self.client.get(reverse("my-checklist-tasks") + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_there_is_one_search_box_and_it_comes_before_the_view_buttons(self):
        html = self.page()
        self.assertEqual(html.count('name="search"'), 1)
        self.assertLess(html.index('name="search"'), html.index("oh-view-types"))

    def test_it_is_not_in_the_filter_popup_any_more(self):
        html = self.page()
        popup = html[html.index("oh-dropdown__filter-body") : html.index("oh-dropdown__filter-footer")]
        self.assertNotIn("search", popup.lower())
        for name in ("candidate", "status", "phase"):  # the real filters are still there
            self.assertIn(name, popup)

    def test_what_was_searched_for_stays_in_the_box(self):
        self.assertIn('value="Offer"', self.page("?search=Offer"))

    def test_searching_narrows_the_board(self):
        response = self.client.get(
            reverse("my-checklist-tasks-board"), {"view": "list", "search": "Offer"}, HTTP_HX_REQUEST="true"
        )
        html = response.content.decode()
        self.assertIn("Offer formalities", html)
        self.assertNotIn("Share joining details", html)

    def test_searching_keeps_the_page_address_in_the_address_bar(self):
        response = self.client.get(
            reverse("my-checklist-tasks-board"), {"view": "kanban", "search": "Offer"}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response["HX-Push-Url"], reverse("my-checklist-tasks") + "?view=kanban&search=Offer")
