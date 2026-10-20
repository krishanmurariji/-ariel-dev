"""
offboarding/exit_tasks.py

Exit tasks: the checklist a leaver's exit has to get through (return the
laptop, revoke access, hand over work...). HR builds Task Groups once; when
an exit starts, every group that applies to that person is turned into live
tasks for that exit.

The shape, as in Keka: each task has an *owner* who does it, an optional
*approver* who checks it, a *trigger date* (relative to the last working day)
when it lands on the owner's list, and a due date. HR can remind, reassign or
skip a task. Nothing is deleted - cancelled and skipped tasks stay on record.

Like exit_service.py this module knows nothing about screens.
"""

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from offboarding import exit_service
from offboarding.exit_service import ExitError, _clean_comment
from offboarding.models import ExitTask, ExitTaskConfig, OffboardingEmployee

REMINDER_GAP = timedelta(hours=1)


def _all(model):
    return model.objects.entire()


# --------------------------------------------------------------------------
# who is on a task
# --------------------------------------------------------------------------


def _person(kind, specific, employee):
    if kind == "employee":
        return employee
    if kind == "reporting_manager":
        return employee.get_reporting_manager()
    if kind == "specific":
        return specific
    return None


def _config_of(template_task):
    """The task's exit settings, or sensible defaults if none were saved."""
    config = getattr(template_task, "exit_config", None)
    return config or ExitTaskConfig(task_id=template_task)


def _department_of(employee):
    info = getattr(employee, "employee_work_info", None)
    return getattr(info, "department_id", None)


def _company_id_of(employee):
    info = getattr(employee, "employee_work_info", None)
    return getattr(info, "company_id_id", None)


# --------------------------------------------------------------------------
# which groups apply
# --------------------------------------------------------------------------


def applicable_groups(case):
    """Offboarding Task Groups that apply to this person's exit."""
    from onboarding.models import TaskTemplate

    employee = case.employee_id
    company_id = _company_id_of(employee)
    department = _department_of(employee)
    groups = []
    candidates = (
        _all(TaskTemplate)
        .filter(phase="offboarding")
        .select_related("exit_rule")
        .prefetch_related("tasks__exit_config")
        .order_by("title")
    )
    for group in candidates:
        if group.company_id_id and group.company_id_id != company_id:
            continue
        rule = getattr(group, "exit_rule", None)
        if rule is not None:
            if not rule.is_active:
                continue
            if rule.exit_type not in ("all", case.exit_type):
                continue
            included = set(rule.include_departments.values_list("pk", flat=True))
            excluded = set(rule.exclude_departments.values_list("pk", flat=True))
            department_id = department.pk if department else None
            if included and department_id not in included:
                continue
            if department_id in excluded:
                continue
        groups.append(group)
    return groups


# --------------------------------------------------------------------------
# creating and timing the tasks
# --------------------------------------------------------------------------


def _trigger_for(case, config):
    base = case.last_working_day or date.today()
    return base + timedelta(days=config.trigger_offset_days)


@transaction.atomic
def create_tasks(case):
    """
    Turn every applicable group into tasks for this exit. Any open tasks from
    an earlier exit of the same person are cancelled first, so a re-exit
    starts clean. Returns how many tasks were created.
    """
    cancel_open_tasks(case)
    employee = case.employee_id
    created = 0
    for group in applicable_groups(case):
        for template_task in group.tasks.all():
            config = _config_of(template_task)
            trigger = _trigger_for(case, config)
            ExitTask.objects.create(
                case_id=case,
                template_task_id=template_task,
                group_title=group.title,
                title=template_task.title,
                description=template_task.description or "",
                sequence=template_task.sequence,
                owner_id=_person(config.owner_type, config.owner_person, employee),
                approver_id=_person(
                    config.approver_type, config.approver_person, employee
                ),
                trigger_date=trigger,
                due_date=trigger + timedelta(days=config.due_after_days),
                mandatory_attachment=config.mandatory_attachment,
            )
            created += 1
    trigger_due(case=case)
    return created


def cancel_open_tasks(case):
    return ExitTask.objects.entire().filter(
        case_id=case, status__in=ExitTask.OPEN
    ).update(status="cancelled")


@transaction.atomic
def reschedule(case):
    """The last working day moved: re-time every task that has not fired yet."""
    for task in ExitTask.objects.entire().filter(case_id=case, status="scheduled"):
        config = _config_of(task.template_task_id) if task.template_task_id else ExitTaskConfig()
        task.trigger_date = _trigger_for(case, config)
        task.due_date = task.trigger_date + timedelta(days=config.due_after_days)
        task.save()
    trigger_due(case=case)


def trigger_due(today=None, case=None):
    """
    Hand every scheduled task whose trigger date has come to its owner.
    Run when tasks are created and by the daily job. Returns how many fired.
    """
    today = today or date.today()
    due = ExitTask.objects.entire().filter(
        status="scheduled",
        trigger_date__lte=today,
        case_id__status="in_progress",
    )
    if case is not None:
        due = due.filter(case_id=case)
    fired = 0
    for task in due.select_related("owner_id", "case_id__employee_id"):
        task.status = "pending"
        task.save()
        if task.owner_id:
            exit_service._tell(
                task.owner_id,
                None,
                _("Exit task for %(name)s: %(task)s (due %(due)s)")
                % {
                    "name": task.case_id.employee_id.get_full_name(),
                    "task": task.title,
                    "due": task.due_date.strftime("%d %b %Y"),
                },
            )
        fired += 1
    return fired


# --------------------------------------------------------------------------
# who may do what
# --------------------------------------------------------------------------


def _is(user, employee):
    return bool(employee and employee.employee_user_id_id == getattr(user, "pk", None))


def can_do(user, task):
    """The owner (or HR) may complete a pending task."""
    return task.status == "pending" and (
        _is(user, task.owner_id) or exit_service.can_manage_exits(user)
    )


def can_review(user, task):
    """The approver (or HR) may approve a task waiting for approval."""
    return task.status == "review" and (
        _is(user, task.approver_id) or exit_service.can_manage_exits(user)
    )


# --------------------------------------------------------------------------
# doing the work
# --------------------------------------------------------------------------


def _open(task):
    return ExitTask.objects.entire().select_for_update().get(pk=task.pk)


@transaction.atomic
def complete_task(task, by, note="", attachment=None, user=None):
    task = _open(task)
    if task.status != "pending":
        raise ExitError(_("This task is not waiting to be done."))
    if user is not None and not can_do(user, task):
        raise ExitError(_("You can't complete this task."))
    note = _clean_comment(note, required=False)
    if attachment:
        task.attachment = attachment
    if task.mandatory_attachment and not task.attachment:
        raise ExitError(_("Please attach the required file."))
    task.note = note
    task.done_by = by
    task.done_at = timezone.now()
    if task.approver_id and task.approver_id_id != getattr(by, "pk", None):
        task.status = "review"
        task.save()
        exit_service._tell(
            task.approver_id,
            by,
            _("Please approve the exit task '%(task)s' for %(name)s.")
            % {"task": task.title, "name": task.case_id.employee_id.get_full_name()},
        )
    else:
        task.status = "done"
        task.save()
    exit_service.log_activity(
        task.case_id.employee_id,
        "task_done",
        by,
        exit_service._letter_of(task.case_id),
        comment=note,
        to_value=task.title[:100],
    )
    return task


@transaction.atomic
def review_task(task, by, approve, comment="", user=None):
    task = _open(task)
    if task.status != "review":
        raise ExitError(_("This task is not waiting for approval."))
    if user is not None and not can_review(user, task):
        raise ExitError(_("You can't approve this task."))
    comment = _clean_comment(comment, required=not approve, label=_("A comment"))
    task.reviewed_by = by
    task.review_comment = comment
    task.status = "done" if approve else "pending"
    task.save()
    exit_service.log_activity(
        task.case_id.employee_id,
        "task_approved" if approve else "task_rejected",
        by,
        exit_service._letter_of(task.case_id),
        comment=comment,
        to_value=task.title[:100],
    )
    if not approve and task.owner_id:
        exit_service._tell(
            task.owner_id,
            by,
            _("The exit task '%(task)s' was sent back: %(why)s")
            % {"task": task.title, "why": comment},
        )
    return task


@transaction.atomic
def skip_task(task, by, reason):
    task = _open(task)
    if task.status not in ExitTask.OPEN:
        raise ExitError(_("This task can no longer be skipped."))
    reason = _clean_comment(reason, required=True, label=_("A reason"))
    task.status = "skipped"
    task.skipped_reason = reason
    task.save()
    exit_service.log_activity(
        task.case_id.employee_id,
        "task_skipped",
        by,
        exit_service._letter_of(task.case_id),
        comment=reason,
        to_value=task.title[:100],
    )
    return task


@transaction.atomic
def remind_task(task, by):
    """Nudge whoever the task is waiting on (at most once an hour)."""
    task = _open(task)
    if task.status not in ("pending", "review"):
        raise ExitError(_("Only a task that is waiting on someone can be chased."))
    target = task.owner_id if task.status == "pending" else task.approver_id
    if target is None:
        raise ExitError(_("This task has no one to remind."))
    now = timezone.now()
    if task.last_reminded_at and now - task.last_reminded_at < REMINDER_GAP:
        raise ExitError(_("A reminder was sent less than an hour ago."))
    task.last_reminded_at = now
    task.save()
    exit_service._tell(
        target,
        by,
        _("Reminder: exit task '%(task)s' for %(name)s is due %(due)s.")
        % {
            "task": task.title,
            "name": task.case_id.employee_id.get_full_name(),
            "due": task.due_date.strftime("%d %b %Y"),
        },
    )
    return target


@transaction.atomic
def reassign_owner(task, new_owner, by):
    task = _open(task)
    if task.status not in ("scheduled", "pending"):
        raise ExitError(_("Only a task that has not been done yet can change hands."))
    if new_owner is None or not new_owner.is_active:
        raise ExitError(_("Please choose an active employee."))
    old = task.owner_id
    task.owner_id = new_owner
    task.save()
    exit_service.log_activity(
        task.case_id.employee_id,
        "task_owner",
        by,
        exit_service._letter_of(task.case_id),
        from_value=old.get_full_name() if old else "",
        to_value=new_owner.get_full_name(),
        comment=task.title,
    )
    if task.status == "pending":
        exit_service._tell(
            new_owner,
            by,
            _("Exit task for %(name)s: %(task)s")
            % {"name": task.case_id.employee_id.get_full_name(), "task": task.title},
        )
    return task


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def tasks_of(case, include_cancelled=False):
    rows = ExitTask.objects.entire().filter(case_id=case).select_related(
        "owner_id", "approver_id"
    )
    if not include_cancelled:
        rows = rows.exclude(status="cancelled")
    return rows.order_by("trigger_date", "sequence", "pk")


def progress(case):
    """(finished, total) over the tasks that count (not cancelled)."""
    rows = list(tasks_of(case).values_list("status", flat=True))
    return sum(1 for s in rows if s in ("done", "skipped")), len(rows)


def tasks_state(case):
    """grey = not started, yellow = under way, green = all finished (or none)."""
    finished, total = progress(case)
    if total == 0 or finished == total:
        return "done"
    started = tasks_of(case).filter(status__in=["review", "done", "skipped"]).exists()
    return "doing" if started else "todo"


def my_tasks(employee):
    """What is waiting on this person: tasks to do and tasks to approve."""
    base = ExitTask.objects.entire().filter(
        case_id__status="in_progress"
    ).select_related("case_id__employee_id", "owner_id", "approver_id")
    to_do = base.filter(owner_id=employee, status="pending")
    to_approve = base.filter(approver_id=employee, status="review")
    return list(to_do.order_by("due_date")), list(to_approve.order_by("due_date"))


def has_work(employee):
    todo, approve = my_tasks(employee)
    return bool(todo or approve)
