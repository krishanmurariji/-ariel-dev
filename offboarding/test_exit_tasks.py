"""
Exit tasks: which groups apply to an exit, creating and timing the tasks,
doing / approving / skipping / chasing them, and what the rest of the exit
process does to them (date changes, cancelling, the daily job).
"""

import datetime
import itertools

from django.core.files.uploadedfile import SimpleUploadedFile

from base.models import Department
from onboarding.models import TaskTemplate, TaskTemplateTask
from offboarding import exit_service, exit_tasks
from offboarding.exit_service import ExitError
from offboarding.models import (
    ExitActivity,
    ExitTask,
    ExitTaskConfig,
    ExitTaskGroupRule,
)
from offboarding.test_exit_pages import TODAY, PageBase, _letter, _person

DAY = datetime.timedelta
_n = itertools.count()


def make_group(title=None, tasks=(), **rule):
    """tasks: dicts of title + any ExitTaskConfig field."""
    group = TaskTemplate.objects.create(
        title=title or f"Group {next(_n)}", phase="offboarding"
    )
    for position, spec in enumerate(tasks):
        spec = dict(spec)
        task = TaskTemplateTask.objects.create(
            template_id=group, title=spec.pop("title"), sequence=position
        )
        ExitTaskConfig.objects.create(task_id=task, **spec)
    if rule:
        departments = {k: rule.pop(k) for k in ("include", "exclude") if k in rule}
        row = ExitTaskGroupRule.objects.create(template_id=group, **rule)
        if "include" in departments:
            row.include_departments.set(departments["include"])
        if "exclude" in departments:
            row.exclude_departments.set(departments["exclude"])
    return group


class TaskBase(PageBase):
    def setUp(self):
        super().setUp()
        self.it_user, self.it = _person()
        self.group = make_group(
            "IT clearance",
            [
                dict(title="Return laptop", owner_type="specific", owner_person=self.it,
                     trigger_offset_days=-7, due_after_days=3),
                dict(title="Exit interview", owner_type="reporting_manager",
                     approver_type="specific", approver_person=self.hr,
                     trigger_offset_days=-30, due_after_days=5),
                dict(title="Revoke access", owner_type="employee",
                     trigger_offset_days=0, mandatory_attachment=True),
            ],
        )
        self.case = self.accepted_case()  # emp: notice today, last day in 20 days

    def task(self, title, case=None):
        return ExitTask.objects.entire().get(case_id=case or self.case, title=title)


class ApplicableGroupTests(TaskBase):
    def groups(self, case=None):
        return [g.title for g in exit_tasks.applicable_groups(case or self.case)]

    def test_a_plain_group_applies_to_everyone(self):
        self.assertEqual(self.groups(), ["IT clearance"])

    def test_onboarding_groups_are_ignored(self):
        TaskTemplate.objects.create(title="Welcome kit", phase="post_joining")
        self.assertEqual(self.groups(), ["IT clearance"])

    def test_a_switched_off_group_does_not_apply(self):
        make_group("Off", [dict(title="x")], is_active=False)
        self.assertNotIn("Off", self.groups())

    def test_exit_type_rule(self):
        make_group("Only fired", [dict(title="x")], exit_type="termination")
        self.assertNotIn("Only fired", self.groups())
        self.case.exit_type = "termination"
        self.assertIn("Only fired", self.groups())

    def test_department_include_and_exclude(self):
        sales = Department.objects.create(department="Sales")
        ops = Department.objects.create(department="Ops")
        make_group("Sales only", [dict(title="x")], include=[sales])
        make_group("Not ops", [dict(title="x")], exclude=[ops])
        self.assertNotIn("Sales only", self.groups())  # emp has no department
        self.assertIn("Not ops", self.groups())
        info = self.emp.employee_work_info
        info.department_id = sales
        info.save()
        case = type(self.case).objects.entire().get(pk=self.case.pk)
        self.assertIn("Sales only", self.groups(case))
        info.department_id = ops
        info.save()
        case = type(self.case).objects.entire().get(pk=self.case.pk)
        self.assertNotIn("Not ops", self.groups(case))
        self.assertNotIn("Sales only", self.groups(case))


class CreatingTasksTests(TaskBase):
    def test_every_task_of_the_group_is_created_with_its_owner(self):
        self.assertEqual(ExitTask.objects.entire().filter(case_id=self.case).count(), 3)
        self.assertEqual(self.task("Return laptop").owner_id, self.it)
        self.assertEqual(self.task("Exit interview").owner_id, self.mgr)
        self.assertEqual(self.task("Exit interview").approver_id, self.hr)
        self.assertEqual(self.task("Revoke access").owner_id, self.emp)
        self.assertTrue(self.task("Revoke access").mandatory_attachment)

    def test_trigger_and_due_dates_are_relative_to_the_last_working_day(self):
        last = self.case.last_working_day
        laptop = self.task("Return laptop")
        self.assertEqual(laptop.trigger_date, last - DAY(days=7))
        self.assertEqual(laptop.due_date, last - DAY(days=4))

    def test_only_tasks_already_triggered_are_pending(self):
        self.assertEqual(self.task("Exit interview").status, "pending")  # 30 days before
        self.assertEqual(self.task("Return laptop").status, "scheduled")
        self.assertEqual(self.task("Revoke access").status, "scheduled")

    def test_a_task_with_no_resolvable_owner_is_kept_and_flagged(self):
        other_case = self.accepted_case(self.other)  # reports to nobody
        self.assertIsNone(self.task("Exit interview", other_case).owner_id)

    def test_a_task_without_saved_settings_gets_defaults(self):
        group = TaskTemplate.objects.create(title="Bare", phase="offboarding")
        TaskTemplateTask.objects.create(template_id=group, title="Bare task")
        third_user, third = _person(manager=self.mgr)
        case = self.accepted_case(third)
        bare = self.task("Bare task", case)
        self.assertEqual(bare.owner_id, self.mgr)  # default: reporting manager
        self.assertIsNone(bare.approver_id)

    def test_starting_again_cancels_the_old_open_tasks(self):
        exit_service.cancel_exit(self.case, self.hr, "changed mind")
        letter = _letter(self.emp)
        exit_service.review_request(
            letter, self.hr, "accept", "ok", last_working_day=TODAY + DAY(days=15)
        )
        rows = ExitTask.objects.entire().filter(case_id=self.case)
        self.assertEqual(rows.filter(status="cancelled").count(), 3)
        self.assertEqual(rows.exclude(status="cancelled").count(), 3)


class LifecycleTests(TaskBase):
    def test_moving_the_last_day_retimes_tasks_that_have_not_fired(self):
        before = self.task("Return laptop").trigger_date
        exit_service.change_last_working_day(
            self.case, self.case.last_working_day + DAY(days=10), "more handover", self.hr
        )
        self.assertEqual(self.task("Return laptop").trigger_date, before + DAY(days=10))

    def test_a_task_already_handed_out_keeps_its_dates(self):
        before = self.task("Exit interview")
        exit_service.change_last_working_day(
            self.case, self.case.last_working_day + DAY(days=10), "more handover", self.hr
        )
        after = self.task("Exit interview")
        self.assertEqual((after.trigger_date, after.due_date), (before.trigger_date, before.due_date))

    def test_moving_the_day_earlier_fires_tasks_that_are_now_due(self):
        exit_service.change_last_working_day(
            self.case, TODAY + DAY(days=2), "short notice agreed", self.hr
        )
        self.assertEqual(self.task("Return laptop").status, "pending")

    def test_cancelling_the_exit_cancels_open_tasks_but_keeps_finished_ones(self):
        interview = self.task("Exit interview")
        exit_tasks.skip_task(interview, self.hr, "not needed")
        exit_service.cancel_exit(self.case, self.hr, "stays")
        self.assertEqual(self.task("Exit interview").status, "skipped")
        self.assertEqual(self.task("Return laptop").status, "cancelled")

    def test_the_daily_job_hands_over_tasks_whose_day_has_come(self):
        laptop = self.task("Return laptop")
        self.assertEqual(exit_tasks.trigger_due(today=laptop.trigger_date - DAY(days=1)), 0)
        self.assertEqual(exit_tasks.trigger_due(today=laptop.trigger_date), 1)
        self.assertEqual(self.task("Return laptop").status, "pending")
        self.assertEqual(exit_tasks.trigger_due(today=laptop.trigger_date), 0)

    def test_nothing_fires_for_an_exit_that_is_not_in_progress(self):
        exit_service.cancel_exit(self.case, self.hr, "stays")
        self.assertEqual(exit_tasks.trigger_due(today=TODAY + DAY(days=90)), 0)


class DoingTheWorkTests(TaskBase):
    def file(self):
        return SimpleUploadedFile("proof.pdf", b"%PDF-1.4 test", content_type="application/pdf")

    def test_the_owner_completes_a_pending_task(self):
        task = self.task("Return laptop")
        exit_tasks.trigger_due(today=task.trigger_date)
        done = exit_tasks.complete_task(self.task("Return laptop"), self.it, "returned", user=self.it_user)
        self.assertEqual(done.status, "done")
        self.assertEqual(done.note, "returned")
        self.assertEqual(done.done_by, self.it)
        self.assertTrue(ExitActivity.objects.entire().filter(action="task_done").exists())

    def test_someone_else_cannot_complete_it_but_hr_can(self):
        task = self.task("Exit interview")
        with self.assertRaises(ExitError):
            exit_tasks.complete_task(task, self.emp, "", user=self.emp_user)
        done = exit_tasks.complete_task(task, self.hr, "", user=self.hr_user)
        self.assertEqual(done.status, "done")  # HR is also the approver: no second step

    def test_a_scheduled_task_cannot_be_completed_early(self):
        with self.assertRaises(ExitError):
            exit_tasks.complete_task(self.task("Return laptop"), self.it, "")

    def test_a_required_attachment_must_be_provided(self):
        task = self.task("Revoke access")
        exit_tasks.trigger_due(today=task.trigger_date)
        with self.assertRaises(ExitError):
            exit_tasks.complete_task(self.task("Revoke access"), self.emp, "done")
        done = exit_tasks.complete_task(
            self.task("Revoke access"), self.emp, "done", attachment=self.file()
        )
        self.assertEqual(done.status, "done")
        self.assertTrue(done.attachment)

    def test_script_in_a_note_is_refused(self):
        task = self.task("Exit interview")
        with self.assertRaises(ExitError):
            exit_tasks.complete_task(task, self.mgr, "<script>x()</script>")
        self.assertEqual(self.task("Exit interview").status, "pending")

    def test_approval_flow(self):
        exit_tasks.complete_task(self.task("Exit interview"), self.mgr, "held it")
        waiting = self.task("Exit interview")
        self.assertEqual(waiting.status, "review")
        with self.assertRaises(ExitError):  # sending back needs a comment
            exit_tasks.review_task(waiting, self.hr, approve=False, comment="")
        back = exit_tasks.review_task(waiting, self.hr, approve=False, comment="Add notes")
        self.assertEqual(back.status, "pending")
        self.assertEqual(back.review_comment, "Add notes")
        exit_tasks.complete_task(self.task("Exit interview"), self.mgr, "added notes")
        approved = exit_tasks.review_task(self.task("Exit interview"), self.hr, approve=True)
        self.assertEqual(approved.status, "done")

    def test_only_the_approver_or_hr_can_approve(self):
        exit_tasks.complete_task(self.task("Exit interview"), self.mgr, "")
        with self.assertRaises(ExitError):
            exit_tasks.review_task(self.task("Exit interview"), self.mgr, True, user=self.mgr_user)

    def test_an_owner_who_is_also_the_approver_needs_no_second_step(self):
        task = self.task("Exit interview")
        task.owner_id = self.hr
        task.save()
        self.assertEqual(exit_tasks.complete_task(task, self.hr, "").status, "done")

    def test_skipping_needs_a_reason_and_is_final(self):
        task = self.task("Return laptop")
        with self.assertRaises(ExitError):
            exit_tasks.skip_task(task, self.hr, "  ")
        skipped = exit_tasks.skip_task(task, self.hr, "contractor, no laptop")
        self.assertEqual(skipped.status, "skipped")
        with self.assertRaises(ExitError):
            exit_tasks.skip_task(skipped, self.hr, "again")

    def test_reminders_go_to_the_right_person_and_are_rate_limited(self):
        target = exit_tasks.remind_task(self.task("Exit interview"), self.hr)
        self.assertEqual(target, self.mgr)
        with self.assertRaises(ExitError):
            exit_tasks.remind_task(self.task("Exit interview"), self.hr)
        with self.assertRaises(ExitError):  # still scheduled - nobody to chase
            exit_tasks.remind_task(self.task("Return laptop"), self.hr)

    def test_reassigning_the_owner(self):
        task = self.task("Exit interview")
        moved = exit_tasks.reassign_owner(task, self.other, self.hr)
        self.assertEqual(moved.owner_id, self.other)
        self.assertTrue(ExitActivity.objects.entire().filter(action="task_owner").exists())
        self.other.is_active = False
        self.other.save()
        with self.assertRaises(ExitError):
            exit_tasks.reassign_owner(task, self.other, self.hr)
        exit_tasks.skip_task(task, self.hr, "n/a")
        with self.assertRaises(ExitError):
            exit_tasks.reassign_owner(task, self.it, self.hr)


class ProgressTests(TaskBase):
    def test_progress_and_icon_colour(self):
        self.assertEqual(exit_tasks.progress(self.case), (0, 3))
        self.assertEqual(exit_tasks.tasks_state(self.case), "todo")
        exit_tasks.skip_task(self.task("Return laptop"), self.hr, "n/a")
        self.assertEqual(exit_tasks.tasks_state(self.case), "doing")
        exit_tasks.skip_task(self.task("Exit interview"), self.hr, "n/a")
        exit_tasks.skip_task(self.task("Revoke access"), self.hr, "n/a")
        self.assertEqual(exit_tasks.progress(self.case), (3, 3))
        self.assertEqual(exit_tasks.tasks_state(self.case), "done")

    def test_no_tasks_at_all_counts_as_done(self):
        ExitTask.objects.entire().filter(case_id=self.case).update(status="cancelled")
        self.assertEqual(exit_tasks.tasks_state(self.case), "done")

    def test_my_tasks_lists_what_is_waiting_on_me(self):
        todo, approve = exit_tasks.my_tasks(self.mgr)
        self.assertEqual([t.title for t in todo], ["Exit interview"])
        self.assertEqual(approve, [])
        exit_tasks.complete_task(self.task("Exit interview"), self.mgr, "")
        self.assertEqual(exit_tasks.my_tasks(self.mgr), ([], []))
        todo, approve = exit_tasks.my_tasks(self.hr)
        self.assertEqual([t.title for t in approve], ["Exit interview"])
        self.assertTrue(exit_tasks.has_work(self.hr))
        self.assertFalse(exit_tasks.has_work(self.other))
