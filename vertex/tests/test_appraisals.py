"""
vertex/tests/test_appraisals.py

Appraisal cycles, the appraisal flow (self -> manager -> release) and goals, ported from ArielVertex
with the gaps fixed: a closed cycle stays closed, ratings are not added to a closed cycle, nobody
evaluates or releases their own appraisal, and the employee sees the manager's side only after release.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from employee.models import EmployeeWorkInformation
from horilla.horilla_middlewares import _thread_locals, set_selected_company
from vertex import access, appraisals
from vertex.models import (
    Appraisal,
    AppraisalCycle,
    AppraisalStage,
    CycleStatus,
    Goal,
    GoalStatus,
)
from vertex.tests.test_projects import HX, VertexTestBase, grant, make_employee

DENIED = "You Dont Have Permission"
SELF = {"self_rating": "4", "self_comments": "Shipped the billing module."}
MANAGER = {"manager_rating": "3.5", "manager_comments": "Good delivery, needs more tests."}


def report_to(employee, manager):
    EmployeeWorkInformation.objects.update_or_create(
        employee_id=employee, defaults={"reporting_manager_id": manager}
    )


def make_cycle(name="H1 2027", status=CycleStatus.DRAFT):
    return AppraisalCycle.objects.create(
        name=name, start_date=date(2027, 1, 1), end_date=date(2027, 6, 30), status=status
    )


class AppraisalBase(VertexTestBase):
    @classmethod
    def setUpTestData(cls):
        # a request left over from an earlier test would be recorded as the creator of new employees
        _thread_locals.request = None
        set_selected_company(None)
        cls.hr = make_employee("Hema", "hr@a.example", "9400000001")
        cls.manager = make_employee("Manu", "mgr@a.example", "9400000002")
        cls.dev = make_employee("Dev", "dev@a.example", "9400000003")
        cls.other = make_employee("Other", "other@a.example", "9400000004")
        cls.boss = make_employee("Boss", "boss@a.example", "9400000005")
        report_to(cls.dev, cls.manager)
        report_to(cls.manager, cls.hr)
        grant(
            cls.hr, "view_appraisalcycle", "add_appraisalcycle", "change_appraisalcycle", "view_appraisal",
            "change_appraisal", "view_allappraisals", "change_appraisalrelease", "view_goal", "add_goal",
            "change_goal", "delete_goal", "view_allgoals",
        )
        grant(cls.manager, "view_appraisal", "change_appraisal", "view_goal", "add_goal", "change_goal", "delete_goal")
        grant(cls.dev, "view_appraisal", "view_goal")
        grant(cls.other, "view_appraisal", "view_goal")
        grant(cls.boss, "view_appraisal", "view_allappraisals", "view_goal", "view_allgoals", "view_appraisalcycle")

    def open_cycle(self, name="H1 2027"):
        cycle = make_cycle(name)
        appraisals.activate_cycle(cycle, self.hr)
        return cycle

    def appraisal_of(self, employee, cycle=None):
        return Appraisal.objects.get(employee=employee, cycle=cycle or AppraisalCycle.objects.get())


class CycleTests(AppraisalBase):
    def test_activation_opens_one_appraisal_per_active_employee_with_their_manager(self):
        cycle = self.open_cycle()
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, CycleStatus.ACTIVE)
        self.assertEqual(self.appraisal_of(self.dev).manager, self.manager)
        self.assertIsNone(self.appraisal_of(self.other).manager)
        self.assertEqual(self.appraisal_of(self.dev).stage, AppraisalStage.SELF_PENDING)

    def test_activating_again_adds_only_the_people_who_joined_later(self):
        cycle = self.open_cycle()
        before = Appraisal.objects.count()
        self.assertEqual(appraisals.activate_cycle(cycle, self.hr), [])
        newcomer = make_employee("New", "new@a.example", "9400000009")
        created = appraisals.activate_cycle(cycle, self.hr)
        self.assertEqual([a.employee for a in created], [newcomer])
        self.assertEqual(Appraisal.objects.count(), before + 1)

    def test_a_closed_cycle_cannot_be_opened_again_and_only_an_active_one_closes(self):
        cycle = make_cycle()
        with self.assertRaises(ValueError):
            appraisals.close_cycle(cycle)  # still a draft
        appraisals.activate_cycle(cycle, self.hr)
        appraisals.close_cycle(cycle)
        with self.assertRaises(ValueError):
            appraisals.activate_cycle(cycle, self.hr)

    def test_people_are_told_when_the_cycle_opens(self):
        self.open_cycle()
        self.assertTrue(Notification.objects.filter(recipient=self.dev.employee_user_id).exists())

    def test_hr_creates_and_edits_a_cycle(self):
        client = self.login(self.hr)
        client.post(reverse("vertex-cycle-new"), {"name": "H2 2027", "start_date": "2027-07-01", "end_date": "2027-12-31"}, **HX)
        cycle = AppraisalCycle.objects.get(name="H2 2027")
        self.assertEqual((cycle.status, cycle.created_by), (CycleStatus.DRAFT, self.hr))
        client.post(reverse("vertex-cycle-edit", args=[cycle.pk]), {"name": "H2 FY27", "start_date": "2027-07-01", "end_date": "2027-12-31"}, **HX)
        cycle.refresh_from_db()
        self.assertEqual(cycle.name, "H2 FY27")

    def test_cycle_form_rejects_bad_dates_and_a_repeated_name(self):
        make_cycle("Taken")
        client = self.login(self.hr)
        client.post(reverse("vertex-cycle-new"), {"name": "Back", "start_date": "2027-07-01", "end_date": "2027-01-01"}, **HX)
        client.post(reverse("vertex-cycle-new"), {"name": "taken", "start_date": "2027-01-01", "end_date": "2027-02-01"}, **HX)
        self.assertEqual(AppraisalCycle.objects.count(), 1)

    def test_a_closed_cycle_cannot_be_edited(self):
        cycle = make_cycle(status=CycleStatus.CLOSED)
        response = self.login(self.hr).get(reverse("vertex-cycle-edit", args=[cycle.pk]), **HX)
        self.assertContains(response, DENIED)

    def test_activate_and_close_are_post_only_and_need_the_permission(self):
        cycle = make_cycle()
        url = reverse("vertex-cycle-activate", args=[cycle.pk])
        self.assertEqual(self.login(self.hr).get(url).status_code, 405)
        self.login(self.dev).post(url)
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, CycleStatus.DRAFT)
        self.login(self.hr).post(url)
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, CycleStatus.ACTIVE)
        self.login(self.hr).post(reverse("vertex-cycle-close", args=[cycle.pk]))
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, CycleStatus.CLOSED)

    def test_only_cycle_managers_see_the_cycles_page(self):
        self.assertEqual(self.login(self.hr).get(reverse("vertex-cycles")).status_code, 200)
        self.assertEqual(self.login(self.boss).get(reverse("vertex-cycles")).status_code, 200)
        self.assertNotEqual(self.login(self.dev).get(reverse("vertex-cycles")).status_code, 200)
        listing = self.login(self.boss).get(reverse("vertex-cycle-list"), **HX)
        self.assertNotContains(listing, "/activate/")  # read only: no Activate / Close / Edit


class AppraisalFlowTests(AppraisalBase):
    def setUp(self):
        super().setUp()
        self.cycle = self.open_cycle()
        self.appraisal = self.appraisal_of(self.dev)

    def refresh(self):
        self.appraisal.refresh_from_db()
        return self.appraisal

    # -- who sees which appraisals

    def test_visibility(self):
        mine = lambda who: set(access.visible_appraisals(who.employee_user_id).values_list("employee_id", flat=True))
        self.assertEqual(mine(self.dev), {self.dev.pk})
        self.assertEqual(mine(self.manager), {self.dev.pk, self.manager.pk})
        self.assertEqual(mine(self.hr), set(Appraisal.objects.values_list("employee_id", flat=True)))
        self.assertEqual(mine(self.boss), mine(self.hr))
        nobody = make_employee("Nobody", "nobody@a.example", "9400000010")
        self.assertEqual(mine(nobody), set())

    def test_list_and_open_are_scoped(self):
        listing = self.login(self.dev).get(reverse("vertex-appraisal-list"), **HX)
        self.assertContains(listing, "Dev")
        other = self.appraisal_of(self.manager)
        self.assertNotContains(listing, other.get_absolute_url())
        self.assertEqual(self.login(self.dev).get(other.get_absolute_url()).status_code, 404)

    # -- self assessment

    def test_employee_submits_the_self_assessment_and_the_manager_is_told(self):
        self.login(self.dev).post(self.appraisal.get_self_url(), SELF, **HX)
        appraisal = self.refresh()
        self.assertEqual(appraisal.stage, AppraisalStage.SELF_SUBMITTED)
        self.assertEqual(appraisal.self_rating, Decimal("4.0"))
        self.assertIsNotNone(appraisal.self_submitted_at)
        self.assertTrue(Notification.objects.filter(recipient=self.manager.employee_user_id).exists())

    def test_self_assessment_is_only_for_the_owner_and_only_once(self):
        self.assertContains(self.login(self.other).get(self.appraisal.get_self_url(), **HX), DENIED)
        self.assertContains(self.login(self.manager).get(self.appraisal.get_self_url(), **HX), DENIED)
        self.login(self.dev).post(self.appraisal.get_self_url(), SELF, **HX)
        self.login(self.dev).post(self.appraisal.get_self_url(), {"self_rating": "1", "self_comments": "again"}, **HX)
        self.assertEqual(self.refresh().self_rating, Decimal("4.0"))

    def test_rating_must_be_between_1_and_5_in_half_steps(self):
        for bad in ("0", "6", "4.3", ""):
            self.login(self.dev).post(self.appraisal.get_self_url(), {"self_rating": bad, "self_comments": "x"}, **HX)
            self.assertEqual(self.refresh().stage, AppraisalStage.SELF_PENDING, bad)
        self.login(self.dev).post(self.appraisal.get_self_url(), {"self_rating": "4", "self_comments": "  "}, **HX)
        self.assertEqual(self.refresh().stage, AppraisalStage.SELF_PENDING)

    def test_no_self_assessment_in_a_closed_cycle(self):
        appraisals.close_cycle(self.cycle)
        self.assertContains(self.login(self.dev).get(self.appraisal.get_self_url(), **HX), DENIED)

    # -- manager evaluation

    def submit_self(self):
        self.login(self.dev).post(self.appraisal.get_self_url(), SELF, **HX)

    def test_manager_waits_for_the_self_assessment(self):
        self.assertContains(self.login(self.manager).get(self.appraisal.get_manager_url(), **HX), DENIED)
        self.submit_self()
        self.assertNotContains(self.login(self.manager).get(self.appraisal.get_manager_url(), **HX), DENIED)

    def test_manager_evaluates_and_hr_is_told(self):
        self.submit_self()
        self.login(self.manager).post(self.appraisal.get_manager_url(), MANAGER, **HX)
        appraisal = self.refresh()
        self.assertEqual((appraisal.stage, appraisal.manager_rating), (AppraisalStage.MANAGER_COMPLETED, Decimal("3.5")))
        self.assertTrue(Notification.objects.filter(recipient=self.hr.employee_user_id).exists())

    def test_only_the_assigned_manager_or_hr_evaluates(self):
        self.submit_self()
        grant(self.other, "change_appraisal")
        self.assertContains(self.login(self.other).get(self.appraisal.get_manager_url(), **HX), DENIED)
        self.assertContains(self.login(self.dev).get(self.appraisal.get_manager_url(), **HX), DENIED)
        self.assertNotContains(self.login(self.hr).get(self.appraisal.get_manager_url(), **HX), DENIED)

    def test_nobody_evaluates_their_own_appraisal(self):
        mine = self.appraisal_of(self.manager)
        mine.stage = AppraisalStage.SELF_SUBMITTED
        mine.manager = self.manager
        mine.save()
        self.assertContains(self.login(self.manager).get(mine.get_manager_url(), **HX), DENIED)

    def test_evaluation_can_be_revised_until_release_but_not_after(self):
        self.submit_self()
        client = self.login(self.manager)
        client.post(self.appraisal.get_manager_url(), MANAGER, **HX)
        client.post(self.appraisal.get_manager_url(), {"manager_rating": "4.5", "manager_comments": "Revised."}, **HX)
        self.assertEqual(self.refresh().manager_rating, Decimal("4.5"))
        self.login(self.hr).post(self.appraisal.get_release_url(), {"final_rating": "4"}, **HX)
        self.assertContains(client.get(self.appraisal.get_manager_url(), **HX), DENIED)

    # -- release

    def evaluated(self):
        self.submit_self()
        self.login(self.manager).post(self.appraisal.get_manager_url(), MANAGER, **HX)

    def test_release_needs_the_evaluation_first(self):
        self.assertContains(self.login(self.hr).get(self.appraisal.get_release_url(), **HX), DENIED)
        self.submit_self()
        self.assertContains(self.login(self.hr).get(self.appraisal.get_release_url(), **HX), DENIED)
        self.evaluated_after_self = self.login(self.manager).post(self.appraisal.get_manager_url(), MANAGER, **HX)
        self.assertNotContains(self.login(self.hr).get(self.appraisal.get_release_url(), **HX), DENIED)

    def test_release_publishes_the_final_rating_and_tells_the_employee(self):
        self.evaluated()
        form = self.login(self.hr).get(self.appraisal.get_release_url(), **HX)
        self.assertContains(form, "3.5")  # starts from the manager's rating
        self.login(self.hr).post(self.appraisal.get_release_url(), {"final_rating": "4", "final_comments": "Well done"}, **HX)
        appraisal = self.refresh()
        self.assertEqual((appraisal.stage, appraisal.final_rating), (AppraisalStage.RELEASED, Decimal("4.0")))
        self.assertIsNotNone(appraisal.released_at)
        self.assertTrue(Notification.objects.filter(recipient=self.dev.employee_user_id, verb__contains="Final rating: 4").exists())

    def test_only_release_holders_release_and_not_their_own(self):
        self.evaluated()
        self.assertContains(self.login(self.manager).get(self.appraisal.get_release_url(), **HX), DENIED)
        self.assertContains(self.login(self.boss).get(self.appraisal.get_release_url(), **HX), DENIED)
        mine = self.appraisal_of(self.hr)
        mine.stage = AppraisalStage.MANAGER_COMPLETED
        mine.save()
        self.assertContains(self.login(self.hr).get(mine.get_release_url(), **HX), DENIED)

    def test_no_release_in_a_closed_cycle(self):
        self.evaluated()
        appraisals.close_cycle(self.cycle)
        self.assertContains(self.login(self.hr).get(self.appraisal.get_release_url(), **HX), DENIED)

    # -- what the employee sees

    def test_employee_does_not_see_the_manager_side_until_release(self):
        self.evaluated()
        page = self.login(self.dev).get(self.appraisal.get_absolute_url())
        self.assertContains(page, SELF["self_comments"])
        self.assertNotContains(page, MANAGER["manager_comments"])
        self.assertNotContains(page, "3.5 / 5")
        self.login(self.hr).post(self.appraisal.get_release_url(), {"final_rating": "4"}, **HX)
        page = self.login(self.dev).get(self.appraisal.get_absolute_url())
        self.assertContains(page, MANAGER["manager_comments"])
        self.assertContains(page, "4.0")

    def test_manager_and_hr_see_both_sides(self):
        self.evaluated()
        for who in (self.manager, self.hr, self.boss):
            page = self.login(who).get(self.appraisal.get_absolute_url())
            self.assertContains(page, MANAGER["manager_comments"], msg_prefix=who.employee_first_name)

    def test_row_actions_follow_the_stage_and_the_person(self):
        listing = lambda who: self.login(who).get(reverse("vertex-appraisal-list"), **HX).content.decode()
        self.assertIn("/self/", listing(self.dev))
        self.assertNotIn("/manager/", listing(self.manager))
        self.submit_self()
        self.assertNotIn("/self/", listing(self.dev))
        self.assertIn("/manager/", listing(self.manager))
        self.login(self.manager).post(self.appraisal.get_manager_url(), MANAGER, **HX)
        self.assertIn("/release/", listing(self.hr))
        self.assertNotIn("/release/", listing(self.manager))

    def test_cycle_filter_on_the_list(self):
        second = make_cycle("H2 2027")
        appraisals.activate_cycle(second, self.hr)
        page = self.login(self.hr).get(reverse("vertex-appraisal-list"), {"cycle": second.pk}, **HX)
        self.assertContains(page, "H2 2027")
        self.assertNotContains(page, "H1 2027")


class GoalTests(AppraisalBase):
    def goal_data(self, employee, **extra):
        data = {
            "employee": employee.pk, "title": "Ship billing", "category": "Delivery", "weightage": "40",
            "target_date": "2027-06-30", "description": "End to end.",
        }
        data.update(extra)
        return data

    def test_manager_assigns_a_goal_to_a_report_and_the_report_is_told(self):
        self.login(self.manager).post(reverse("vertex-goal-new"), self.goal_data(self.dev), **HX)
        goal = Goal.objects.get()
        self.assertEqual((goal.employee, goal.assigned_by, goal.status, goal.progress), (self.dev, self.manager, GoalStatus.NOT_STARTED, 0))
        self.assertTrue(Notification.objects.filter(recipient=self.dev.employee_user_id).exists())

    def test_manager_cannot_assign_to_someone_who_does_not_report_to_them(self):
        self.login(self.manager).post(reverse("vertex-goal-new"), self.goal_data(self.other), **HX)
        self.assertFalse(Goal.objects.exists())
        self.login(self.manager).post(reverse("vertex-goal-new"), self.goal_data(self.manager), **HX)
        self.assertFalse(Goal.objects.exists())

    def test_hr_assigns_to_anyone(self):
        self.login(self.hr).post(reverse("vertex-goal-new"), self.goal_data(self.other), **HX)
        self.assertEqual(Goal.objects.get().employee, self.other)

    def test_a_developer_cannot_assign_goals(self):
        self.assertContains(self.login(self.dev).get(reverse("vertex-goal-new"), **HX), DENIED)
        self.login(self.dev).post(reverse("vertex-goal-new"), self.goal_data(self.dev), **HX)
        self.assertFalse(Goal.objects.exists())

    def test_weights_of_one_persons_goals_in_a_cycle_stay_within_100(self):
        client = self.login(self.manager)
        client.post(reverse("vertex-goal-new"), self.goal_data(self.dev, weightage="60"), **HX)
        client.post(reverse("vertex-goal-new"), self.goal_data(self.dev, title="Too much", weightage="50"), **HX)
        self.assertEqual(Goal.objects.count(), 1)
        client.post(reverse("vertex-goal-new"), self.goal_data(self.dev, title="Fits", weightage="40"), **HX)
        self.assertEqual(Goal.objects.count(), 2)

    def test_cancelled_goals_and_other_cycles_do_not_use_up_weight(self):
        cycle = make_cycle()
        Goal.objects.create(employee=self.dev, title="Old", weightage=80, target_date=date(2027, 1, 1), status=GoalStatus.CANCELLED)
        Goal.objects.create(employee=self.dev, title="Other cycle", weightage=90, target_date=date(2027, 1, 1), cycle=cycle)
        self.login(self.manager).post(reverse("vertex-goal-new"), self.goal_data(self.dev, weightage="100"), **HX)
        self.assertEqual(Goal.objects.filter(title="Ship billing").count(), 1)

    def test_editing_a_goal_counts_the_other_goals_only(self):
        first = Goal.objects.create(employee=self.dev, title="A", weightage=50, target_date=date(2027, 1, 1), assigned_by=self.manager)
        Goal.objects.create(employee=self.dev, title="B", weightage=40, target_date=date(2027, 1, 1))
        client = self.login(self.manager)
        client.post(reverse("vertex-goal-edit", args=[first.pk]), self.goal_data(self.dev, title="A", weightage="60"), **HX)
        first.refresh_from_db()
        self.assertEqual(first.weightage, 60)
        client.post(reverse("vertex-goal-edit", args=[first.pk]), self.goal_data(self.dev, title="A", weightage="61"), **HX)
        first.refresh_from_db()
        self.assertEqual(first.weightage, 60)

    def test_owner_updates_progress_and_full_progress_completes_the_goal(self):
        goal = Goal.objects.create(employee=self.dev, title="G", weightage=10, target_date=date(2027, 1, 1))
        url = reverse("vertex-goal-progress", args=[goal.pk])
        client = self.login(self.dev)
        client.post(url, {"progress": "30", "status": "not_started"}, **HX)
        goal.refresh_from_db()
        self.assertEqual((goal.progress, goal.status), (30, GoalStatus.IN_PROGRESS))
        client.post(url, {"progress": "100", "status": "in_progress"}, **HX)
        goal.refresh_from_db()
        self.assertEqual((goal.progress, goal.status), (100, GoalStatus.COMPLETED))

    def test_marking_a_goal_completed_sets_it_to_100_and_cancelled_keeps_its_progress(self):
        goal = Goal.objects.create(employee=self.dev, title="G", weightage=10, target_date=date(2027, 1, 1), progress=40)
        url = reverse("vertex-goal-progress", args=[goal.pk])
        self.login(self.dev).post(url, {"progress": "40", "status": "completed"}, **HX)
        goal.refresh_from_db()
        self.assertEqual((goal.progress, goal.status), (100, GoalStatus.COMPLETED))
        self.login(self.manager).post(url, {"progress": "100", "status": "cancelled"}, **HX)
        goal.refresh_from_db()
        self.assertEqual(goal.status, GoalStatus.CANCELLED)

    def test_progress_is_limited_to_the_owner_and_their_manager(self):
        goal = Goal.objects.create(employee=self.dev, title="G", weightage=10, target_date=date(2027, 1, 1))
        url = reverse("vertex-goal-progress", args=[goal.pk])
        self.assertEqual(self.login(self.other).get(url, **HX).content.decode().count(DENIED), 1)
        self.login(self.other).post(url, {"progress": "50", "status": "in_progress"}, **HX)
        goal.refresh_from_db()
        self.assertEqual(goal.progress, 0)
        self.login(self.manager).post(url, {"progress": "50", "status": "in_progress"}, **HX)
        goal.refresh_from_db()
        self.assertEqual(goal.progress, 50)

    def test_progress_beyond_100_is_rejected(self):
        goal = Goal.objects.create(employee=self.dev, title="G", weightage=10, target_date=date(2027, 1, 1))
        self.login(self.dev).post(reverse("vertex-goal-progress", args=[goal.pk]), {"progress": "150", "status": "in_progress"}, **HX)
        goal.refresh_from_db()
        self.assertEqual(goal.progress, 0)

    def test_goal_visibility(self):
        mine = Goal.objects.create(employee=self.dev, title="Dev goal", weightage=10, target_date=date(2027, 1, 1))
        theirs = Goal.objects.create(employee=self.other, title="Other goal", weightage=10, target_date=date(2027, 1, 1))
        ids = lambda who: set(access.visible_goals(who.employee_user_id).values_list("pk", flat=True))
        self.assertEqual(ids(self.dev), {mine.pk})
        self.assertEqual(ids(self.manager), {mine.pk})
        self.assertEqual(ids(self.hr), {mine.pk, theirs.pk})
        self.assertEqual(ids(self.boss), {mine.pk, theirs.pk})

    def test_edit_and_delete_belong_to_the_manager_and_hr(self):
        goal = Goal.objects.create(employee=self.dev, title="G", weightage=10, target_date=date(2027, 1, 1))
        self.assertContains(self.login(self.dev).get(reverse("vertex-goal-edit", args=[goal.pk]), **HX), DENIED)
        self.login(self.dev).post(goal.get_delete_url())
        self.login(self.other).post(goal.get_delete_url())
        self.assertTrue(Goal.objects.exists())
        self.login(self.manager).post(goal.get_delete_url())
        self.assertFalse(Goal.objects.exists())

    def test_leadership_reads_but_cannot_change_goals(self):
        goal = Goal.objects.create(employee=self.dev, title="G", weightage=10, target_date=date(2027, 1, 1))
        listing = self.login(self.boss).get(reverse("vertex-goal-list"), **HX)
        self.assertContains(listing, "G")
        self.assertNotContains(listing, "/delete/")
        self.assertNotContains(listing, "/edit/")
        self.assertContains(self.login(self.boss).get(reverse("vertex-goal-new"), **HX), DENIED)
        self.assertContains(self.login(self.boss).get(goal.get_progress_url(), **HX), DENIED)


class AppraisalPagesTests(AppraisalBase):
    def test_admin_passes_every_check(self):
        admin = make_employee("Admin", "admin@a.example", "9400000020")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        cycle = self.open_cycle()
        appraisal = self.appraisal_of(self.dev, cycle)
        client = self.login(admin)
        for url in (
            reverse("vertex-cycles"), reverse("vertex-appraisals"), reverse("vertex-goals"),
            appraisal.get_absolute_url(),
        ):
            self.assertEqual(client.get(url).status_code, 200, url)
        for url in (
            reverse("vertex-cycle-nav"), reverse("vertex-cycle-list"), reverse("vertex-cycle-new"),
            reverse("vertex-cycle-edit", args=[cycle.pk]), reverse("vertex-appraisal-nav"),
            reverse("vertex-appraisal-list"), reverse("vertex-goal-nav"), reverse("vertex-goal-list"),
            reverse("vertex-goal-new"),
        ):
            response = client.get(url, **HX)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, DENIED, msg_prefix=url)

    def test_pages_render_in_the_vertex_design_and_list_columns(self):
        cycle = self.open_cycle()
        Goal.objects.create(employee=self.dev, title="Ship it", category="Delivery", weightage=10, target_date=date(2027, 6, 1), cycle=cycle)
        hr = self.login(self.hr)
        for url in (reverse("vertex-cycle-new"), reverse("vertex-goal-new"), self.appraisal_of(self.dev, cycle).get_self_url()):
            if url.endswith("/self/"):
                continue  # HR is not the employee
            self.assertContains(hr.get(url, **HX), "vm__head", msg_prefix=url)
        self.assertContains(hr.get(reverse("vertex-cycle-list"), **HX), "released")
        for label in ("Employee", "Goal", "Weight", "Progress", "Target date"):
            self.assertContains(hr.get(reverse("vertex-goal-list"), **HX), label)
        self.assertNotContains(hr.get(reverse("vertex-goal-list"), **HX), "+00:00")

    def test_user_without_the_permissions_sees_none_of_it(self):
        nobody = make_employee("Nobody", "nobody2@a.example", "9400000021")
        client = self.login(nobody)
        for name in ("vertex-cycles", "vertex-appraisals", "vertex-goals"):
            self.assertNotEqual(client.get(reverse(name)).status_code, 200, name)

    def test_my_performance_lists_released_appraisals(self):
        grant(self.dev, "view_myperformance")
        cycle = self.open_cycle()
        appraisal = self.appraisal_of(self.dev, cycle)
        page = self.login(self.dev).get(reverse("vertex-my-performance"))
        self.assertNotContains(page, "H1 2027")
        Appraisal.objects.filter(pk=appraisal.pk).update(
            stage=AppraisalStage.RELEASED, final_rating=Decimal("4.5"), released_at=timezone.now()
        )
        page = self.login(self.dev).get(reverse("vertex-my-performance"))
        self.assertContains(page, "H1 2027")
        self.assertContains(page, "4.5")
