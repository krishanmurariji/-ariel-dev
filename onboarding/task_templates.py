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
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from horilla.decorators import login_required, permission_required
from onboarding.checklist_access import (
    apply_status_change,
    can_manage_task,
    visible_candidates_queryset,
)
from onboarding.filters import CandidateChecklistTaskFilter
from onboarding.forms import (
    TaskTemplateForm,
    TaskTemplateTaskFormSet,
    build_duplicate_task_formset,
)
from base.models import DurationPreset
from onboarding.models import PHASE_CHOICES, CandidateChecklistTask, TaskTemplate


def _duration_presets():
    return DurationPreset.objects.filter(is_active=True)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("onboarding.view_tasktemplate"), name="dispatch")
class TaskTemplateListView(View):
    """
    Settings page listing every Task Group and its tasks.
    """

    def get(self, request):
        templates = TaskTemplate.objects.prefetch_related("tasks")
        return render(
            request,
            "onboarding/task_templates/list.html",
            {"templates": templates, "phase_choices": PHASE_CHOICES},
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
            for name in ("title", "description"):
                form.fields[name].disabled = True


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("onboarding.view_tasktemplate"), name="dispatch")
class TaskTemplateViewModal(View):
    """
    Read-only modal listing every task in a Task Group - reached by
    clicking a card on the Task Templates list.
    """

    def get(self, request, pk):
        template = get_object_or_404(TaskTemplate, pk=pk)
        return render(
            request,
            "onboarding/task_templates/_view_modal.html",
            {"template": template, "tasks": template.tasks.all()},
        )


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
        duplicate_source = None
        if not instance:
            duplicate_source = TaskTemplate.objects.filter(
                pk=request.GET.get("duplicate")
            ).first()

        if duplicate_source:
            # A duplicate is a brand-new, unsaved Task Group prefilled from
            # an existing one - title is unique on the model, so "Copy of
            # ..." avoids a guaranteed clash on first save while still
            # carrying over every other detail unchanged.
            form = TaskTemplateForm(
                initial={
                    "title": _("Copy of %(title)s") % {"title": duplicate_source.title},
                    "phase": duplicate_source.phase,
                    "default_assignee_id": duplicate_source.default_assignee_id_id,
                    "default_due_offset": duplicate_source.default_due_offset,
                }
            )
            initial_tasks = list(
                duplicate_source.tasks.order_by("sequence", "id").values(
                    "title", "description", "sequence"
                )
            )
            formset = build_duplicate_task_formset(len(initial_tasks) + 1)(
                instance=None, initial=initial_tasks, prefix="tasks"
            )
        else:
            form = TaskTemplateForm(instance=instance)
            formset = TaskTemplateTaskFormSet(instance=instance, prefix="tasks")
            _lock_saved_task_forms(formset)
        return render(
            request,
            "onboarding/task_templates/form.html",
            {
                "form": form,
                "formset": formset,
                "instance": instance,
                "duration_presets": _duration_presets(),
            },
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
            {
                "form": form,
                "formset": formset,
                "instance": instance,
                "duration_presets": _duration_presets(),
            },
        )


@login_required
@permission_required("onboarding.delete_tasktemplate")
def task_template_delete(request, pk):
    TaskTemplate.objects.filter(pk=pk).delete()
    messages.success(request, _("Task group deleted"))
    return redirect("task-template-list")


MY_TASKS_VIEW_COOKIE = "onboarding_my_tasks_view"


@login_required
def my_checklist_tasks(request):
    """
    Page shell for My Tasks: a Kanban/List board loaded via htmx
    (my_checklist_tasks_board). The last-chosen view (Kanban vs List) is
    remembered via a small first-party cookie, read here to seed the
    board container's initial URL - simpler and race-free versus
    localStorage plus a DOM-rewrite before htmx's own "load" trigger fires.
    """
    initial_view = request.GET.get("view") or request.COOKIES.get(MY_TASKS_VIEW_COOKIE, "kanban")
    initial_query = request.GET.copy()
    initial_query["view"] = initial_view
    return render(
        request,
        "onboarding/task_templates/my_tasks.html",
        {
            "filter_form": CandidateChecklistTaskFilter(request.GET, request=request).form,
            "initial_view": initial_view,
            "initial_board_query": initial_query.urlencode(),
        },
    )


@login_required
def my_checklist_tasks_board(request):
    """
    Single stable htmx target for the My Tasks filter form / view toggle,
    so switching between Kanban and List never requires juggling different
    target URLs - query-string filters simply carry over untouched.

    Kanban mode delegates to MyTasksCandidateTabsView - one pill-tab per
    visible candidate (mirroring the Recruitment Pipeline's own tab strip),
    each lazily loading that candidate's Kanban board on click.
    """
    from onboarding.cbv.checklist_tasks import (
        CandidateChecklistTaskListView,
        MyTasksCandidateTabsView,
    )

    if request.GET.get("view") == "list":
        return CandidateChecklistTaskListView.as_view()(request)

    if not visible_candidates_queryset(request).exists():
        return render(request, "onboarding/task_templates/_no_candidates.html")
    return MyTasksCandidateTabsView.as_view()(request)


@login_required
def checklist_task_self_update(request, pk):
    task = get_object_or_404(CandidateChecklistTask, pk=pk)
    if request.method == "POST" and can_manage_task(request, task):
        ok, error = apply_status_change(task, request.POST.get("status"))
        if not ok:
            messages.error(request, error)
    return redirect("my-checklist-tasks")


@login_required
def checklist_task_edit_modal(request, pk):
    task = get_object_or_404(CandidateChecklistTask, pk=pk)
    if not can_manage_task(request, task):
        return HttpResponse("")
    return render(
        request,
        "onboarding/task_templates/_checklist_task_edit_modal.html",
        {"task": task, "status_choices": CandidateChecklistTask.STATUS_CHOICES},
    )
