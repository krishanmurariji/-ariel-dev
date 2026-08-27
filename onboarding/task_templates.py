"""
task_templates.py

CRUD for Task Groups (TaskTemplate: a named, reusable set of onboarding
tasks, e.g. "Recruitment Survey") plus "My Checklist Tasks", where an
assignee acts on the CandidateChecklistTask rows created by the Start
Onboarding stepper.
"""

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from horilla.decorators import login_required, permission_required
from onboarding.forms import TaskTemplateForm, TaskTemplateTaskFormSet
from onboarding.models import CandidateChecklistTask, TaskTemplate
from onboarding.stepper import _maybe_mark_onboarding_complete


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("onboarding.view_tasktemplate"), name="dispatch")
class TaskTemplateListView(View):
    """
    Settings page listing every Task Group and its tasks.
    """

    def get(self, request):
        templates = TaskTemplate.objects.prefetch_related("tasks")
        return render(
            request, "onboarding/task_templates/list.html", {"templates": templates}
        )


def _lock_saved_task_forms(formset):
    """
    A TaskTemplateTask row that's already saved may already be referenced
    by in-progress onboarding checklists, so its content is frozen once
    created - only new rows are editable/removable. Disabling the fields
    (rather than just hiding them in the template) makes Django fall back
    to each field's stored value during validation, so a locked row can't
    be changed by editing the submitted form data either.
    """
    for form in formset.forms:
        if form.instance.pk:
            for name in ("title", "description", "is_mandatory", "requires_proof"):
                form.fields[name].disabled = True


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("onboarding.add_tasktemplate"), name="dispatch")
class TaskTemplateFormView(View):
    """
    Create/edit a Task Group's name together with all its tasks in one
    submit (header + inline formset of TaskTemplateTask rows, including
    drag-reordered priority).
    """

    def get(self, request, pk=None):
        instance = TaskTemplate.objects.filter(pk=pk).first() if pk else None
        form = TaskTemplateForm(instance=instance)
        formset = TaskTemplateTaskFormSet(instance=instance, prefix="tasks")
        _lock_saved_task_forms(formset)
        return render(
            request,
            "onboarding/task_templates/form.html",
            {"form": form, "formset": formset, "instance": instance},
        )

    def post(self, request, pk=None):
        instance = TaskTemplate.objects.filter(pk=pk).first() if pk else None
        form = TaskTemplateForm(request.POST, instance=instance)
        formset = TaskTemplateTaskFormSet(request.POST, instance=instance, prefix="tasks")
        _lock_saved_task_forms(formset)
        if form.is_valid():
            with transaction.atomic():
                template = form.save()
                formset = TaskTemplateTaskFormSet(
                    request.POST, instance=template, prefix="tasks"
                )
                _lock_saved_task_forms(formset)
                if formset.is_valid():
                    formset.save()
                    messages.success(request, _("Task group saved"))
                    return redirect("task-template-list")
                transaction.set_rollback(True)
        return render(
            request,
            "onboarding/task_templates/form.html",
            {"form": form, "formset": formset, "instance": instance},
        )


@login_required
@permission_required("onboarding.delete_tasktemplate")
def task_template_delete(request, pk):
    TaskTemplate.objects.filter(pk=pk).delete()
    messages.success(request, _("Task group deleted"))
    return redirect("task-template-list")


STATUS_TAB_ORDER = ["stuck", "todo", "scheduled", "ongoing", "done"]


@login_required
def my_checklist_tasks(request):
    """
    Where an assignee acts on the tasks the stepper created for them.
    Organized as tabs - Priority opens by default (mandatory tasks that are
    Stuck or Todo, since those are the ones actually blocking someone's
    onboarding), then the plain status tabs in Stuck/Todo-first order.
    """
    employee = getattr(request.user, "employee_get", None)
    tasks = []
    if employee:
        tasks = list(
            CandidateChecklistTask.objects.filter(assignee_id=employee)
            .select_related("candidate_id", "template_task_id")
            .order_by("-id")
        )

    status_labels = dict(CandidateChecklistTask.STATUS_CHOICES)
    tasks_by_status = {status: [] for status in STATUS_TAB_ORDER}
    for task in tasks:
        tasks_by_status.setdefault(task.status, []).append(task)

    priority_tasks = sorted(
        (task for task in tasks if task.is_mandatory and task.status in ("stuck", "todo")),
        key=lambda task: STATUS_TAB_ORDER.index(task.status),
    )

    status_tabs = [{"key": "priority", "label": _("Priority"), "tasks": priority_tasks}]
    status_tabs += [
        {"key": status, "label": status_labels.get(status, status), "tasks": tasks_by_status[status]}
        for status in STATUS_TAB_ORDER
    ]

    return render(
        request,
        "onboarding/task_templates/my_tasks.html",
        {
            "employee": employee,
            "status_tabs": status_tabs,
            "default_status": "priority",
            "status_choices": CandidateChecklistTask.STATUS_CHOICES,
            "total_count": len(tasks),
        },
    )


@login_required
def checklist_task_self_update(request, pk):
    task = get_object_or_404(CandidateChecklistTask, pk=pk)
    employee = getattr(request.user, "employee_get", None)
    if request.method == "POST" and employee and task.assignee_id_id == employee.pk:
        new_status = request.POST.get("status")
        if new_status in dict(CandidateChecklistTask.STATUS_CHOICES):
            if new_status == "done" and task.requires_proof and not (
                request.FILES.get("proof_file") or task.proof_file
            ):
                messages.error(
                    request,
                    _("This task requires proof of completion before it can be marked done."),
                )
            else:
                if new_status != task.status:
                    task.status_updated_at = timezone.now()
                task.status = new_status
                if request.FILES.get("proof_file"):
                    task.proof_file = request.FILES["proof_file"]
                if new_status == "done":
                    task.completed_at = timezone.now()
                elif task.completed_at:
                    task.completed_at = None
                task.save()
                if new_status == "done":
                    _maybe_mark_onboarding_complete(task.candidate_id)
    return redirect("my-checklist-tasks")


@login_required
def checklist_task_edit_modal(request, pk):
    task = get_object_or_404(CandidateChecklistTask, pk=pk)
    employee = getattr(request.user, "employee_get", None)
    if not employee or task.assignee_id_id != employee.pk:
        return HttpResponse("")
    return render(
        request,
        "onboarding/task_templates/_checklist_task_edit_modal.html",
        {"task": task, "status_choices": CandidateChecklistTask.STATUS_CHOICES},
    )


@login_required
def checklist_task_remove_proof(request, pk):
    """
    Removes an already-attached proof file. If the task's status is
    currently "done" and it still requires proof, dropping the file would
    leave it in an inconsistent state, so the task is reverted to "todo" too.
    """
    task = get_object_or_404(CandidateChecklistTask, pk=pk)
    employee = getattr(request.user, "employee_get", None)
    if request.method == "POST" and employee and task.assignee_id_id == employee.pk:
        if task.proof_file:
            task.proof_file.delete(save=False)
            task.proof_file = None
            if task.status == "done" and task.requires_proof:
                task.status = "todo"
                task.completed_at = None
            task.save()
            messages.success(request, _("Proof removed"))
    return redirect("my-checklist-tasks")
