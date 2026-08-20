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

import logging
import re
import uuid
from ast import Dict
from datetime import date, datetime
from typing import Any

from django import forms
from django.apps import apps
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from base.forms import Form
from base.forms import ModelForm as BaseModelForm
from base.methods import reload_queryset
from base.widgets import CustomTextInputWidget
from employee.filters import EmployeeFilter
from employee.models import Employee
from horilla import horilla_middlewares
from horilla.horilla_middlewares import _thread_locals
from horilla_widgets.widgets.horilla_multi_select_field import HorillaMultiSelectField
from horilla_widgets.widgets.select_widgets import HorillaMultiSelectWidget
from recruitment import widgets
from recruitment.models import (
    AISettings,
    Candidate,
    CandidateDocument,
    CandidateDocumentRequest,
    CandidateEducation,
    CandidateEmergencyContact,
    CandidateExperience,
    CandidateIntakeMailbox,
    InterviewSchedule,
    JobPosition,
    LinkedInAccount,
    Platform,
    Recruitment,
    RecruitmentSurvey,
    RejectedCandidate,
    RejectReason,
    Resume,
    Skill,
    SkillZone,
    SkillZoneCandidate,
    Stage,
    StageFiles,
    StageNote,
    SurveyTemplate,
)

logger = logging.getLogger(__name__)


class ModelForm(forms.ModelForm):
    """
    Override of Django ModelForm to add initial styling and defaults.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        reload_queryset(self.fields)

        request = getattr(horilla_middlewares._thread_locals, "request", None)

        today = date.today()
        now = datetime.now()

        default_input_class = "oh-input w-100"
        select_class = "oh-select oh-select-2 select2-hidden-accessible"
        checkbox_class = "oh-switch__checkbox"

        for field_name, field in self.fields.items():
            widget = field.widget
            label = _(field.label) if field.label else ""

            # Date field
            if isinstance(widget, forms.DateInput):
                field.initial = today
                widget.input_type = "date"
                widget.format = "%Y-%m-%d"
                field.input_formats = ["%Y-%m-%d"]

                existing_class = widget.attrs.get("class", default_input_class)
                widget.attrs.update(
                    {
                        "class": f"{existing_class} form-control",
                        "placeholder": label,
                    }
                )

            # Time field
            elif isinstance(widget, forms.TimeInput):
                field.initial = now.strftime("%H:%M")
                widget.input_type = "time"
                widget.format = "%H:%M"
                field.input_formats = ["%H:%M"]

                existing_class = widget.attrs.get("class", default_input_class)
                widget.attrs.update(
                    {
                        "class": f"{existing_class} form-control",
                        "placeholder": label,
                    }
                )

            # Number, Email, Text, File, URL fields
            elif isinstance(
                widget,
                (
                    forms.NumberInput,
                    forms.EmailInput,
                    forms.TextInput,
                    forms.FileInput,
                    forms.URLInput,
                ),
            ):
                existing_class = widget.attrs.get("class", default_input_class)
                widget.attrs.update(
                    {
                        "class": f"{existing_class} form-control",
                        "placeholder": _(field.label.title()) if field.label else "",
                    }
                )

            # Select fields
            elif isinstance(widget, forms.Select):
                if not isinstance(field, forms.ModelMultipleChoiceField):
                    field.empty_label = _("---Choose {label}---").format(label=label)
                existing_class = widget.attrs.get("class", select_class)
                widget.attrs.update({"class": existing_class})

            # Textarea
            elif isinstance(widget, forms.Textarea):
                existing_class = widget.attrs.get("class", default_input_class)
                widget.attrs.update(
                    {
                        "class": f"{existing_class} form-control",
                        "placeholder": label,
                        "rows": 2,
                        "cols": 40,
                    }
                )

            # Checkbox types
            elif isinstance(
                widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)
            ):
                existing_class = widget.attrs.get("class", checkbox_class)
                widget.attrs.update({"class": existing_class})

        # Set employee_id and company_id once
        if request:
            employee = getattr(request.user, "employee_get", None)
            if employee:
                if "employee_id" in self.fields:
                    self.fields["employee_id"].initial = employee

                if "company_id" in self.fields:
                    company_field = self.fields["company_id"]
                    company = getattr(employee, "get_company", None)
                    if company:
                        queryset = company_field.queryset
                        company_field.initial = (
                            company if company in queryset else queryset.first()
                        )


class RegistrationForm(forms.ModelForm):
    """
    Overriding django default model form to apply some styles
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reload_queryset(self.fields)
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.Select,)):
                label = ""
                if field.label is not None:
                    label = _(field.label)
                field.empty_label = _("---Choose {label}---").format(label=label)
                self.fields[field_name].widget.attrs.update(
                    {"id": uuid.uuid4, "class": "oh-select-2 oh-select--sm w-100"}
                )
            elif isinstance(widget, (forms.TextInput)):
                field.widget.attrs.update(
                    {
                        "class": "oh-input w-100",
                    }
                )
            elif isinstance(
                widget,
                (
                    forms.CheckboxInput,
                    forms.CheckboxSelectMultiple,
                ),
            ):
                field.widget.attrs.update({"class": "oh-switch__checkbox "})


class DropDownForm(forms.ModelForm):
    """
    Overriding django default model form to apply some styles
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reload_queryset(self.fields)
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(
                widget,
                (
                    forms.NumberInput,
                    forms.EmailInput,
                    forms.TextInput,
                    forms.FileInput,
                    forms.URLInput,
                ),
            ):
                if field.label is not None:
                    label = _(field.label)
                    field.widget.attrs.update(
                        {
                            "class": "oh-input oh-input--small oh-table__add-new-row d-block w-100",
                            "placeholder": label,
                        }
                    )
            elif isinstance(widget, (forms.Select,)):
                self.fields[field_name].widget.attrs.update(
                    {
                        "class": "oh-select-2 oh-select--xs-forced ",
                        "id": uuid.uuid4(),
                    }
                )
            elif isinstance(widget, (forms.Textarea)):
                if field.label is not None:
                    label = _(field.label)
                    field.widget.attrs.update(
                        {
                            "class": "oh-input oh-input--small oh-input--textarea",
                            "placeholder": label,
                            "rows": 1,
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
                field.widget.attrs.update({"class": "oh-switch__checkbox "})


class RecruitmentCreationForm(BaseModelForm):
    """
    Form for Recruitment model
    """

    cols = {
        "is_published": 4,
        "optional_profile_image": 4,
        "optional_resume": 4,
    }

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Recruitment
        fields = "__all__"
        exclude = ["is_active", "linkedin_post_id"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"data-summernote": ""}),
        }

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("horilla_form.html", context)
        return table_html

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reload_queryset(self.fields)
        if "open_positions" in self.fields:
            # Only actually required when publishing (see clean()), but marked
            # required here so the form shows the "*" indicator - the common
            # case is publishing, and the field being silently optional in the
            # UI was confusing.
            self.fields["open_positions"].required = True
        if "recruitment_managers" in self.fields:
            self.fields["recruitment_managers"] = HorillaMultiSelectField(
                queryset=Employee.objects.filter(is_active=True),
                widget=HorillaMultiSelectWidget(
                    filter_route_name="employee-widget-filter",
                    filter_class=EmployeeFilter,
                    filter_instance_context_name="f",
                    filter_template_path="employee_filters.html",
                    required=True,
                ),
                label=f"{self._meta.model()._meta.get_field('recruitment_managers').verbose_name}",
            )
        if "recruitment_heads" in self.fields:
            self.fields["recruitment_heads"] = HorillaMultiSelectField(
                queryset=Employee.objects.filter(is_active=True),
                required=False,
                widget=HorillaMultiSelectWidget(
                    filter_route_name="employee-widget-filter",
                    filter_class=EmployeeFilter,
                    filter_instance_context_name="f",
                    filter_template_path="employee_filters.html",
                    required=False,
                ),
                label=f"{self._meta.model()._meta.get_field('recruitment_heads').verbose_name}",
            )
            # The model field has blank=True but the HorillaMultiSelectField
            # widget still needs required explicitly cleared on edit-mode too.
            self.fields["recruitment_heads"].required = False

        skill_choices = [("", _("---Choose Skills---"))] + list(
            self.fields["skills"].queryset.values_list("id", "title")
        )
        self.fields["skills"].choices = skill_choices
        self.fields["skills"].choices += [("create", _("Create new skill "))]
        self.fields["linkedin_account_id"].queryset = LinkedInAccount.objects.filter(
            is_active=True
        )
        self.fields["publish_in_linkedin"].widget.attrs.update(
            {"onchange": "toggleLinkedIn()"}
        )

    # def create_option(self, *args,**kwargs):
    #     option = super().create_option(*args,**kwargs)

    def clean(self):
        if isinstance(self.fields["recruitment_managers"], HorillaMultiSelectField):
            ids = self.data.getlist("recruitment_managers")
            if ids:
                self.errors.pop("recruitment_managers", None)
        if isinstance(self.fields.get("recruitment_heads"), HorillaMultiSelectField):
            self.errors.pop("recruitment_heads", None)
        open_positions = self.cleaned_data.get("open_positions")
        is_published = self.cleaned_data.get("is_published")
        if not is_published:
            self.errors.pop("open_positions", None)
        elif not open_positions:
            raise forms.ValidationError(
                _("Job position is required if the recruitment is publishing.")
            )
        if (
            self.cleaned_data.get("publish_in_linkedin")
            and not self.cleaned_data["linkedin_account_id"]
        ):
            raise forms.ValidationError(
                {
                    "linkedin_account_id": _(
                        "LinkedIn account is required for publishing."
                    )
                }
            )
        super().clean()


class StageCreationForm(BaseModelForm):
    """
    Form for Stage model
    """

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Stage
        fields = "__all__"
        exclude = ["sequence", "is_active"]
        labels = {
            "stage": _("Stage"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reload_queryset(self.fields)
        if not self.instance.pk:
            self.fields["stage_managers"] = HorillaMultiSelectField(
                queryset=Employee.objects.filter(is_active=True),
                required=False,
                widget=HorillaMultiSelectWidget(
                    filter_route_name="employee-widget-filter",
                    filter_class=EmployeeFilter,
                    filter_instance_context_name="f",
                    filter_template_path="employee_filters.html",
                    required=False,
                ),
                label=f"{self._meta.model()._meta.get_field('stage_managers').verbose_name}",
            )
        # The model field itself has no blank=True, so the edit-mode form
        # (which uses the default ModelForm-generated field instead of the
        # HorillaMultiSelectField above) would otherwise still require it.
        self.fields["stage_managers"].required = False

    def clean(self):
        if isinstance(self.fields["stage_managers"], HorillaMultiSelectField):
            ids = self.data.getlist("stage_managers")
            if ids:
                self.errors.pop("stage_managers", None)
        super().clean()


class CandidateCreationForm(BaseModelForm):
    """
    Form for Candidate model
    """

    load = forms.CharField(widget=widgets.RecruitmentAjaxWidget, required=False)
    new_platform = forms.CharField(
        required=False,
        label=_("Or type a new source name (e.g. LinkedIn, Indeed)"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["platform"].required = False
        self.fields["profile"].widget.attrs["accept"] = ".jpg, .jpeg, .png"
        self.fields["profile"].required = False
        self.fields["resume"].widget.attrs["accept"] = ".pdf"
        self.fields["resume"].required = False
        if self.instance.recruitment_id is not None:
            if self.instance is not None:
                self.fields["job_position_id"] = forms.ModelChoiceField(
                    queryset=self.instance.recruitment_id.open_positions.all(),
                    label=_("Job Position"),
                )
        self.fields["recruitment_id"].widget.attrs = {"data-widget": "ajax-widget"}
        self.fields["job_position_id"].widget.attrs = {"data-widget": "ajax-widget"}

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Candidate
        fields = [
            "profile",
            "name",
            "portfolio",
            "email",
            "mobile",
            "recruitment_id",
            "job_position_id",
            "dob",
            "gender",
            "address",
            "platform",
            "country",
            "state",
            "zip",
            "resume",
            "referral",
            "canceled",
            "is_active",
        ]

        widgets = {
            "scheduled_date": forms.DateInput(attrs={"type": "date"}),
            "dob": forms.DateInput(attrs={"type": "date"}),
        }

    def save(self, commit: bool = ...):
        candidate = self.instance
        new_platform = (self.cleaned_data.get("new_platform") or "").strip()
        if new_platform:
            platform_obj, _created = Platform.objects.get_or_create(
                title__iexact=new_platform, defaults={"title": new_platform}
            )
            candidate.platform = platform_obj
        recruitment = candidate.recruitment_id
        stage = candidate.stage_id
        candidate.hired = False
        candidate.start_onboard = False
        if stage is not None:
            if stage.stage_type == "hired" and candidate.canceled is False:
                candidate.hired = True
                candidate.start_onboard = True
        candidate.recruitment_id = recruitment
        candidate.stage_id = stage
        job_id = self.data.get("job_position_id")
        if job_id:
            job_position = JobPosition.objects.get(id=job_id)
            self.instance.job_position_id = job_position
        return super().save(commit)

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string(
            "candidate/candidate_create_form_as_p.html", context
        )
        return table_html

    def clean(self):
        errors = {}
        profile = self.cleaned_data["profile"]
        resume = self.cleaned_data["resume"]
        recruitment: Recruitment = self.cleaned_data["recruitment_id"]
        if not resume and not recruitment.optional_resume:
            errors["resume"] = _("This field is required")
        if not profile and not recruitment.optional_profile_image:
            errors["profile"] = _("This field is required")
        if self.instance.name is not None:
            self.errors.pop("job_position_id", None)
            if (
                self.instance.job_position_id is None
                or self.data.get("job_position_id") == ""
            ):
                errors["job_position_id"] = _("This field is required")
            if (
                self.instance.job_position_id
                not in self.instance.recruitment_id.open_positions.all()
            ):
                errors["job_position_id"] = _("Choose valid choice")
        if errors:
            raise ValidationError(errors)
        return super().clean()


class ApplicationForm(RegistrationForm):
    """
    Form for create Candidate
    """

    load = forms.CharField(widget=widgets.RecruitmentAjaxWidget, required=False)
    active_recruitment = Recruitment.objects.filter(
        is_active=True, closed=False, is_published=True
    )
    recruitment_id = forms.ModelChoiceField(queryset=active_recruitment)

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Candidate
        exclude = (
            "stage_id",
            "schedule_date",
            "referral",
            "start_onboard",
            "hired",
            "is_active",
            "canceled",
            "joining_date",
            "sequence",
            "offerletter_status",
            "platform",
            "skills",
        )
        widgets = {
            "recruitment_id": forms.TextInput(
                attrs={
                    "required": "required",
                }
            ),
            "dob": forms.DateInput(
                attrs={
                    "type": "date",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = getattr(_thread_locals, "request", None)
        self.fields["profile"].widget.attrs["accept"] = ".jpg, .jpeg, .png"
        self.fields["profile"].required = False
        self.fields["resume"].widget.attrs["accept"] = ".pdf"
        self.fields["resume"].required = False

        self.fields["recruitment_id"].widget.attrs = {"data-widget": "ajax-widget"}
        self.fields["job_position_id"].widget.attrs = {"data-widget": "ajax-widget"}
        if request and request.user.has_perm("recruitment.add_candidate"):
            self.fields["profile"].required = False

        # Only name/email/mobile are mandatory on the public application
        # form - resume/profile follow the recruitment's own
        # optional_resume/optional_profile_image flags (handled in clean()
        # below), everything else (gender, address, country, state, city,
        # zip, portfolio) is optional.
        self.fields["mobile"].required = True
        self.fields["gender"].required = False

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        name = re.sub(r"\s+", " ", name)
        if not re.match(r"^[A-Za-z ]+$", name):
            raise ValidationError(
                _("Name can only contain letters and spaces.")
            )
        return name

    def clean_mobile(self):
        mobile = (self.cleaned_data.get("mobile") or "").strip()
        if not re.match(r"^\d{7,15}$", mobile):
            raise ValidationError(
                _("Contact number must contain 7 to 15 digits only.")
            )
        return mobile

    def clean_dob(self):
        dob = self.cleaned_data.get("dob")
        if dob:
            today = date.today()
            if dob > today:
                raise ValidationError(_("Date of birth cannot be in the future."))
            age = (
                today.year
                - dob.year
                - ((today.month, today.day) < (dob.month, dob.day))
            )
            if age < 18:
                raise ValidationError(
                    _("Candidate must be at least 18 years old.")
                )
            if age > 100:
                raise ValidationError(_("Enter a valid date of birth."))
        return dob

    def clean_reason_to_leave(self):
        reason = (self.cleaned_data.get("reason_to_leave") or "").strip()
        return reason

    def clean(self, *args, **kwargs):
        name = self.cleaned_data.get("name")
        request = getattr(_thread_locals, "request", None)

        errors = {}
        profile = self.cleaned_data.get("profile")
        resume = self.cleaned_data.get("resume")
        email = self.cleaned_data.get("email")
        mobile = self.cleaned_data.get("mobile")
        recruitment: Recruitment = self.cleaned_data.get("recruitment_id")

        if recruitment:
            if not resume and not recruitment.optional_resume:
                errors["resume"] = _("This field is required")
            # Unlike resume, the profile image section is hidden entirely
            # on the public form unless a recruitment opts in via
            # optional_profile_image - so it's only required when that
            # flag is on (the opposite polarity of optional_resume).
            if recruitment.optional_profile_image and not profile:
                errors["profile"] = _("This field is required")

            if email and Candidate.objects.filter(
                email__iexact=email, recruitment_id=recruitment
            ).exists():
                errors["email"] = _(
                    "A candidate with this email has already applied for this job."
                )
            if mobile and Candidate.objects.filter(
                mobile=mobile, recruitment_id=recruitment
            ).exists():
                errors["mobile"] = _(
                    "A candidate with this phone number has already applied for this job."
                )

        if errors:
            raise ValidationError(errors)

        if (
            not profile
            and request
            and request.user.has_perm("recruitment.add_candidate")
        ):
            profile_pic_url = f"https://ui-avatars.com/api/?name={name}"
            self.cleaned_data["profile"] = profile_pic_url

        super().clean()
        return self.cleaned_data


# Shared with the education/experience formsets below. Mirrors the
# client-side pattern used on the public application form (see
# TEXT_FIELD_PATTERN in application_form.html) so a submission that
# bypasses the JS (a forged POST, JS disabled) is still held to the
# same rule server-side: must start with a letter/digit, then only
# letters, digits, spaces and common name/address punctuation - no
# stray symbols, markup, or emoji-only garbage.
CANDIDATE_TEXT_FIELD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'()&/-]*$")
CANDIDATE_CGPA_PATTERN = re.compile(r"^\d{1,3}(\.\d{1,2})?$")


def _validate_candidate_text_field(form, field_name, label, required=False, min_length=2):
    """
    Applies the shared min-length + character-set rule to one field on
    one formset row. max_length is already enforced by the model
    field's own CharField validator before this runs.
    """
    value = (form.cleaned_data.get(field_name) or "").strip()
    if not value:
        if required:
            form.add_error(field_name, _("%(label)s is required.") % {"label": label})
        return
    if len(value) < min_length:
        form.add_error(
            field_name,
            _("%(label)s must be at least %(min)d characters.")
            % {"label": label, "min": min_length},
        )
        return
    if not CANDIDATE_TEXT_FIELD_PATTERN.match(value):
        form.add_error(
            field_name,
            _("%(label)s contains characters that aren't allowed.")
            % {"label": label},
        )


class CandidateEducationBaseFormSet(forms.BaseInlineFormSet):
    """
    Validates each education row's required/text fields, date range,
    and CGPA/percentage format - the model fields themselves are
    largely unconstrained (plain CharFields with no character-set or
    numeric rules) so this is the only place any of that gets checked.
    """

    def clean(self):
        super().clean()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue

            _validate_candidate_text_field(
                form, "degree", _("Course"), required=True
            )
            _validate_candidate_text_field(
                form, "specialization", _("Branch/Specialization")
            )
            _validate_candidate_text_field(form, "university", _("University/College"))
            _validate_candidate_text_field(form, "location", _("Location"))

            start_date = form.cleaned_data.get("start_date")
            end_date = form.cleaned_data.get("end_date")
            if start_date and end_date and end_date < start_date:
                form.add_error(
                    "end_date",
                    _("End of course cannot be before the start of course."),
                )

            percentage_cgpa = (form.cleaned_data.get("percentage_cgpa") or "").strip()
            if percentage_cgpa:
                if not CANDIDATE_CGPA_PATTERN.match(percentage_cgpa) or not (
                    0 <= float(percentage_cgpa) <= 100
                ):
                    form.add_error(
                        "percentage_cgpa",
                        _(
                            "Enter a valid CGPA (0-10) or percentage (0-100), e.g. 8.5 or 82."
                        ),
                    )


# Inline formsets used on the public application form so a candidate can
# add any number of education/experience/emergency-contact entries. extra=0
# because the form template renders its own starting row and "Add another"
# button in JS rather than relying on Django's blank-extra-forms rendering.
CandidateEducationFormSet = forms.inlineformset_factory(
    Candidate,
    CandidateEducation,
    fields=[
        "degree",
        "specialization",
        "percentage_cgpa",
        "start_date",
        "end_date",
        "university",
        "location",
    ],
    formset=CandidateEducationBaseFormSet,
    extra=0,
    can_delete=True,
)

class CandidateExperienceBaseFormSet(forms.BaseInlineFormSet):
    """
    Restricts "Currently working here" to at most one experience entry -
    a candidate can only be currently employed at one place at a time -
    and validates each row's required/text fields and date range.
    """

    def clean(self):
        super().clean()
        current_job_count = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if form.cleaned_data.get("is_current_job"):
                current_job_count += 1

            _validate_candidate_text_field(
                form, "company_name", _("Company Name"), required=True
            )
            _validate_candidate_text_field(
                form, "designation", _("Job Title"), required=True
            )
            _validate_candidate_text_field(form, "location", _("Location"))

            start_date = form.cleaned_data.get("start_date")
            end_date = form.cleaned_data.get("end_date")
            if start_date and end_date and end_date < start_date:
                form.add_error(
                    "end_date",
                    _("Date of relieving cannot be before the date of joining."),
                )
        if current_job_count > 1:
            raise ValidationError(
                _(
                    'Only one experience entry can be marked as "Currently working here".'
                )
            )


CandidateExperienceFormSet = forms.inlineformset_factory(
    Candidate,
    CandidateExperience,
    fields=[
        "company_name",
        "designation",
        "is_current_job",
        "start_date",
        "end_date",
        "location",
    ],
    formset=CandidateExperienceBaseFormSet,
    extra=0,
    can_delete=True,
)

CandidateEmergencyContactFormSet = forms.inlineformset_factory(
    Candidate,
    CandidateEmergencyContact,
    fields=["name", "phone", "relation"],
    extra=0,
    can_delete=True,
)


class RecruitmentDropDownForm(DropDownForm):
    """
    Form for Recruitment model
    """

    class Meta:
        """
        Meta class to add the additional info
        """

        fields = "__all__"
        model = Recruitment
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"data-summernote": ""}),
        }
        labels = {"description": _("Description"), "vacancy": _("Vacancy")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["job_position_id"].widget.attrs.update({"id": uuid.uuid4})
        self.fields["recruitment_managers"].widget.attrs.update({"id": uuid.uuid4})
        field = self.fields["is_active"]
        field.widget = field.hidden_widget()


class AddCandidateForm(ModelForm):
    """
    Form for Candidate model
    """

    verbose_name = "Add Candidate"

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Candidate
        fields = [
            "profile",
            "resume",
            "name",
            "email",
            "mobile",
            "gender",
            "stage_id",
            "job_position_id",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = kwargs["initial"].get("stage_id")
        if initial:
            recruitment = Stage.objects.get(id=initial).recruitment_id
            self.instance.recruitment_id = recruitment
            self.fields["stage_id"].queryset = self.fields["stage_id"].queryset.filter(
                recruitment_id=recruitment
            )
            self.fields["job_position_id"].queryset = recruitment.open_positions
        self.fields["profile"].widget.attrs["accept"] = ".jpg, .jpeg, .png"
        self.fields["resume"].widget.attrs["accept"] = ".pdf"
        if recruitment.optional_profile_image:
            self.fields["profile"].required = False
        if recruitment.optional_resume:
            self.fields["resume"].required = False
        self.fields["gender"].empty_label = None
        self.fields["job_position_id"].empty_label = None
        self.fields["stage_id"].empty_label = None

    def clean(self):
        cleaned_data = super().clean()
        # recruitment_id isn't a form field (it's set from the stage in
        # __init__ above), so Django's automatic unique_together
        # validation for ("email", "recruitment_id") never runs on this
        # form - check it explicitly here instead, or a duplicate
        # candidate + recruitment combination hits the DB's unique
        # constraint unhandled and surfaces as a 500 to the user.
        email = cleaned_data.get("email")
        recruitment = self.instance.recruitment_id
        if email and recruitment:
            duplicates = Candidate.objects.filter(
                email=email, recruitment_id=recruitment
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error(
                    "email",
                    _(
                        "A candidate with this email has already applied "
                        "to this recruitment."
                    ),
                )
        return cleaned_data

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class StageDropDownForm(DropDownForm):
    """
    Form for Stage model
    """

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Stage
        fields = "__all__"
        exclude = ["sequence", "is_active"]
        labels = {
            "stage": _("Stage"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stage = Stage.objects.last()
        if stage is not None and stage.sequence is not None:
            self.instance.sequence = stage.sequence + 1
        else:
            self.instance.sequence = 1


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = [
                single_file_clean(data, initial),
            ]
        return result[0] if result else []


class StageNoteForm(ModelForm):
    """
    Form for StageNote model
    """

    class Meta:
        """
        Meta class to add the additional info
        """

        model = StageNote
        # exclude = (
        #     "updated_by",
        #     "stage_id",
        # )
        fields = ["description"]
        exclude = ["is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # field = self.fields["candidate_id"]
        # field.widget = field.hidden_widget()
        self.fields["stage_files"] = MultipleFileField(label="files")
        self.fields["stage_files"].required = False

    def save(self, commit: bool = ...) -> Any:
        attachment = []
        multiple_attachment_ids = []
        attachments = None
        if self.files.getlist("stage_files"):
            attachments = self.files.getlist("stage_files")
            self.instance.attachement = attachments[0]
            multiple_attachment_ids = []

            for attachment in attachments:
                file_instance = StageFiles()
                file_instance.files = attachment
                file_instance.save()
                multiple_attachment_ids.append(file_instance.pk)
        instance = super().save(commit)
        if commit:
            instance.stage_files.add(*multiple_attachment_ids)
        return instance, multiple_attachment_ids


class StageNoteUpdateForm(ModelForm):
    class Meta:
        """
        Meta class to add the additional info
        """

        model = StageNote
        exclude = ["updated_by", "stage_id", "stage_files", "is_active"]
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["candidate_id"]
        field.widget = field.hidden_widget()


class QuestionForm(ModelForm):
    """
    QuestionForm
    """

    cols = {"options": 12, "template_id": 12, "question": 12}

    verbose_name = "Survey Questions"

    recruitment = forms.ModelMultipleChoiceField(
        queryset=Recruitment.objects.filter(is_active=True),
        required=False,
        label=_("Recruitment"),
    )
    options = forms.CharField(
        widget=forms.TextInput, label=_("Options"), required=False
    )

    class Meta:
        """
        Class Meta for additional options
        """

        model = RecruitmentSurvey
        fields = "__all__"
        exclude = ["recruitment_ids", "job_position_ids", "is_active", "options"]
        labels = {
            "question": _("Question"),
            "sequence": _("Sequence"),
            "type": _("Type"),
            "options": _("Options"),
            "is_mandatory": _("Is Mandatory"),
        }

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string(
            "survey/question_template_organized_form.html", context
        )
        return table_html

    def clean(self):
        cleaned_data = super().clean()
        recruitment = self.cleaned_data.get("recruitment")
        question_type = self.cleaned_data.get("type")
        options = self.cleaned_data.get("options")
        self.recruitment = (
            recruitment if recruitment is not None else Recruitment.objects.none()
        )
        if question_type in ["options", "multiple"] and (
            options is None or options == ""
        ):
            raise ValidationError({"options": "Options field is required"})
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.type in ["options", "multiple"]:
            additional_options = []
            for key, value in self.cleaned_data.items():
                if key.startswith("options") and value:
                    additional_options.append(value)

            instance.options = ", ".join(additional_options)
            if commit:
                instance.save()
                self.save_m2m()
        else:
            instance.options = ""
        return instance

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance", None)
        self.option_count = 1

        def create_options_field(option_key, initial=None):
            self.fields[option_key] = forms.CharField(
                widget=forms.TextInput(
                    attrs={
                        "name": option_key,
                        "id": f"id_{option_key}",
                        "class": "oh-input w-100",
                    }
                ),
                label=_("Options"),
                required=False,
                initial=initial,
            )

        def create_options_field_more(option_key, initial=None):
            self.fields[option_key] = forms.CharField(
                widget=CustomTextInputWidget(
                    delete_url="add-remove-options-field",
                    attrs={
                        "name": option_key,
                        "id": f"{option_key}",
                        "class": "oh-input w-100",
                    },
                ),
                required=False,
                initial=initial,
            )

        if instance:
            split_options = instance.options.split(",")
            for i, option in enumerate(split_options):
                if i == 0:
                    create_options_field("options", option)
                else:
                    self.option_count += 1
                    create_options_field_more(f"options{i}", option)

        if instance:
            self.fields["recruitment"].initial = instance.recruitment_ids.all()
        self.fields["type"].widget.attrs.update(
            {"class": " w-100", "style": "border:solid 1px #6c757d52;height:50px;"}
        )
        for key, value in self.data.items():
            if key.startswith("options"):
                self.option_count += 1
                create_options_field(key, initial=value)
        fields_order = list(self.fields.keys())
        fields_order.remove("recruitment")
        fields_order.insert(2, "recruitment")
        self.fields = {field: self.fields[field] for field in fields_order}


class SurveyForm(forms.Form):
    """
    SurveyTemplateForm
    """

    def __init__(self, recruitment, *args, **kwargs) -> None:
        super().__init__(recruitment, *args, **kwargs)
        questions = recruitment.recruitmentsurvey_set.all()
        all_questions = RecruitmentSurvey.objects.none() | questions
        for template in recruitment.survey_templates.all():
            questions = template.recruitmentsurvey_set.all()
            all_questions = all_questions | questions
        context = {"form": self, "questions": all_questions.distinct()}
        form = render_to_string("survey_form.html", context)
        self.form = form
        return
        # for question in questions:
        # self


class SurveyPreviewForm(forms.Form):
    """
    SurveyTemplateForm
    """

    def __init__(self, template, *args, **kwargs) -> None:
        super().__init__(template, *args, **kwargs)
        all_questions = RecruitmentSurvey.objects.filter(template_id__in=[template])
        context = {"form": self, "questions": all_questions.distinct()}
        form = render_to_string("survey_preview_form.html", context)
        self.form = form
        return
        # for question in questions:
        # self


class TemplateForm(BaseModelForm):
    """
    TemplateForm
    """

    cols = {"title": 12, "description": 12, "company_id": 12}

    verbose_name = "Template"

    class Meta:
        model = SurveyTemplate
        fields = "__all__"
        exclude = ["is_active"]

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class AddQuestionForm(Form):
    """
    AddQuestionForm
    """

    verbose_name = "Add Question"
    question_ids = forms.ModelMultipleChoiceField(
        queryset=RecruitmentSurvey.objects.all(), label="Questions"
    )
    template_ids = forms.ModelMultipleChoiceField(
        queryset=SurveyTemplate.objects.all(), label="Templates"
    )

    def save(self):
        """
        Manual save/adding of questions to the templates
        """
        for question in self.cleaned_data["question_ids"]:
            question.template_id.add(*self.data["template_ids"])

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


exclude_fields = [
    "id",
    "profile",
    "portfolio",
    "resume",
    "sequence",
    "schedule_date",
    "created_at",
    "created_by",
    "modified_by",
    "is_active",
    "last_updated",
    "horilla_history",
]


class CandidateMovePipelineForm(ModelForm):
    """
    Form to move a candidate from their current recruitment pipeline to a
    different open job (and a specific stage within it) in the same company.
    """

    # Full width, stacked - side-by-side (the "col" filter's default half
    # width) made this small two-field modal render unusually wide.
    cols = {"recruitment_id": 12, "stage_id": 12}

    class Meta:
        model = Candidate
        fields = ["recruitment_id", "stage_id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recruitment_id"].label = _("Move to job opening")
        self.fields["recruitment_id"].empty_label = _("---Choose job opening---")
        self.fields["recruitment_id"].required = True
        self.fields["recruitment_id"].widget = forms.Select(
            attrs={"class": "oh-select w-100", "id": "id_recruitment_id"}
        )
        current_recruitment = self.instance.recruitment_id
        queryset = Recruitment.objects.filter(closed=False)
        if current_recruitment:
            queryset = queryset.filter(
                company_id=current_recruitment.company_id
            ).exclude(pk=current_recruitment.pk)
        self.fields["recruitment_id"].queryset = queryset

        self.fields["stage_id"].label = _("Stage")
        self.fields["stage_id"].required = True
        self.fields["stage_id"].widget = forms.Select(
            attrs={"class": "oh-select w-100", "id": "id_stage_id"}
        )
        # The stage dropdown is populated client-side via JS once a job
        # opening is picked (see candidate/move_pipeline_form.html) - the
        # queryset here only needs to match whatever recruitment_id was
        # actually submitted, so validation accepts exactly what the user
        # was shown regardless of client-side timing/JS state.
        selected_recruitment_id = self.data.get("recruitment_id") if self.is_bound else None
        if selected_recruitment_id:
            self.fields["stage_id"].queryset = Stage.objects.filter(
                recruitment_id=selected_recruitment_id
            )
            self.fields["stage_id"].empty_label = _("---Choose Stage---")
        else:
            self.fields["stage_id"].queryset = Stage.objects.none()
            self.fields["stage_id"].empty_label = _("---Choose job opening first---")


class CandidateExportForm(forms.Form):
    model_fields = Candidate._meta.get_fields()
    field_choices = [
        (field.name, field.verbose_name.capitalize())
        for field in model_fields
        if hasattr(field, "verbose_name") and field.name not in exclude_fields
    ]
    field_choices = field_choices + [
        ("rejected_candidate__description", "Rejected Description"),
    ]
    selected_fields = forms.MultipleChoiceField(
        choices=field_choices,
        widget=forms.CheckboxSelectMultiple,
        initial=[
            "name",
            "recruitment_id",
            "job_position_id",
            "stage_id",
            "email",
            "mobile",
            "hired",
            "joining_date",
        ],
    )


class SkillZoneCreateForm(BaseModelForm):

    cols = {"title": 12, "description": 12, "company_id": 12}

    class Meta:
        """
        Class Meta for additional options
        """

        model = SkillZone
        fields = "__all__"
        exclude = ["is_active"]


class SkillZoneCandidateForm(ModelForm):

    cols = {"skill_zone_id": 12, "candidate_id": 12, "reason": 12}
    verbose_name = "Skill Zone Candidate"
    candidate_id = forms.ModelMultipleChoiceField(
        queryset=Candidate.objects.all(),
        widget=forms.SelectMultiple,
        label=_("Candidate"),
    )

    class Meta:
        """
        Class Meta for additional options
        """

        model = SkillZoneCandidate
        fields = ["skill_zone_id", "reason"]

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def clean_candidate_id(self):
        candidate_field = self.cleaned_data["candidate_id"]

        if isinstance(candidate_field, Candidate):
            return candidate_field

        if hasattr(candidate_field, "__iter__"):
            for candidate in candidate_field:
                if not isinstance(candidate, Candidate):
                    raise forms.ValidationError("Invalid candidate selected.")
            return candidate_field

        return candidate_field

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["candidate_id"].empty_label = None

        self.fields = {
            "skill_zone_id": self.fields["skill_zone_id"],
            "candidate_id": self.fields["candidate_id"],
            "reason": self.fields["reason"],
        }

        if self.instance.pk:
            self.verbose_name = (
                self.instance.candidate_id.name
                + " / "
                + self.instance.skill_zone_id.title
            )
            self.fields["candidate_id"] = forms.ModelChoiceField(
                queryset=Candidate.objects.all(),
                widget=forms.Select(attrs={"class": "oh-select oh-select2 w-100"}),
                label=_("Candidate"),
                # candidate_id is a manually-declared field, not one of
                # Meta.fields - Django's ModelForm.__init__ only
                # auto-populates initial values from the instance for
                # fields listed in Meta.fields, so without this the
                # dropdown always opens blank on edit even though a
                # candidate is already set.
                initial=self.instance.candidate_id,
                # Which candidate a Skill Zone entry belongs to is that
                # entry's identity (see verbose_name above) - editing is
                # only meant to change skill_zone_id/reason, never
                # reassign the record to a different candidate. disabled
                # (not just excluded from Meta.fields) so it renders
                # visibly non-interactive instead of looking like a
                # working dropdown that silently does nothing on save.
                disabled=True,
            )

    def save(self, commit: bool = True) -> SkillZoneCandidate:

        if not self.instance.pk:
            candidates = Candidate.objects.filter(
                id__in=list((self.data.getlist("candidate_id")))
            )
            skill_zone = self.cleaned_data["skill_zone_id"]
            reason = self.cleaned_data["reason"]
            for candidate in candidates:
                zone_cand = SkillZoneCandidate()
                zone_cand.skill_zone_id = skill_zone
                zone_cand.candidate_id = candidate
                zone_cand.reason = reason
                zone_cand.save()
        else:
            instance = super().save()

        return self.instance


class ToSkillZoneForm(ModelForm):

    verbose_name = "Add To Skill Zone"
    skill_zone_ids = forms.ModelMultipleChoiceField(
        queryset=SkillZone.objects.all(), label=_("Skill Zones")
    )

    cols = {"reason": 12, "skill_zone_ids": 12}

    class Meta:
        """
        Class Meta for additional options
        """

        model = SkillZoneCandidate
        fields = "__all__"
        exclude = [
            "skill_zone_id",
            "is_active",
            "candidate_id",
        ]
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": "This candidate alreay exist in this skill zone",
            }
        }

    def clean(self):
        cleaned_data = super().clean()
        candidate = cleaned_data.get("candidate_id")
        skill_zones = cleaned_data.get("skill_zone_ids")
        skill_zone_list = []
        for skill_zone in skill_zones:
            # Check for the unique together constraint manually
            if SkillZoneCandidate.objects.filter(
                candidate_id=candidate, skill_zone_id=skill_zone
            ).exists():
                # Raise a ValidationError with a custom error message
                skill_zone_list.append(skill_zone)
        if len(skill_zone_list) > 0:
            skill_zones_str = ", ".join(
                str(skill_zone) for skill_zone in skill_zone_list
            )
            raise ValidationError(f"{candidate} already exists in {skill_zones_str}.")

            # cleaned_data['skill_zone_id'] =skill_zone
        return cleaned_data

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class RejectReasonForm(ModelForm):
    """
    RejectReasonForm
    """

    cols = {"title": 12, "description": 12, "company_id": 12}

    verbose_name = "Reject Reason"

    class Meta:
        model = RejectReason
        fields = "__all__"
        exclude = ["is_active"]

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class RejectedCandidateForm(ModelForm):
    """
    RejectedCandidateForm
    """

    verbose_name = "Rejected Candidate"

    cols = {"reject_reason_id": 12, "description": 12}

    class Meta:
        model = RejectedCandidate
        fields = "__all__"
        exclude = ["is_active"]

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reject_reason_id"].empty_label = None
        self.fields["candidate_id"].widget = self.fields["candidate_id"].hidden_widget()
        reject_reason_widget = self.fields["reject_reason_id"].widget
        existing_class = reject_reason_widget.attrs.get("class", "")
        reject_reason_widget.attrs["class"] = f"{existing_class} reject-reason-select".strip()


class ScheduleInterviewForm(BaseModelForm):
    """
    ScheduleInterviewForm
    """

    cols = {
        "interview_date": 12,
        "interview_time": 12,
        "candidate_id": 12,
        "description": 12,
        "employee_id": 12,
    }

    verbose_name = "Schedule Interview"

    class Meta:
        model = InterviewSchedule
        fields = "__all__"
        exclude = ["is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["interview_date"].widget = forms.DateInput(
            attrs={"type": "date", "class": "oh-input w-100"}
        )
        if self.instance.pk:
            # Update mode: keep this permissive and normalize manually in clean()
            # so unchanged browser values do not fail with "Enter a valid time".
            self.fields["interview_time"] = forms.CharField(
                required=False,
                widget=forms.TimeInput(
                    attrs={"type": "time", "class": "oh-input w-100"}
                ),
            )
        else:
            self.fields["interview_time"] = forms.TimeField(
                required=True,
                input_formats=["%H:%M", "%I:%M %p", "%H:%M:%S", "%I:%M:%S %p"],
                widget=forms.TimeInput(
                    attrs={"type": "time", "class": "oh-input w-100"}
                ),
            )
        candidate_attr = {
            "hx-include": "#InterviewCreateForm",
            "hx-target": "#id_employee_id_parent_div",
            "hx-get": "/recruitment/get-interview-managers/",
            "hx-swap": "innerHTML",
            "hx-select": "#id_employee_id_parent_div",
            "hx-trigger": "change, load delay:300ms",
        }

        if self.instance.pk:
            candidate_attr["hx-get"] += f"?pk={self.instance.pk}"

        self.fields["candidate_id"].widget.attrs.update(candidate_attr)

    def clean(self):

        instance = self.instance
        cleaned_data = super().clean() or {}
        interview_date = cleaned_data.get("interview_date")
        interview_time = cleaned_data.get("interview_time")
        raw_interview_time = (self.data.get("interview_time") or "").strip()
        managers = cleaned_data.get("employee_id") or []

        if instance.pk:
            parsed_time = None
            if raw_interview_time:
                for fmt in (
                    "%H:%M",
                    "%I:%M %p",
                    "%H:%M:%S",
                    "%I:%M:%S %p",
                    "%I:%M%p",
                    "%H:%M:%S.%f",
                ):
                    try:
                        parsed_time = datetime.strptime(raw_interview_time, fmt).time()
                        break
                    except ValueError:
                        continue
            cleaned_data["interview_time"] = parsed_time or instance.interview_time
            interview_time = cleaned_data.get("interview_time")

        if not instance.pk and interview_date and interview_date < date.today():
            self.add_error("interview_date", _("Interview date cannot be in the past."))

        if not instance.pk and interview_time:
            now = datetime.now().time()
            if (
                not instance.pk
                and interview_date == date.today()
                and interview_time < now
            ):
                self.add_error(
                    "interview_time", _("Interview time cannot be in the past.")
                )

        if managers and apps.is_installed("leave"):
            from leave.models import LeaveRequest

            leave_employees = LeaveRequest.objects.filter(
                employee_id__in=managers, status="approved"
            )
        else:
            leave_employees = []

        employees = [
            leave.employee_id.get_full_name()
            for leave in leave_employees
            if interview_date and interview_date in leave.requested_dates()
        ]

        if employees:
            self.add_error(
                "employee_id", _(f"{employees} have approved leave on this date")
            )

        return cleaned_data

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class SkillsForm(ModelForm):
    cols = {
        "title": 12,
    }

    class Meta:
        model = Skill
        fields = ["title"]


class PlatformForm(ModelForm):
    cols = {
        "title": 12,
    }

    class Meta:
        model = Platform
        fields = ["title"]


class ResumeForm(ModelForm):
    class Meta:
        model = Resume
        fields = ["file", "recruitment_id"]
        widgets = {"recruitment_id": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs.update(
            {
                "onchange": "submitForm($(this))",
            }
        )


class CandidateDocumentRequestForm(ModelForm):
    class Meta:
        model = CandidateDocumentRequest
        fields = "__all__"
        exclude = ["is_active"]


class CandidateDocumentUpdateForm(ModelForm):
    """form to Update a Document"""

    verbose_name = "CandidateDocument"

    class Meta:
        model = CandidateDocument
        fields = "__all__"
        exclude = ["is_active", "document_request_id"]


class CandidateDocumentRejectForm(ModelForm):
    """form to add rejection reason while rejecting a Document"""

    class Meta:
        model = CandidateDocument
        fields = ["reject_reason"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reject_reason"].widget.attrs["required"] = True


class CandidateDocumentForm(ModelForm):
    """form to create a new Document"""

    verbose_name = "Document"

    class Meta:
        model = CandidateDocument
        fields = "__all__"
        exclude = ["document_request_id", "status", "reject_reason", "is_active"]
        widgets = {
            "employee_id": forms.HiddenInput(),
        }

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("horilla_form.html", context)
        return table_html


class StageChangeForm(forms.ModelForm):
    """
    StageChangeForm
    """

    class Meta:
        """
        Meta class for additional options
        """

        model = Candidate
        fields = [
            "stage_id",
        ]


class LinkedInAccountForm(BaseModelForm):
    """
    LinkedInAccount form
    """

    class Meta:
        model = LinkedInAccount
        fields = [
            "username",
            "email",
            "api_token",
            "is_active",
            "company_id",
        ]


class CandidateIntakeMailboxForm(BaseModelForm):
    """
    CandidateIntakeMailbox form
    """

    class Meta:
        model = CandidateIntakeMailbox
        fields = [
            "provider",
            "azure_api",
            "google_api",
            "fallback_recruitment_id",
            "daily_candidate_cap",
            "is_active",
            "company_id",
        ]
        widgets = {
            "provider": forms.Select(
                attrs={"onchange": "candidateIntakeProviderChange($(this))"}
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        provider = cleaned_data.get("provider")
        azure_api = cleaned_data.get("azure_api")
        google_api = cleaned_data.get("google_api")
        if provider == "outlook":
            if not azure_api:
                self.add_error(
                    "azure_api", _("Choose an Outlook mailbox for this provider.")
                )
            if google_api:
                self.add_error(
                    "google_api", _("Leave this empty when the provider is Outlook.")
                )
        elif provider == "gmail":
            if not google_api:
                self.add_error(
                    "google_api", _("Choose a Gmail mailbox for this provider.")
                )
            if azure_api:
                self.add_error(
                    "azure_api", _("Leave this empty when the provider is Gmail.")
                )
        return cleaned_data


class AISettingsForm(BaseModelForm):
    """
    AISettings form. `provider` is driven entirely by the switch in
    recruitment/ai_settings.html (see aiProviderChange() there) - it stays
    a real <select> for the form to submit, just hidden from view, so
    there's exactly one control the user actually interacts with.
    """

    openai_api_key = forms.CharField(
        required=False,
        label=_("OpenAI API Key"),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = AISettings
        fields = [
            "provider",
            "openai_api_key",
            "openai_model",
            "ollama_base_url",
            "ollama_model",
        ]
        widgets = {
            "provider": forms.Select(attrs={"onchange": "aiProviderChange($(this))"}),
        }
