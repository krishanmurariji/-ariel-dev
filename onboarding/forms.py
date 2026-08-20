"""
forms.py

This module contains the form classes used in the application.

Each form represents a specific functionality or data input in the
application. They are responsible for validating
and processing user input data.

Classes:
- YourForm: Represents a form for handling specific data input.

Usage:
from django import forms

class YourForm(forms.Form):
    field_name = forms.CharField()

    def clean_field_name(self):
        # Custom validation logic goes here
        pass
"""

import uuid
from datetime import date
from typing import Any

from django import forms
from django.contrib.auth.forms import UserCreationForm as UserForm
from django.forms import DateInput, ValidationError
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from base.methods import reload_queryset
from employee.filters import EmployeeFilter
from employee.models import (
    Employee,
    EmployeeBankDetails,
    EmployeeEducation,
    EmployeeEmergencyContact,
    EmployeeExperience,
    phone_validator,
)
from horilla_auth.models import HorillaUser
from horilla_widgets.widgets.horilla_multi_select_field import HorillaMultiSelectField
from horilla_widgets.widgets.select_widgets import HorillaMultiSelectWidget
from onboarding.models import (
    CandidateStage,
    CandidateTask,
    OnboardingStage,
    OnboardingTask,
)
from recruitment.models import Candidate


class UserCreationFormCustom(UserForm):
    """
    Overriding user creation form to apply some styles
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reload_queryset(self.fields)
        for _, field in self.fields.items():
            widget = field.widget
            if isinstance(
                widget,
                (
                    forms.NumberInput,
                    forms.EmailInput,
                    forms.TextInput,
                    forms.PasswordInput,
                ),
            ):
                field.widget.attrs.update(
                    {
                        "class": "oh-input oh-input--password w-100",
                        "placeholder": field.label,
                    }
                )
            elif isinstance(widget, (forms.DateField)):
                field.widget.attrs.update({"class": "oh-input oh-calendar-input w-100"})
            elif isinstance(
                widget, (forms.NumberInput, forms.EmailInput, forms.TextInput)
            ):
                field.widget.attrs.update(
                    {"class": "oh-input w-100", "placeholder": field.label}
                )
            elif isinstance(widget, (forms.Select,)):
                field.empty_label = f"---Choose {field.label}---"
                field.widget.attrs.update(
                    {"class": "oh-select oh-select-2 select2-hidden-accessible"}
                )
            elif isinstance(widget, (forms.Textarea)):
                field.widget.attrs.update(
                    {
                        "class": "oh-input w-100",
                        "placeholder": field.label,
                        "rows": 2,
                        "cols": 40,
                    }
                )
            elif isinstance(
                widget,
                (
                    forms.CheckboxInput,
                    forms.CheckboxSelectMultiple,
                ),
            ):
                field.widget.attrs.update({"class": "oh-switch__checkbox"})


class OnboardingCandidateForm(ModelForm):
    """
    Form for Candidate model
    """

    class Meta:
        """
        Meta class for some additional options
        """

        model = Candidate
        fields = "__all__"
        exclude = (
            "stage_id",
            "assigned_manager",
            "confirmation",
            "hired",
            "referral",
            "portfolio",
            "canceled",
            "is_active",
            "resume",
            "schedule_date",
            "job_position_id",
        )
        widgets = {
            "joining_date": DateInput(attrs={"type": "date"}),
        }
        labels = {
            "name": _("Full Name"),
            "email": _("Email"),
            "mobile": _("Mobile"),
        }


class UserCreationForm(UserCreationFormCustom):
    """
    Form for HorillaUser model
    """

    class Meta:
        """
        Meta class to add some additional options
        """

        model = HorillaUser
        fields = ["password1", "password2"]


class OnboardingViewTaskForm(ModelForm):
    """
    Form for OnboardingTask model
    """

    candidates = forms.ModelMultipleChoiceField(
        queryset=Candidate.objects.all(),
        # widget=forms.SelectMultiple(attrs={"class": "select2-hidden-accessible "}),
        required=False,
    )
    stage_id = forms.HiddenInput()
    task_title = forms.CharField(label=(_("Task title")))
    managers = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.all(),
        # widget=forms.SelectMultiple(attrs={"class": "select2-hidden-accessible "})
    )
    is_required = forms.BooleanField(required=False, label=_("Is Required"))

    class Meta:
        """
        Meta class for some additional options
        """

        model = CandidateTask
        fields = "__all__"
        exclude = ["status", "candidate_id", "onboarding_task_id", "is_active"]

    def clean(self):
        for field_name, field_instance in self.fields.items():
            if isinstance(field_instance, HorillaMultiSelectField):
                self.errors.pop(field_name, None)
                if len(self.data.getlist(field_name)) < 1:
                    raise forms.ValidationError({field_name: "Thif field is required"})
                cleaned_data = super().clean()
                employee_data = self.fields[field_name].queryset.filter(
                    id__in=self.data.getlist(field_name)
                )
                cleaned_data[field_name] = employee_data
        cleaned_data = super().clean()
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["managers"] = HorillaMultiSelectField(
            queryset=Employee.objects.all(),
            widget=HorillaMultiSelectWidget(
                filter_route_name="employee-widget-filter",
                filter_class=EmployeeFilter,
                filter_instance_context_name="f",
                filter_template_path="employee_filters.html",
                required=True,
                instance=self.instance,
            ),
            label=_("Task Managers"),
        )
        reload_queryset(self.fields)


class OnboardingTaskForm(ModelForm):
    """
    Form for OnboardingTaskModel
    """

    class Meta:
        """
        Meta class for add some additional options
        """

        model = OnboardingTask
        fields = "__all__"
        exclude = ["is_active"]
        widgets = {
            "candidates": forms.SelectMultiple(
                attrs={"class": "oh-select oh-select-2 w-100 select2-hidden-accessible"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee_id"] = HorillaMultiSelectField(
            queryset=Employee.objects.all(),
            widget=HorillaMultiSelectWidget(
                filter_route_name="employee-widget-filter",
                filter_class=EmployeeFilter,
                filter_instance_context_name="f",
                filter_template_path="employee_filters.html",
                required=True,
                instance=self.instance,
            ),
            label=self.fields["employee_id"].label,
        )
        stage_id = self.initial.get("stage_id")
        if stage_id:
            stage = OnboardingStage.objects.get(id=stage_id)
            recruitment = stage.recruitment_id

            # Adjust the queryset based on the 'stage'
            stage_queryset = recruitment.onboarding_stage.all()
            self.fields["stage_id"].queryset = stage_queryset
            candidate_ids = stage.candidate.all().values_list("candidate_id", flat=True)
            cand_queryset = Candidate.objects.filter(id__in=candidate_ids)
            self.fields["candidates"].queryset = cand_queryset

    def clean(self):
        if isinstance(self.fields["employee_id"], HorillaMultiSelectField):
            ids = self.data.getlist("employee_id")
            if ids:
                self.errors.pop("employee_id", None)
        super().clean()


class OnboardingViewStageForm(ModelForm):
    """
    Form for OnboardingStageModel
    """

    class Meta:
        """
        Meta class for add some additional options
        """

        model = OnboardingStage
        fields = ["stage_title", "employee_id", "is_final_stage", "recruitment_id"]
        labels = {
            "stage_title": _("Stage Title"),
            "is_final_stage": _("Is Final Stage"),
        }

    def __init__(self, *args, **kwargs):
        """
        Initializes the form with custom field settings and widgets.
        """
        super().__init__(*args, **kwargs)
        reload_queryset(self.fields)
        self.fields["employee_id"] = HorillaMultiSelectField(
            queryset=Employee.objects.filter(is_active=True),
            widget=HorillaMultiSelectWidget(
                filter_route_name="employee-widget-filter",
                filter_class=EmployeeFilter,
                filter_instance_context_name="f",
                filter_template_path="employee_filters.html",
                required=True,
                instance=self.instance,
            ),
            label=self.fields["employee_id"].label,
        )

        # Loop through form fields and generate unique IDs for their attributes
        for field_name, field in self.fields.items():
            unique_id = str(uuid.uuid4())  # You can customize the unique ID format

            # Set the widget's attributes with the unique ID
            field.widget.attrs.update({"id": unique_id})

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("horilla_form.html", context)
        return table_html

    def clean(self):
        if isinstance(self.fields["employee_id"], HorillaMultiSelectField):
            ids = self.data.getlist("employee_id")
            if ids:
                self.errors.pop("employee_id", None)
        super().clean()


class EmployeeCreationForm(ModelForm):
    """
    Form for Employee Model
    """

    employee_first_name = forms.CharField(required=True, label=_("First Name"))
    employee_last_name = forms.CharField(required=False, label=_("Last Name"))
    # Overriding the field like this bypasses the model field's own
    # validators=[phone_validator] (ModelForm only auto-applies those to
    # fields it generates itself from the model) - re-declared explicitly
    # here so phone format is still actually checked.
    phone = forms.CharField(
        required=True, label=_("Phone"), validators=[phone_validator]
    )
    address = forms.CharField(required=True, label=_("Address"))
    country = forms.CharField(required=True, label=_("Country"))
    state = forms.CharField(required=True, label=_("State"))
    zip = forms.CharField(required=True, label=_("Zip"))
    children = forms.IntegerField(required=True, label=_("Children"))

    class Meta:
        """
        Meta class to add some additional options
        """

        model = Employee
        fields = "__all__"
        exclude = (
            "employee_user_id",
            "employee_profile",
            "email",
            "is_active",
            "additional_info",
            "is_from_onboarding",
            "is_directly_converted",
            # Replaced by build_employee_emergency_contact_formset below - a single
            # employee can have more than one emergency contact now, these
            # three flat fields only ever held one. Kept on the model
            # itself (existing reports/exports/API still read them).
            "emergency_contact",
            "emergency_contact_name",
            "emergency_contact_relation",
            # Replaced by EmployeeEducationFormSet/EmployeeExperienceFormSet
            # below - the candidate already gave this as multiple
            # structured entries on the application form, so this onboarding
            # step reviews/edits those instead of asking for one flat
            # summary. Kept on the model itself (existing reports/exports/
            # API still read them) but no longer collected here.
            "qualification",
            "experience",
        )
        widgets = {
            "dob": DateInput(attrs={"type": "date"}),
        }

    def clean_dob(self):
        # Mirrors recruitment.forms.ApplicationForm.clean_dob - same rule
        # applied on the candidate side, kept consistent here.
        dob = self.cleaned_data.get("dob")
        if dob:
            today = date.today()
            if dob > today:
                raise ValidationError(_("Date of birth cannot be in the future."))
            age = (
                today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            )
            if age < 15:
                raise ValidationError(_("Employee must be at least 15 years old."))
            if age > 100:
                raise ValidationError(_("Enter a valid date of birth."))
        return dob

    def clean(self):
        cleaned_data = self.cleaned_data
        if cleaned_data["children"] and cleaned_data["children"] < 0:
            raise ValidationError(
                {"children": _("No of children should be a postive integier")}
            )

        return super().clean()


_TEXT_INPUT_CLASS = "oh-input w-100 mb-2"
_DATE_INPUT_CLASS = "oh-input w-100 mb-2"


class EmployeeEducationForm(forms.ModelForm):
    """Adds date-ordering validation on top of the plain ModelForm the
    factory would otherwise auto-generate - inlineformset_factory doesn't
    let you attach a clean() without passing a real form class."""

    class Meta:
        model = EmployeeEducation
        fields = [
            "degree",
            "specialization",
            "percentage_cgpa",
            "start_date",
            "end_date",
            "university",
            "location",
        ]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and start_date > date.today():
            self.add_error(
                "start_date", _("Start of course cannot be in the future.")
            )
        if start_date and end_date and end_date < start_date:
            self.add_error(
                "end_date", _("End of course cannot be before the start of course.")
            )
        return cleaned_data


class EmployeeExperienceForm(forms.ModelForm):
    """See EmployeeEducationForm docstring."""

    class Meta:
        model = EmployeeExperience
        fields = ["company_name", "designation", "start_date", "end_date", "location"]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        today = date.today()
        if start_date and start_date > today:
            self.add_error(
                "start_date", _("Date of joining cannot be in the future.")
            )
        if end_date and end_date > today:
            self.add_error(
                "end_date", _("Date of relieving cannot be in the future.")
            )
        if start_date and end_date and end_date < start_date:
            self.add_error(
                "end_date",
                _("Date of relieving cannot be before the date of joining."),
            )
        return cleaned_data


def build_employee_education_formset(extra=0):
    """
    extra is set by the caller based on how many rows need to be shown
    blank/prefilled (see onboarding.views.employee_creation) - inline
    formsets only render `extra` rows beyond whatever the bound instance
    already has saved, so a fixed extra=0 factory can't also be used to
    seed rows from the candidate's application data.
    """
    return forms.inlineformset_factory(
        Employee,
        EmployeeEducation,
        form=EmployeeEducationForm,
        fields=[
            "degree",
            "specialization",
            "percentage_cgpa",
            "start_date",
            "end_date",
            "university",
            "location",
        ],
        widgets={
            "degree": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "specialization": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "percentage_cgpa": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "university": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "location": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "start_date": DateInput(
                attrs={"type": "date", "class": _DATE_INPUT_CLASS}
            ),
            "end_date": DateInput(attrs={"type": "date", "class": _DATE_INPUT_CLASS}),
        },
        extra=extra,
        can_delete=True,
    )


def build_employee_experience_formset(extra=0):
    """
    See build_employee_education_formset docstring re: extra.

    is_current_job is deliberately NOT in fields - by the time someone
    reaches this onboarding step they've already been hired here, so
    "currently working" at a *different* company no longer makes sense to
    offer as an option. onboarding.views.employee_creation forces every
    row back to is_current_job=False on save regardless of what was set
    when the candidate originally applied, so the template only ever
    needs to show a relieving date, never the checkbox.
    """
    return forms.inlineformset_factory(
        Employee,
        EmployeeExperience,
        form=EmployeeExperienceForm,
        fields=[
            "company_name",
            "designation",
            "start_date",
            "end_date",
            "location",
        ],
        widgets={
            "company_name": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "designation": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "location": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "start_date": DateInput(
                attrs={"type": "date", "class": _DATE_INPUT_CLASS}
            ),
            "end_date": DateInput(attrs={"type": "date", "class": _DATE_INPUT_CLASS}),
        },
        extra=extra,
        can_delete=True,
    )


def build_employee_emergency_contact_formset(extra=0):
    """
    See build_employee_education_formset docstring re: extra - a fixed
    extra=0 factory can't also seed rows from the candidate's own
    emergency contacts.
    """
    return forms.inlineformset_factory(
        Employee,
        EmployeeEmergencyContact,
        fields=["name", "phone", "relation"],
        widgets={
            "name": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "phone": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
            "relation": forms.TextInput(attrs={"class": _TEXT_INPUT_CLASS}),
        },
        extra=extra,
        can_delete=True,
    )


class BankDetailsCreationForm(ModelForm):
    """
    Form for BankDetailsCreationForm
    """

    bank_name = forms.CharField(required=True, label="Bank Name")
    account_number = forms.CharField(required=True, label="Account Number")
    branch = forms.CharField(required=True, label="Branch")
    address = forms.Textarea()
    country = forms.CharField(required=True, label="Country")
    state = forms.CharField(required=True, label="State")
    city = forms.CharField(required=True, label="City")
    any_other_code1 = forms.CharField(required=True, label="Code #1")
    any_other_code2 = forms.CharField(required=False, label="Code #2")

    class Meta:
        """
        Meta class to add some additional options
        """

        model = EmployeeBankDetails
        fields = "__all__"
        exclude = ["employee_id", "additional_info", "is_active"]


class StageChangeForm(forms.ModelForm):
    """
    StageChangeForm
    """

    class Meta:
        """
        Meta class for additional options
        """

        model = CandidateStage
        fields = [
            "onboarding_stage_id",
        ]

    def clean(self):
        cleaned_data = super().clean()
        new_stage = cleaned_data.get("onboarding_stage_id")
        old_stage = self.instance.onboarding_stage_id
        if (
            new_stage
            and old_stage
            and new_stage.sequence is not None
            and old_stage.sequence is not None
        ):
            pending_tasks = self.instance.pending_required_tasks(old_stage)
            if pending_tasks.exists():
                task_titles = ", ".join(
                    pending_tasks.values_list("task_title", flat=True)
                )
                raise forms.ValidationError(
                    _(
                        "Complete the following required task(s) before "
                        "moving to the next stage: %(tasks)s"
                    )
                    % {"tasks": task_titles}
                )
        return cleaned_data
