"""
offboarding/forms.py

This module is used to register forms for offboarding app
"""

import contextlib
from datetime import date
from typing import Any

import bleach
from bleach.css_sanitizer import CSSSanitizer
from django import forms
from django.contrib import messages
from django.template.loader import render_to_string
from django.utils.html import escape, linebreaks
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from employee.forms import MultipleFileField
from employee.models import Employee
from horilla import horilla_middlewares
from horilla.models import has_xss
from notifications.signals import notify
from offboarding.form_fields import TextCharField
from offboarding.exit_policy import notice_period_days, notice_period_end
from offboarding.exit_service import _settings_for, settle_new_request
from offboarding.mailers import notify_manager_of_resignation
from offboarding.models import (
    EmployeeTask,
    ExitReasonOption,
    Offboarding,
    OffboardingEmployee,
    OffboardingNote,
    OffboardingStage,
    OffboardingStageMultipleFile,
    OffboardingTask,
    ResignationLetter,
)


class OffboardingForm(ModelForm):
    """
    OffboardingForm model form class
    """

    verbose_name = "Offboarding"

    cols = {"title": 12, "description": 12, "managers": 12, "status": 12}

    class Meta:
        model = Offboarding
        fields = "__all__"
        exclude = ["is_active"]

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class OffboardingStageForm(ModelForm):
    """
    OffboardingStage model form
    """

    verbose_name = "Stage"

    cols = {"title": 12, "type": 12, "managers": 12}

    class Meta:
        model = OffboardingStage
        fields = "__all__"
        exclude = ["is_active"]

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html


class OffboardingEmployeeForm(ModelForm):
    """
    OffboardingEmployeeForm model form
    """

    verbose_name = "Offboarding "

    class Meta:
        model = OffboardingEmployee
        fields = "__all__"
        exclude = ["notice_period", "unit", "is_active"]
        widgets = {
            "notice_period_starts": forms.DateInput(attrs={"type": "date"}),
            "notice_period_ends": forms.DateInput(attrs={"type": "date"}),
        }

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        attrs = self.fields["employee_id"].widget.attrs
        attrs["onchange"] = "initialNoticePeriod($(this))"
        self.fields["employee_id"].widget.attrs.update(attrs)
        attrs = self.fields["notice_period_starts"].widget.attrs
        attrs["onchange"] = "noticePeriodUpdate($(this))"
        self.fields["notice_period_starts"].widget.attrs.update(attrs)
        if self.instance.pk:
            if self.instance.notice_period_starts:
                self.initial["notice_period_starts"] = (
                    self.instance.notice_period_starts.strftime("%Y-%m-%d")
                )
            if self.instance.notice_period_ends:
                self.initial["notice_period_ends"] = (
                    self.instance.notice_period_ends.strftime("%Y-%m-%d")
                )


class StageSelectForm(ModelForm):
    """
    This form is used to register drop down for the pipeline
    """

    class Meta:
        model = OffboardingEmployee
        fields = [
            "stage_id",
        ]

    def __init__(self, *args, offboarding=None, **kwargs):
        super().__init__(*args, **kwargs)
        attrs = self.fields["stage_id"].widget.attrs
        attrs["onchange"] = "offboardingUpdateStage($(this))"
        attrs["class"] = "w-100 oh-custom-select"
        self.fields["stage_id"].widget.attrs.update(attrs)
        self.fields["stage_id"].empty_label = None
        self.fields["stage_id"].queryset = OffboardingStage.objects.filter(
            offboarding_id=offboarding
        )
        self.fields["stage_id"].label = ""


class NoteForm(ModelForm):
    """
    Offboarding note model form
    """

    verbose_name = "Add Note"

    class Meta:
        model = OffboardingNote
        fields = "__all__"
        exclude = ["is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attachment"] = MultipleFileField(label="Attachements")
        self.fields["attachment"].required = False

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def save(self, commit: bool = ...) -> Any:
        multiple_attachment_ids = []
        attachments = None
        if self.files.getlist("attachment"):
            attachments = self.files.getlist("attachment")
            self.instance.attachment = attachments[0]
            multiple_attachment_ids = []
            for attachment in attachments:
                file_instance = OffboardingStageMultipleFile()
                file_instance.attachment = attachment
                file_instance.save()
                multiple_attachment_ids.append(file_instance.pk)
        instance = super().save(commit)
        if commit:
            instance.attachments.add(*multiple_attachment_ids)
        return instance, attachments


class TaskForm(ModelForm):
    """
    TaskForm model form
    """

    verbose_name = "Offboarding Task"
    tasks_to = forms.ModelMultipleChoiceField(
        queryset=OffboardingEmployee.objects.all(), required=False, label=_("Task To")
    )
    cols = {"title": 12, "managers": 12, "stage_id": 12, "tasks_to": 12}

    class Meta:
        model = OffboardingTask
        fields = "__all__"
        exclude = ["status", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage_id"].empty_label = "All Stages in Offboarding"
        self.fields["managers"].empty_label = None
        if not self.instance.pk:
            queryset = OffboardingEmployee.objects.filter(
                stage_id__offboarding_id=OffboardingStage.objects.filter(
                    id=self.initial.get("stage_id")
                )
                .first()
                .offboarding_id
            )
            self.fields["tasks_to"].queryset = queryset

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def save(self, commit: bool = ...) -> Any:
        super().save(commit)
        if commit:
            employees = self.cleaned_data["tasks_to"]
            for employee in employees:
                assigned_task = EmployeeTask.objects.get_or_create(
                    employee_id=employee,
                    task_id=self.instance,
                )


# Allowlist for the Summernote-edited ResignationLetter.description field,
# shown unescaped (|safe) to HR/managers reviewing resignations - mirrors
# the bleach.clean() pattern already used for other Summernote fields
# (recruitment/forms.py RecruitmentCreationForm.clean_description,
# onboarding/forms.py _sanitize_rich_text) so stored HTML can't carry a
# stored-XSS payload.
_DESCRIPTION_ALLOWED_TAGS = [
    "p", "br", "b", "strong", "i", "em", "u", "s", "strike",
    "ul", "ol", "li", "a", "span", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
]
_DESCRIPTION_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target"],
    "span": ["style"],
    "p": ["style"],
}
_DESCRIPTION_ALLOWED_CSS_PROPERTIES = [
    "color", "background-color", "font-size", "font-weight",
    "text-align", "text-decoration",
]


class ResignationLetterForm(ModelForm):
    """
    Resignation Letter
    """

    cols = {
        "employee_id": 12,
        "title": 12,
        "description": 12,
        "planned_to_leave_on": 12,
        "status": 12,
    }

    description = forms.CharField(
        widget=forms.Textarea(attrs={"data-summernote": "", "style": "display:none;"}),
        label="Description",
    )
    verbose_name = "Resignation Letter"

    class Meta:
        model = ResignationLetter
        fields = "__all__"
        # The fields below belong to the employee's own "Resign from job"
        # form (SelfResignationForm) - not part of the HR form.
        exclude = [
            "is_active",
            "reason",
            "discussed_with_manager",
            "discussion_summary",
            "preferred_last_working_day",
            "notice_period_days",
            "notice_period_ends",
            "additional_comment",
            "exit_type",
            "notice_date",
            "review_comment",
            "reviewed_at",
            "attachment",
        ]

    def clean_description(self):
        description = self.cleaned_data.get("description")
        if not description:
            return description
        return bleach.clean(
            description,
            tags=_DESCRIPTION_ALLOWED_TAGS,
            attributes=_DESCRIPTION_ALLOWED_ATTRIBUTES,
            css_sanitizer=CSSSanitizer(
                allowed_css_properties=_DESCRIPTION_ALLOWED_CSS_PROPERTIES
            ),
            strip=True,
        )

    def as_p(self):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("common_form.html", context)
        return table_html

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["planned_to_leave_on"].widget = forms.DateInput(
            attrs={"type": "date", "class": "oh-input w-100"}
        )
        exclude = []
        if self.instance.pk:
            exclude.append("employee_id")
            self.verbose_name = (
                self.instance.employee_id.get_full_name() + "'s Resignation Letter"
            )

        request = getattr(horilla_middlewares._thread_locals, "request", None)
        if request and not request.user.has_perm("offboarding.add_resignationletter"):
            exclude = exclude + ["status"]
            self.fields["employee_id"].queryset = Employee.objects.filter(
                employee_user_id=request.user
            )
            self.fields["employee_id"].initial = request.user.employee_get
            self.instance.employee_id = request.user.employee_get
        if request and request.user.has_perm("offboarding.add_resignationletter"):
            if request.GET.get("emp_id"):
                emp_id = request.GET.get("emp_id")
                self.fields["employee_id"].queryset = Employee.objects.filter(id=emp_id)
                self.fields["employee_id"].initial = emp_id
        print("employee queryset:", self.fields["employee_id"].queryset)
        exclude = list(set(exclude))
        for field in exclude:
            del self.fields[field]

    def save(self, commit: bool = ...) -> Any:
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        instance = self.instance
        if (
            not request.user.has_perm("offboarding.add_resignationletter")
            and instance.status == "requested"
        ) or request.user.has_perm("add_resignationletter"):
            instance = super().save(commit)
        else:
            messages.info(
                request, "You cannot edit a request that has been rejected/approved"
            )
            return None

        if (
            instance.status == "requested"
            and request
            and not request.user.has_perm("offboarding.add_resignationletter")
        ):
            from horilla.access import VIEW, managers_for

            # the reporting manager and, if they may see resignations, the second manager
            for manager in managers_for(self.instance.employee_id, "resignation", VIEW):
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=manager.employee_user_id,
                        verb=f"{self.instance.employee_id.get_full_name()} requested for resignation.",
                        verb_ar=f"",
                        verb_de=f"",
                        verb_es=f"",
                        verb_fr=f"",
                        redirect="#",
                        icon="information",
                    )
        return instance


class SelfResignationForm(ModelForm):
    """
    The employee's own "Resign from job" form. It always files the resignation
    for the logged-in employee - there is no employee field to tamper with -
    and records why they are leaving, whether they spoke to their manager, and
    any preference for an earlier last working day than the notice period
    ends. Saving it also tells their reporting manager (in-app and by email).
    """

    verbose_name = _("Resign from job")
    YES_NO = [("yes", _("Yes")), ("no", _("No"))]

    class Meta:
        model = ResignationLetter
        fields = []

    discussed_with_manager = forms.ChoiceField(
        label=_("Did you have discussion with manager on your decision to exit?"),
        choices=YES_NO,
        initial="yes",
        widget=forms.RadioSelect,
    )
    discussion_summary = TextCharField(
        label=_("Please write the summary of your discussion"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Type here")}),
    )
    reason = forms.ChoiceField(
        label=_("Please select a reason for your resignation"),
        choices=[("", _("Select reason"))],
        error_messages={"required": _("Resignation reason is required")},
    )
    early_last_working_day = forms.ChoiceField(
        label=_("Any preference of an early last working day?"),
        required=False,
        choices=YES_NO,
        initial="no",
        widget=forms.RadioSelect,
    )
    preferred_last_working_day = forms.DateField(
        label=_("Last working day"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    additional_comment = TextCharField(
        label=_("Additional comment"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Type here")}),
    )
    notify_employees = forms.ModelMultipleChoiceField(
        label=_("Persons to notify"),
        required=False,
        queryset=Employee.objects.none(),
        help_text=_(
            "Optional. Anyone you choose receives the same email as your "
            "reporting manager."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        self.employee = request.user.employee_get
        self.fields["notify_employees"].queryset = (
            Employee.objects.filter(is_active=True)
            .exclude(pk=self.employee.pk)
            .select_related("employee_work_info__job_position_id")
            .order_by("employee_first_name", "employee_last_name")
        )
        self.fields["notify_employees"].label_from_instance = self._person_label
        # Binds the letter to the employee up front so the model's own
        # "already has a pending/approved resignation" check reports as a
        # normal form error instead of failing later at save time.
        self.instance.employee_id = self.employee

        company_id = getattr(
            getattr(self.employee, "employee_work_info", None), "company_id_id", None
        )
        titles = list(
            ExitReasonOption.for_company("resignation", company_id).values_list(
                "title", flat=True
            )
        ) or [str(label) for label in ResignationLetter.legacy_reasons.values()]
        self.fields["reason"].choices = [("", _("Select reason"))] + [
            (title, title) for title in titles
        ]

        self.today = date.today()
        self.notice_days = notice_period_days(self.employee)
        self.notice_end = notice_period_end(self.notice_days, self.today)
        # With no notice policy to default to, the employee has to say when
        # they want to leave - there is no "early" about it.
        self.date_is_mandatory = self.notice_end is None
        # HR can switch the question off in Exit Settings
        settings = _settings_for(self.employee)
        self.early_allowed = settings.allow_early_last_working_day
        # "Consider the resignation date as the start of the notice period" off:
        # the notice only starts once the resignation is accepted, so its end
        # is not known yet (the date below is just the latest an early day can be).
        self.notice_from_approval = (
            not settings.notice_starts_on_resignation_date and self.notice_end is not None
        )
        self.fields["preferred_last_working_day"].initial = None

    @staticmethod
    def _person_label(employee):
        position = employee.get_job_position()
        name = employee.get_full_name()
        return f"{name} - {position}" if position else name

    def _reject_script_like_text(self, name):
        # The model layer refuses script-like text outright (has_xss) and
        # would raise at save time - catch it here so the employee gets a
        # normal message next to the field instead of a bare error page.
        value = self.cleaned_data.get(name) or ""
        if has_xss(value):
            raise forms.ValidationError(
                _(
                    "This text contains content that is not allowed (such as "
                    "script tags or event handlers). Please remove it."
                )
            )
        return value

    def clean_discussion_summary(self):
        return self._reject_script_like_text("discussion_summary")

    def clean_additional_comment(self):
        return self._reject_script_like_text("additional_comment")

    def clean(self):
        cleaned = super().clean()

        if cleaned.get("discussed_with_manager") == "yes" and not (
            cleaned.get("discussion_summary") or ""
        ).strip():
            self.add_error("discussion_summary", _("Summary is required"))

        wants_date = self.date_is_mandatory or (
            self.early_allowed and cleaned.get("early_last_working_day") == "yes"
        )
        chosen = cleaned.get("preferred_last_working_day")
        if wants_date:
            if not chosen:
                if "preferred_last_working_day" not in self.errors:
                    self.add_error(
                        "preferred_last_working_day", _("Please select a date")
                    )
            elif chosen < self.today:
                self.add_error(
                    "preferred_last_working_day",
                    _("The last working day cannot be in the past"),
                )
            elif self.notice_end and chosen > self.notice_end:
                self.add_error(
                    "preferred_last_working_day",
                    _("Please choose a date on or before %(date)s")
                    % {"date": self.notice_end.strftime("%d %b %Y")},
                )
        else:
            cleaned["preferred_last_working_day"] = None
        return cleaned

    def save(self, commit=True):
        data = self.cleaned_data
        letter = self.instance
        employee = self.employee
        discussed = data["discussed_with_manager"] == "yes"
        preferred = data.get("preferred_last_working_day")

        letter.employee_id = employee
        letter.title = f"Resignation - {employee.get_full_name()}"[:100]
        letter.reason = data["reason"]
        letter.reason_option = ExitReasonOption.objects.entire().filter(
            title=data["reason"], exit_type="resignation"
        ).first()
        letter.discussed_with_manager = discussed
        letter.discussion_summary = (data.get("discussion_summary") or "") if discussed else ""
        letter.preferred_last_working_day = preferred
        letter.notice_period_days = self.notice_days
        letter.notice_period_ends = None if self.notice_from_approval else self.notice_end
        letter.additional_comment = data.get("additional_comment") or ""
        letter.planned_to_leave_on = preferred or self.notice_end
        # Shown in the Resignations list/detail, which render a free-text
        # description - the employee's own comment, or failing that a line
        # naming the reason, so it is never blank.
        letter.description = linebreaks(
            escape(letter.additional_comment)
            or escape(
                _("Resigning - reason: %(reason)s")
                % {"reason": letter.get_reason_col()}
            )
        )
        letter.status = "requested"
        letter.save()
        letter.notify_employees.set(data.get("notify_employees") or [])

        from horilla.access import VIEW, managers_for

        recipients = list(managers_for(employee, "resignation", VIEW))
        for person in letter.notify_employees.all():
            if all(person.pk != known.pk for known in recipients):
                recipients.append(person)
        for manager in recipients:
            if not manager.employee_user_id:
                continue
            with contextlib.suppress(Exception):
                notify.send(
                    employee,
                    recipient=manager.employee_user_id,
                    verb=f"{employee.get_full_name()} requested for resignation.",
                    verb_ar="",
                    verb_de="",
                    verb_es="",
                    verb_fr="",
                    redirect="#",
                    icon="information",
                )
        self.manager_mailed = notify_manager_of_resignation(letter)
        # Under "Acknowledgement" review mode the exit starts straight away.
        settle_new_request(letter)
        return letter
