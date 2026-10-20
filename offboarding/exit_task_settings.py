"""
offboarding/exit_task_settings.py

Settings > Offboarding > Exit Tasks: HR builds the Task Groups an exit has to
get through, saying for each group who it applies to and for each task who
does it, who approves it and when it lands on their list.

The groups are onboarding's own Task Groups (the "offboarding" type), so this
page only ever lists and edits those; onboarding's screens never show them.
Same scope rule as the rest of Exit Settings: with a company selected you
edit that company's groups (organisation-wide ones show read-only); in
"All Company" mode you edit the organisation-wide ones.
"""

from django import forms
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.models import Company, Department
from employee.models import Employee
from horilla.decorators import login_required, permission_required
from horilla.models import has_xss
from offboarding.form_fields import TextCharField
from offboarding.exit_settings import PERM, _htmx, _scope
from offboarding.exit_settings import _answer as _answer_on_settings_page
from offboarding.models import ExitTaskConfig, ExitTaskGroupRule
from onboarding.models import TaskTemplate, TaskTemplateTask

WHEN = [
    ("before", _("days before the last working day")),
    ("on", _("on the last working day")),
    ("after", _("days after the last working day")),
]
TEXT_BLOCKED = _("This text contains content that is not allowed.")


def _answer(request, **kwargs):
    """Answer an action on this page (plain posts come back here)."""
    return _answer_on_settings_page(request, back="exit-task-groups", **kwargs)


def _style(form):
    """Give every field the app's standard input look."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, (forms.HiddenInput, forms.CheckboxInput, forms.RadioSelect)):
            continue
        if isinstance(widget, forms.SelectMultiple):
            css = "oh-select"
        elif isinstance(widget, forms.Select):
            css = "oh-select"
        else:
            css = "oh-input"
        widget.attrs["class"] = f"{css} w-100"


def _groups_in_scope(scope):
    rows = TaskTemplate.objects.entire().filter(phase="offboarding")
    if scope:
        return rows.filter(Q(company_id=scope) | Q(company_id__isnull=True))
    return rows.filter(company_id__isnull=True)


def _editable_group_or_404(request, pk):
    scope = _scope(request)
    group = get_object_or_404(TaskTemplate.objects.entire(), pk=pk, phase="offboarding")
    if group.company_id_id != scope:
        return None
    return group


class GroupForm(forms.Form):
    title = forms.CharField(max_length=100)
    is_active = forms.BooleanField(required=False, initial=True)
    exit_type = forms.ChoiceField(choices=ExitTaskGroupRule.EXIT_TYPES, initial="all")
    include_departments = forms.ModelMultipleChoiceField(
        queryset=Department.objects.none(), required=False
    )
    exclude_departments = forms.ModelMultipleChoiceField(
        queryset=Department.objects.none(), required=False
    )

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        departments = Department.objects.all().order_by("department")
        self.fields["include_departments"].queryset = departments
        self.fields["exclude_departments"].queryset = departments
        _style(self)

    def clean_title(self):
        title = (self.cleaned_data["title"] or "").strip()
        if not title:
            raise forms.ValidationError(_("A name is required."))
        if has_xss(title):
            raise forms.ValidationError(TEXT_BLOCKED)
        clash = TaskTemplate.objects.entire().filter(title__iexact=title)
        if self.group is not None:
            clash = clash.exclude(pk=self.group.pk)
        if clash.exists():
            raise forms.ValidationError(
                _("A task group with this name already exists (onboarding and exit groups share names).")
            )
        return title

    def clean(self):
        cleaned = super().clean()
        both = set(cleaned.get("include_departments") or []) & set(
            cleaned.get("exclude_departments") or []
        )
        if both:
            self.add_error(
                "exclude_departments",
                _("A department cannot be both included and excluded."),
            )
        return cleaned


class TaskRowForm(forms.Form):
    task_pk = forms.IntegerField(required=False, widget=forms.HiddenInput)
    title = forms.CharField(max_length=200, required=False)
    description = TextCharField(
        required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 2})
    )
    owner_type = forms.ChoiceField(choices=ExitTaskConfig.OWNER_TYPES, initial="reporting_manager")
    owner_person = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False)
    approver_type = forms.ChoiceField(choices=ExitTaskConfig.APPROVER_TYPES, initial="none")
    approver_person = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False)
    trigger_when = forms.ChoiceField(choices=WHEN, initial="before")
    trigger_days = forms.IntegerField(min_value=0, max_value=365, initial=7, required=False)
    due_after_days = forms.IntegerField(min_value=0, max_value=365, initial=3, required=False)
    mandatory_attachment = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        people = Employee.objects.filter(is_active=True).order_by(
            "employee_first_name", "employee_last_name"
        )
        self.fields["owner_person"].queryset = people
        self.fields["approver_person"].queryset = people
        _style(self)

    def has_changed(self):
        # an untouched blank row is simply ignored
        return bool(
            (self["title"].value() or "").strip() or (self["task_pk"].value() or "")
        )

    def clean_title(self):
        title = (self.cleaned_data["title"] or "").strip()
        if title and has_xss(title):
            raise forms.ValidationError(TEXT_BLOCKED)
        return title

    def clean_description(self):
        text = (self.cleaned_data["description"] or "").strip()
        if text and has_xss(text):
            raise forms.ValidationError(TEXT_BLOCKED)
        return text

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("title"):
            self.add_error("title", _("A task needs a title."))
        if cleaned.get("owner_type") == "specific" and not cleaned.get("owner_person"):
            self.add_error("owner_person", _("Please choose the person."))
        if cleaned.get("approver_type") == "specific" and not cleaned.get("approver_person"):
            self.add_error("approver_person", _("Please choose the person."))
        if cleaned.get("trigger_when") != "on" and cleaned.get("trigger_days") is None:
            self.add_error("trigger_days", _("Please enter the number of days."))
        if cleaned.get("due_after_days") is None:
            cleaned["due_after_days"] = 3
        return cleaned

    def offset(self):
        data = self.cleaned_data
        days = data.get("trigger_days") or 0
        return {"before": -days, "on": 0, "after": days}[data["trigger_when"]]


TaskFormSet = forms.formset_factory(TaskRowForm, extra=1, can_delete=True)


def _initial_rows(group):
    rows = []
    for task in group.tasks.select_related("exit_config").order_by("sequence", "pk"):
        config = getattr(task, "exit_config", None) or ExitTaskConfig()
        offset = config.trigger_offset_days
        rows.append(
            {
                "task_pk": task.pk,
                "title": task.title,
                "description": task.description or "",
                "owner_type": config.owner_type,
                "owner_person": config.owner_person_id,
                "approver_type": config.approver_type,
                "approver_person": config.approver_person_id,
                "trigger_when": "before" if offset < 0 else "after" if offset > 0 else "on",
                "trigger_days": abs(offset),
                "due_after_days": config.due_after_days,
                "mandatory_attachment": config.mandatory_attachment,
            }
        )
    return rows


def _summary(group):
    rule = getattr(group, "exit_rule", None)
    parts = []
    if rule is None or rule.exit_type == "all":
        parts.append(_("All exits"))
    else:
        parts.append(rule.get_exit_type_display())
    if rule is not None:
        included = [d.department for d in rule.include_departments.all()]
        excluded = [d.department for d in rule.exclude_departments.all()]
        if included:
            parts.append(_("only in: %(names)s") % {"names": ", ".join(included)})
        if excluded:
            parts.append(_("not in: %(names)s") % {"names": ", ".join(excluded)})
    return " · ".join(str(part) for part in parts)


def _groups_context(request):
    scope = _scope(request)
    groups = list(
        _groups_in_scope(scope)
        .select_related("exit_rule")
        .prefetch_related(
            "exit_rule__include_departments",
            "exit_rule__exclude_departments",
            "tasks__exit_config",
        )
        .annotate(task_count=Count("tasks"))
        .order_by("title")
    )
    for group in groups:
        group.editable = group.company_id_id == scope
        group.summary = _summary(group)
        group.active = getattr(getattr(group, "exit_rule", None), "is_active", True)
        group.preview = [task.title for task in group.tasks.all()]
    return {"groups": groups, "scope_company": scope}


@login_required
@permission_required(PERM)
def exit_task_groups(request):
    return render(
        request, "offboarding/settings/exit_task_groups.html", _groups_context(request)
    )


@login_required
@permission_required(PERM)
def exit_task_groups_section(request):
    """Just the list of groups, so it can refresh after a change."""
    return render(request, "offboarding/settings/_xt_groups.html", _groups_context(request))


def _save(request, group, form, formset):
    scope = _scope(request)
    with transaction.atomic():
        data = form.cleaned_data
        if group is None:
            group = TaskTemplate(phase="offboarding")
            group.company_id = Company.objects.filter(pk=scope).first() if scope else None
        group.title = data["title"]
        group.save()
        rule, _created = ExitTaskGroupRule.objects.get_or_create(template_id=group)
        rule.is_active = data["is_active"]
        rule.exit_type = data["exit_type"]
        rule.save()
        rule.include_departments.set(data["include_departments"])
        rule.exclude_departments.set(data["exclude_departments"])

        kept = []
        position = 0
        for row in formset.forms:
            if not row.has_changed():
                continue
            if row.cleaned_data.get("DELETE"):
                continue
            cleaned = row.cleaned_data
            task = None
            if cleaned.get("task_pk"):
                task = group.tasks.filter(pk=cleaned["task_pk"]).first()
            if task is None:
                task = TaskTemplateTask(template_id=group)
            task.title = cleaned["title"]
            task.description = cleaned["description"]
            task.sequence = position
            task.save()
            position += 1
            kept.append(task.pk)
            config, _made = ExitTaskConfig.objects.get_or_create(task_id=task)
            config.owner_type = cleaned["owner_type"]
            config.owner_person = cleaned["owner_person"] if cleaned["owner_type"] == "specific" else None
            config.approver_type = cleaned["approver_type"]
            config.approver_person = (
                cleaned["approver_person"] if cleaned["approver_type"] == "specific" else None
            )
            config.trigger_offset_days = row.offset()
            config.due_after_days = cleaned["due_after_days"]
            config.mandatory_attachment = cleaned["mandatory_attachment"]
            config.save()
        # rows taken out of the form (or ticked for removal) are deleted
        group.tasks.exclude(pk__in=kept).delete()
    return group


@login_required
@permission_required(PERM)
def exit_task_group_form(request, pk=None):
    group = None
    if pk is not None:
        group = _editable_group_or_404(request, pk)
        if group is None:
            return _answer(
                request,
                error=_("Organisation-wide groups can only be changed in All Company mode."),
                close=False,
            )
    if request.method == "POST":
        form = GroupForm(request.POST, group=group)
        formset = TaskFormSet(request.POST, prefix="tasks")
        valid = form.is_valid()
        valid = formset.is_valid() and valid
        rows = [f for f in formset.forms if f.has_changed() and not f.cleaned_data.get("DELETE")]
        if valid and not rows:
            form.add_error(None, _("Add at least one task."))
            valid = False
        if valid:
            _save(request, group, form, formset)
            return _answer(request, message=_("The task group has been saved."))
    else:
        if group is not None:
            rule = getattr(group, "exit_rule", None)
            form = GroupForm(
                group=group,
                initial={
                    "title": group.title,
                    "is_active": rule.is_active if rule else True,
                    "exit_type": rule.exit_type if rule else "all",
                    "include_departments": list(rule.include_departments.all()) if rule else [],
                    "exclude_departments": list(rule.exclude_departments.all()) if rule else [],
                },
            )
            formset = TaskFormSet(initial=_initial_rows(group), prefix="tasks")
        else:
            form = GroupForm()
            formset = TaskFormSet(prefix="tasks")
    modal = _htmx(request)
    return render(
        request,
        "offboarding/settings/modals/task_group_form.html"
        if modal
        else "offboarding/settings/exit_task_group_form.html",
        {
            "form": form,
            "formset": formset,
            "group": group,
            "modal": modal,
            "empty_form": TaskFormSet(prefix="tasks").empty_form,
        },
    )


@login_required
@permission_required(PERM)
@require_POST
def exit_task_group_toggle(request, pk):
    group = _editable_group_or_404(request, pk)
    if group is None:
        return _answer(
            request,
            error=_("Organisation-wide groups can only be changed in All Company mode."),
            close=False,
        )
    rule, _created = ExitTaskGroupRule.objects.get_or_create(template_id=group)
    rule.is_active = not rule.is_active
    rule.save()
    return _answer(
        request,
        message=_("%(name)s is now on.") % {"name": group.title}
        if rule.is_active
        else _("%(name)s is now off - it will not be added to new exits.") % {"name": group.title},
        close=False,
    )


@login_required
@permission_required(PERM)
@require_POST
def exit_task_group_delete(request, pk):
    group = _editable_group_or_404(request, pk)
    if group is None:
        return _answer(
            request,
            error=_("Organisation-wide groups can only be changed in All Company mode."),
            close=False,
        )
    name = group.title
    group.delete()
    return _answer(
        request,
        message=_("%(name)s was deleted. Exits already using its tasks keep them.") % {"name": name},
    )
