"""
vertex/forms_performance.py - feedback, performance report and improvement plan forms.
"""

import re

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from vertex import performance
from vertex.forms import ModelForm
from vertex.models import (
    Feedback,
    PerformancePeriodType,
    Pip,
    PipOutcome,
    PipStatus,
    Project,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_PERIOD = re.compile(r"^\d{4}-(Q[1-4]|0[1-9]|1[0-2])$", re.I)


def _people(exclude=None):
    people = Employee.objects.filter(is_active=True).order_by("employee_first_name", "employee_last_name")
    return people.exclude(pk=exclude.pk) if exclude is not None else people


def _current_employee():
    from horilla.horilla_middlewares import _thread_locals
    from vertex import access

    request = getattr(_thread_locals, "request", None)
    user = getattr(request, "user", None)
    return access.employee_of(user) if user is not None and user.is_authenticated else None


class FeedbackForm(ModelForm):
    """
    Structured quarterly feedback. One score block per competency (0-100); Client &
    Stakeholder Communication can be marked Not Applicable. Scores can be left empty
    while the feedback is a draft, but every applicable score is required to submit it.
    """

    vm_icon = "chatbubble-ellipses-outline"
    vm_hint = "Nothing here reaches the employee until HR has approved and published it."
    vm_add_another = False
    cols = {
        "subject": 4, "project": 4, "period": 4, "constructive_summary": 12, "strengths": 6,
        "improvement_areas": 6, "action_plan": 12, "internal_notes": 12, "submit_now": 12,
    }

    submit_now = forms.BooleanField(
        required=False, label=_("Submit for HR approval now"),
        help_text=_("Leave off to keep it as a draft you can still edit."),
    )

    class Meta:
        model = Feedback
        fields = [
            "subject", "project", "period", "constructive_summary", "strengths",
            "improvement_areas", "action_plan", "internal_notes",
        ]
        labels = {"constructive_summary": _("Summary for the employee")}
        widgets = {
            "constructive_summary": forms.Textarea(attrs={"rows": 3}),
            "strengths": forms.Textarea(attrs={"rows": 2}),
            "improvement_areas": forms.Textarea(attrs={"rows": 2}),
            "action_plan": forms.Textarea(attrs={"rows": 2}),
            "internal_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        from vertex import access

        super().__init__(*args, **kwargs)
        me = _current_employee()
        self.fields["subject"].queryset = _people(exclude=me)
        from horilla.horilla_middlewares import _thread_locals

        request = getattr(_thread_locals, "request", None)
        user = getattr(request, "user", None)
        self.fields["project"].queryset = (
            access.visible_projects(user).order_by("name") if user is not None else Project.objects.none()
        )
        self.fields["project"].required = False
        if not self.instance.pk:
            self.fields["period"].initial = performance.current_quarter()
        existing = {c.category: c for c in self.instance.category_scores.all()} if self.instance.pk else {}
        self.cols = dict(self.cols)  # per form: the class level dict is shared
        self.vm_sections = {
            "constructive_summary": _("Feedback"),
            "internal_notes": _("Confidential"),
        }
        for category in performance.CATEGORIES:
            current = existing.get(category.key)
            self.fields[f"score_{category.key}"] = forms.IntegerField(
                required=False, min_value=0, max_value=100, label=_("Score (0 to 100)"),
                initial=current.score if current and not current.not_applicable else None,
                widget=forms.NumberInput(attrs={"min": 0, "max": 100, "placeholder": "0 to 100"}),
            )
            if category.can_be_na:
                self.fields[f"na_{category.key}"] = forms.BooleanField(
                    required=False, label=_("Not applicable"), initial=bool(current and current.not_applicable),
                )
            self.fields[f"comment_{category.key}"] = forms.CharField(
                required=False, label=_("Comment"), initial=current.comment if current else "",
                widget=forms.TextInput(attrs={"placeholder": _("Evidence or example (optional)")}),
            )
            self.vm_sections[f"score_{category.key}"] = f"{category.name}|{category.factors}"
            self.cols[f"score_{category.key}"] = 3
            self.cols[f"na_{category.key}"] = 3
            self.cols[f"comment_{category.key}"] = 6 if category.can_be_na else 9
        # keep the competency blocks after the free-text fields and before the submit switch
        order = list(self.Meta.fields)
        for category in performance.CATEGORIES:
            order.append(f"score_{category.key}")
            if category.can_be_na:
                order.append(f"na_{category.key}")
            order.append(f"comment_{category.key}")
        order.append("submit_now")
        self.order_fields(order)

    def clean_period(self):
        value = (self.cleaned_data["period"] or "").strip().upper()
        if not _PERIOD.match(value):
            raise forms.ValidationError(_("Use a quarter like 2026-Q4 or a month like 2026-10."))
        return value

    def clean_constructive_summary(self):
        value = self.cleaned_data["constructive_summary"].strip()
        if not value:
            raise forms.ValidationError(_("Write the summary the employee will see."))
        return value

    def clean_subject(self):
        subject = self.cleaned_data["subject"]
        me = _current_employee()
        if me is not None and subject.pk == me.pk:
            raise forms.ValidationError(_("You cannot submit feedback about yourself."))
        return subject

    def clean(self):
        data = super().clean()
        if data.get("submit_now"):
            for category in performance.CATEGORIES:
                skipped = category.can_be_na and data.get(f"na_{category.key}")
                if not skipped and data.get(f"score_{category.key}") is None:
                    self.add_error(
                        f"score_{category.key}", _("Score every applicable competency before submitting.")
                    )
        return data

    def category_data(self):
        """(category key, score or None, not applicable, comment) for each competency that was filled in."""
        rows = []
        for category in performance.CATEGORIES:
            score = self.cleaned_data.get(f"score_{category.key}")
            na = bool(category.can_be_na and self.cleaned_data.get(f"na_{category.key}"))
            comment = (self.cleaned_data.get(f"comment_{category.key}") or "").strip()
            if score is None and not na:
                continue
            rows.append((category.key, 0 if na else score, na, comment))
        return rows


class RevisionForm(forms.Form):
    reason = forms.CharField(
        label=_("What needs to change?"), widget=forms.Textarea(attrs={"rows": 2}), max_length=2000
    )

    def clean_reason(self):
        value = self.cleaned_data["reason"].strip()
        if not value:
            raise forms.ValidationError(_("Say what the author should change."))
        return value


class GenerateReportForm(forms.Form):
    """Generate (or regenerate) the draft performance report of one person for one period."""

    vm_icon = "analytics-outline"
    vm_hint = "Built from approved feedback, code and project reviews and status updates."
    vm_add_another = False
    cols = {"subject": 12, "period_type": 6, "period": 6}

    subject = forms.ModelChoiceField(queryset=Employee.objects.none(), label=_("Employee"))
    period_type = forms.ChoiceField(choices=PerformancePeriodType.choices, label=_("Period type"))
    period = forms.CharField(max_length=10, label=_("Period"), help_text=_("2026-07 for a month, 2026-Q3 for a quarter"))

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance  # the framework passes one; a report form has none
        self.fields["subject"].queryset = _people()
        self.fields["period_type"].initial = PerformancePeriodType.QUARTERLY
        self.fields["period"].initial = performance.current_quarter()
        for field in self.fields.values():
            css = "oh-select oh-select-2" if isinstance(field.widget, forms.Select) else "oh-input w-100"
            field.widget.attrs.setdefault("class", css)

    def clean(self):
        data = super().clean()
        if data.get("period_type") and data.get("period"):
            try:
                data["period"] = performance.normalise_period(data["period_type"], data["period"])
            except ValueError as error:
                self.add_error("period", str(error))
        return data


class PipForm(ModelForm):
    vm_icon = "trending-up-outline"
    vm_hint = "A documented plan to help someone get back on track."
    vm_add_another = False
    cols = {
        "subject": 6, "start_date": 6, "reason": 12, "expected_improvement": 12, "support_provided": 12,
        "status": 6, "outcome": 6, "review_notes": 12,
    }

    class Meta:
        model = Pip
        fields = [
            "subject", "start_date", "reason", "expected_improvement", "support_provided",
            "status", "outcome", "review_notes",
        ]
        widgets = {
            "start_date": _DATE,
            "reason": forms.Textarea(attrs={"rows": 2}),
            "expected_improvement": forms.Textarea(attrs={"rows": 2}),
            "support_provided": forms.Textarea(attrs={"rows": 2}),
            "review_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].queryset = _people()
        if self.instance.pk:
            # the person and start of a plan do not change; status, outcome and notes do
            self.fields["subject"].disabled = True
            self.fields["start_date"].disabled = True
            self.vm_sections = {"status": _("Review")}
        else:
            for name in ("status", "outcome", "review_notes"):
                del self.fields[name]
            self.fields["start_date"].initial = timezone.localdate()

    def clean_reason(self):
        value = self.cleaned_data["reason"].strip()
        if not value:
            raise forms.ValidationError(_("Say why the plan is needed."))
        return value

    def clean(self):
        data = super().clean()
        if self.instance.pk and data.get("outcome") and data.get("outcome") != PipOutcome.PENDING:
            if data.get("status") not in (PipStatus.COMPLETED, PipStatus.CLOSED):
                self.add_error("status", _("Mark the plan Completed or Closed when an outcome is recorded."))
        return data
