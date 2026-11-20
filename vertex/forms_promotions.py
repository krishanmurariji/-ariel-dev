"""
vertex/forms_promotions.py - promotion, promotion decision, training and training suggestion forms.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from base.models import JobPosition
from employee.models import Employee
from vertex import access
from vertex.forms import ModelForm, cap_text
from vertex.forms_appraisals import _request_user
from vertex.models import OPEN_PROMOTION_STAGES, Promotion, TrainingRecommendation

JUSTIFICATION_LIMIT = 1000
NOTE_LIMIT = 500
SKILL_LIMIT = 200


def _employees(queryset_fn):
    user = _request_user()
    people = queryset_fn(user) if user is not None else Employee.objects.none()
    return people.order_by("employee_first_name", "employee_last_name")


class PromotionForm(ModelForm):
    vm_icon = "trending-up-outline"
    vm_hint = "Put someone forward. HR validates it, leadership approves it, then HR completes it."
    vm_add_another = False
    cols = {"employee": 12, "proposed_position": 6, "proposed_salary": 6, "justification": 12}

    class Meta:
        model = Promotion
        fields = ["employee", "proposed_position", "proposed_salary", "justification"]
        widgets = {"justification": forms.Textarea(attrs={"rows": 4})}
        labels = {
            "proposed_position": _("Proposed designation"), "proposed_salary": _("Proposed salary (optional)"),
            "justification": _("Why this promotion?"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = _employees(access.recommendable_employees)
        self.fields["proposed_position"].queryset = JobPosition.objects.order_by("job_position")
        cap_text(self, "justification", JUSTIFICATION_LIMIT)

    def clean_justification(self):
        value = self.cleaned_data["justification"].strip()
        if not value:
            raise forms.ValidationError(_("Say why this person should be promoted."))
        return value

    def clean_proposed_salary(self):
        value = self.cleaned_data["proposed_salary"]
        if value is not None and value <= 0:
            raise forms.ValidationError(_("The proposed salary must be above zero."))
        return value

    def clean(self):
        data = super().clean()
        employee, position = data.get("employee"), data.get("proposed_position")
        if employee is not None:
            if Promotion.objects.filter(employee=employee, stage__in=OPEN_PROMOTION_STAGES).exists():
                self.add_error("employee", _("%(name)s already has a promotion being decided.") % {"name": employee.get_full_name()})
            elif position is not None and employee.get_job_position() == position:
                self.add_error("proposed_position", _("%(name)s already holds this designation.") % {"name": employee.get_full_name()})
        return data


class DecisionForm(forms.Form):
    """An optional note with an HR / leadership step."""

    vm_icon = "checkmark-done-outline"
    vm_hint = "Add a note if it helps the next person."
    vm_add_another = False
    cols = {"note": 12}
    verbose_name = _("Decision")

    note = forms.CharField(
        label=_("Note (optional)"), required=False, max_length=NOTE_LIMIT,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": NOTE_LIMIT, "class": "oh-input w-100"}),
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance  # the framework passes one; the step reads the promotion from the URL


class RejectForm(DecisionForm):
    vm_icon = "close-circle-outline"
    vm_hint = "The person who recommended it is told why. The employee is not told."
    verbose_name = _("Reject promotion")

    note = forms.CharField(
        label=_("Why is it not going ahead?"), required=True, max_length=NOTE_LIMIT,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": NOTE_LIMIT, "class": "oh-input w-100"}),
    )

    def clean_note(self):
        value = self.cleaned_data["note"].strip()
        if not value:
            raise forms.ValidationError(_("Give a reason."))
        return value


class TrainingForm(ModelForm):
    vm_icon = "school-outline"
    vm_hint = "Training to close a skill gap."
    vm_add_another = False
    cols = {"employee": 12, "skill_gap": 12, "recommended_training": 12, "duration_months": 6}

    class Meta:
        model = TrainingRecommendation
        fields = ["employee", "skill_gap", "recommended_training", "duration_months"]
        widgets = {"duration_months": forms.NumberInput(attrs={"min": 1, "max": 24})}
        labels = {"duration_months": _("Duration (months)"), "recommended_training": _("Training")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = _employees(access.training_employees)
        cap_text(self, "skill_gap", SKILL_LIMIT)
        cap_text(self, "recommended_training", SKILL_LIMIT)
        if self.instance.pk:
            self.fields["employee"].disabled = True

    def clean_skill_gap(self):
        value = self.cleaned_data["skill_gap"].strip()
        if not value:
            raise forms.ValidationError(_("Name the skill gap."))
        return value

    def clean_recommended_training(self):
        value = self.cleaned_data["recommended_training"].strip()
        if not value:
            raise forms.ValidationError(_("Say which training."))
        return value


class TrainingStatusForm(ModelForm):
    verbose_name = _("Update training")
    vm_icon = "school-outline"
    vm_hint = "Where this training stands."
    vm_add_another = False
    cols = {"status": 12}

    class Meta:
        model = TrainingRecommendation
        fields = ["status"]


class SuggestForm(forms.Form):
    verbose_name = _("Suggest training")
    vm_icon = "sparkles-outline"
    vm_hint = "Looks at goal progress and the latest published performance report, and suggests training for gaps under 60%."
    vm_add_another = False
    cols = {"employee": 12}

    employee = forms.ModelChoiceField(queryset=Employee.objects.none(), label=_("Employee"))

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["employee"].queryset = _employees(access.training_employees)
        self.fields["employee"].widget.attrs.setdefault("class", "oh-select oh-select-2")
