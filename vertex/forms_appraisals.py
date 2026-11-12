"""
vertex/forms_appraisals.py - appraisal cycle, appraisal stage and goal forms.
"""

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from vertex import access
from vertex.forms import ModelForm
from vertex.models import (
    Appraisal,
    AppraisalCycle,
    CycleStatus,
    Goal,
    GoalStatus,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_RATING = forms.NumberInput(attrs={"min": "1", "max": "5", "step": "0.5"})
HALF = Decimal("0.5")


def _request_user():
    from horilla.horilla_middlewares import _thread_locals

    request = getattr(_thread_locals, "request", None)
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def _clean_rating(value):
    if value is not None and value % HALF != 0:
        raise forms.ValidationError(_("Rate in steps of 0.5, from 1 to 5."))
    return value


class CycleForm(ModelForm):
    vm_icon = "calendar-number-outline"
    vm_hint = "An appraisal round. Activate it later to open an appraisal for every active employee."
    vm_add_another = False
    cols = {"name": 12, "start_date": 6, "end_date": 6}

    class Meta:
        model = AppraisalCycle
        fields = ["name", "start_date", "end_date"]
        widgets = {"start_date": _DATE, "end_date": _DATE}
        labels = {"name": _("Cycle name")}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = AppraisalCycle.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("A cycle with this name already exists."))
        return name

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", _("The end date cannot be before the start date."))
        return data


class SelfAppraisalForm(ModelForm):
    verbose_name = _("Self assessment")
    vm_icon = "person-circle-outline"
    vm_hint = "Rate your own work this cycle and say why. Your manager sees this before they evaluate you."
    vm_add_another = False
    cols = {"self_rating": 12, "self_comments": 12}

    class Meta:
        model = Appraisal
        fields = ["self_rating", "self_comments"]
        widgets = {"self_rating": _RATING, "self_comments": forms.Textarea(attrs={"rows": 5})}
        labels = {"self_rating": _("Your rating (1 to 5)"), "self_comments": _("What went well, and what did not")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["self_rating"].required = True
        self.fields["self_comments"].required = True

    def clean_self_rating(self):
        return _clean_rating(self.cleaned_data["self_rating"])

    def clean_self_comments(self):
        value = self.cleaned_data["self_comments"].strip()
        if not value:
            raise forms.ValidationError(_("Add a few words about your work this cycle."))
        return value


class ManagerAppraisalForm(ModelForm):
    verbose_name = _("Manager evaluation")
    vm_icon = "ribbon-outline"
    vm_hint = "Your evaluation. The employee sees it only after HR releases the final rating."
    vm_add_another = False
    cols = {"manager_rating": 12, "manager_comments": 12}

    class Meta:
        model = Appraisal
        fields = ["manager_rating", "manager_comments"]
        widgets = {"manager_rating": _RATING, "manager_comments": forms.Textarea(attrs={"rows": 5})}
        labels = {"manager_rating": _("Your rating (1 to 5)"), "manager_comments": _("Evaluation")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager_rating"].required = True
        self.fields["manager_comments"].required = True

    def clean_manager_rating(self):
        return _clean_rating(self.cleaned_data["manager_rating"])

    def clean_manager_comments(self):
        value = self.cleaned_data["manager_comments"].strip()
        if not value:
            raise forms.ValidationError(_("Write your evaluation."))
        return value


class ReleaseAppraisalForm(ModelForm):
    verbose_name = _("Release final rating")
    vm_icon = "checkmark-done-outline"
    vm_hint = "The final rating the employee will see. It starts from the manager's rating."
    vm_add_another = False
    cols = {"final_rating": 12, "final_comments": 12}

    class Meta:
        model = Appraisal
        fields = ["final_rating", "final_comments"]
        widgets = {"final_rating": _RATING, "final_comments": forms.Textarea(attrs={"rows": 3})}
        labels = {"final_rating": _("Final rating (1 to 5)"), "final_comments": _("Note to the employee (optional)")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["final_rating"].required = True
        if self.instance.pk and self.instance.final_rating is None:
            self.initial["final_rating"] = self.instance.manager_rating  # form.initial wins over field.initial

    def clean_final_rating(self):
        return _clean_rating(self.cleaned_data["final_rating"])


class GoalForm(ModelForm):
    vm_icon = "flag-outline"
    vm_hint = "A goal or KRA with a weight. The weights of one person's active goals in a cycle add up to at most 100."
    vm_add_another = False
    cols = {
        "employee": 6, "cycle": 6, "title": 12, "category": 6, "weightage": 3, "target_date": 3, "description": 12,
    }

    class Meta:
        model = Goal
        fields = ["employee", "cycle", "title", "category", "weightage", "target_date", "description"]
        widgets = {"target_date": _DATE, "description": forms.Textarea(attrs={"rows": 3})}
        labels = {"weightage": _("Weight (%)"), "cycle": _("Appraisal cycle")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = _request_user()
        people = access.assignable_employees(user) if user is not None else Employee.objects.none()
        self.fields["employee"].queryset = people.order_by("employee_first_name", "employee_last_name")
        cycles = AppraisalCycle.objects.filter(status__in=[CycleStatus.DRAFT, CycleStatus.ACTIVE])
        if self.instance.cycle_id:
            cycles = AppraisalCycle.objects.filter(pk=self.instance.cycle_id) | cycles
        self.fields["cycle"].queryset = cycles.distinct()
        self.fields["cycle"].empty_label = _("No cycle")
        if self.instance.pk:
            self.fields["employee"].disabled = True

    def clean_title(self):
        value = self.cleaned_data["title"].strip()
        if not value:
            raise forms.ValidationError(_("Give the goal a title."))
        return value

    def clean(self):
        data = super().clean()
        employee = self.instance.employee if self.instance.pk else data.get("employee")
        weight = data.get("weightage")
        if employee is not None and weight:
            used = access.weight_used(employee, data.get("cycle"), exclude_pk=self.instance.pk)
            if used + weight > 100:
                self.add_error(
                    "weightage",
                    _("%(name)s already has %(used)s%% of goal weight in this cycle; at most %(left)s%% is left.")
                    % {"name": employee.get_full_name(), "used": used, "left": max(0, 100 - used)},
                )
        return data


class GoalProgressForm(ModelForm):
    verbose_name = _("Update progress")
    vm_icon = "trending-up-outline"
    vm_hint = "Where this goal stands. At 100% it is marked Completed."
    vm_add_another = False
    cols = {"progress": 6, "status": 6}

    class Meta:
        model = Goal
        fields = ["progress", "status"]
        widgets = {"progress": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "5"})}
        labels = {"progress": _("Progress (%)")}

    def clean(self):
        data = super().clean()
        progress, status = data.get("progress"), data.get("status")
        if progress is None or not status:
            return data
        if status == GoalStatus.COMPLETED:
            data["progress"] = 100
        elif progress >= 100 and status != GoalStatus.CANCELLED:
            data["status"] = GoalStatus.COMPLETED
        elif progress > 0 and status == GoalStatus.NOT_STARTED:
            data["status"] = GoalStatus.IN_PROGRESS
        return data
