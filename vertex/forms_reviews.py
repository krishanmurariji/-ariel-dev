"""
vertex/forms_reviews.py - review and minutes forms.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from vertex.forms import CALL_AGENDA_LIMIT, CALL_TITLE_LIMIT, ModelForm, cap_text, clean_email_list
from vertex.models import (
    CodeReview,
    MeetingMinute,
    Project,
    ProjectReview,
    ReviewRequest,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


def _people():
    return Employee.objects.filter(is_active=True).order_by(
        "employee_first_name", "employee_last_name"
    )


class ReviewRequestForm(ModelForm):
    vm_icon = "checkmark-done-outline"
    vm_hint = "Pick one or more people. Add a meeting time to schedule them straight away."
    vm_add_another = False
    vm_sections = {"title": "Schedule a meeting (optional)"}
    """
    Request a review for one or many employees. When a meeting time is given the reviews
    are created already scheduled (ArielVertex ScheduleGroup), otherwise they stay Open.
    The form is never saved directly: the view creates one request per selected employee.
    """

    cols = {"project": 6, "review_type": 6, "subjects": 12, "assigned_to": 6, "due_date": 6, "notes": 12, "extra_attendees": 12}

    subjects = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.none(), label=_("Employees to review")
    )
    # optional meeting
    title = forms.CharField(max_length=200, required=False, label=_("Meeting title"))
    scheduled_at = forms.DateTimeField(widget=_DATETIME, required=False, label=_("Meeting time"))
    duration_minutes = forms.IntegerField(
        min_value=5, max_value=480, initial=30, required=False, label=_("Minutes")
    )
    extra_attendees = forms.CharField(required=False, label=_("Extra attendee emails"))

    class Meta:
        model = ReviewRequest
        fields = ["project", "review_type", "assigned_to", "due_date", "notes"]
        labels = {"assigned_to": _("Reviewer")}
        widgets = {"due_date": _DATE, "notes": forms.Textarea(attrs={"rows": 2})}

    field_order = [
        "project", "review_type", "subjects", "assigned_to", "due_date", "notes",
        "title", "scheduled_at", "duration_minutes", "extra_attendees",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.order_by("name")
        self.fields["subjects"].queryset = _people()
        self.fields["assigned_to"].queryset = _people()
        self.fields["assigned_to"].required = False
        cap_text(self, "title", CALL_TITLE_LIMIT)

    def clean(self):
        data = super().clean()
        if data.get("scheduled_at") and not data.get("title"):
            self.add_error("title", _("Give the meeting a title."))
        return data

    def clean_extra_attendees(self):
        return clean_email_list(self.cleaned_data["extra_attendees"])


class ScheduleForm(forms.Form):
    # layout on the review page: a 6-column grid, see spans
    spans = {
        "title": 3, "scheduled_at": 3, "duration_minutes": 2,
        "assigned_to": 4, "extra_attendees": 6, "description": 6,
    }

    title = forms.CharField(max_length=200, label=_("Meeting title"))
    scheduled_at = forms.DateTimeField(widget=_DATETIME, label=_("Meeting time"))
    duration_minutes = forms.IntegerField(min_value=5, max_value=480, initial=30, label=_("Minutes"))
    assigned_to = forms.ModelChoiceField(
        queryset=Employee.objects.none(), required=False, label=_("Reviewer")
    )
    extra_attendees = forms.CharField(required=False, label=_("Extra attendee emails"))
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label=_("Description"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _people()
        cap_text(self, "title", CALL_TITLE_LIMIT)
        cap_text(self, "description", CALL_AGENDA_LIMIT)

    def clean_extra_attendees(self):
        return clean_email_list(self.cleaned_data["extra_attendees"])


class _RatingsMixin:
    """The model field is a PositiveSmallIntegerField, whose form field forces min=0; ratings start at 1."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name.endswith("_rating"):
                field.widget.attrs.update({"min": 1, "max": 5})


class ProjectReviewForm(_RatingsMixin, forms.ModelForm):
    spans = {
        "delivery_rating": 2, "ownership_rating": 2, "collaboration_rating": 2,
        "strengths": 3, "observations": 3, "action_items": 6,
    }

    class Meta:
        model = ProjectReview
        fields = [
            "delivery_rating", "ownership_rating", "collaboration_rating",
            "strengths", "observations", "action_items",
        ]
        widgets = {
            **{f: forms.Textarea(attrs={"rows": 2}) for f in ("strengths", "observations", "action_items")},
            **{f: forms.NumberInput(attrs={"min": 1, "max": 5, "placeholder": "1 to 5"}) for f in ("delivery_rating", "ownership_rating", "collaboration_rating")},
        }


class CodeReviewForm(_RatingsMixin, forms.ModelForm):
    spans = {
        "code_quality_rating": 2, "architecture_rating": 2, "testing_rating": 2,
        "strengths": 3, "observations": 3, "blockers": 3, "action_items": 3,
    }

    class Meta:
        model = CodeReview
        fields = [
            "code_quality_rating", "architecture_rating", "testing_rating",
            "strengths", "observations", "blockers", "action_items",
        ]
        widgets = {
            **{f: forms.Textarea(attrs={"rows": 2}) for f in ("strengths", "observations", "blockers", "action_items")},
            **{f: forms.NumberInput(attrs={"min": 1, "max": 5, "placeholder": "1 to 5"}) for f in ("code_quality_rating", "architecture_rating", "testing_rating")},
        }


class MinutesForm(ModelForm):
    vm_icon = "document-text-outline"
    vm_hint = "Capture the notes now; generate the minutes on the next page."
    vm_add_another = False
    cols = {"title": 6, "meeting_date": 6, "location": 12, "attendees": 12, "raw_notes": 12, "minutes_text": 12}

    class Meta:
        model = MeetingMinute
        fields = ["title", "meeting_date", "location", "attendees", "raw_notes", "minutes_text"]
        widgets = {
            "meeting_date": _DATETIME,
            "attendees": forms.Textarea(attrs={"rows": 2}),
            "raw_notes": forms.Textarea(attrs={"rows": 8, "style": "min-height:190px"}),
            "minutes_text": forms.Textarea(attrs={"rows": 14, "style": "min-height:280px"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["raw_notes"].required = True
        self.fields["minutes_text"].required = False
        if not (self.instance.pk and self.instance.minutes_text):
            del self.fields["minutes_text"]  # nothing to correct before the first generate
