"""
vertex/forms_reviews.py - review and minutes forms.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from vertex.models import (
    CodeReview,
    MeetingMinute,
    Project,
    ProjectReview,
    ReviewType,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


def _people():
    return Employee.objects.filter(is_active=True).order_by(
        "employee_first_name", "employee_last_name"
    )


class ReviewRequestForm(forms.Form):
    """
    Request a review for one or many employees. When a meeting time is given the reviews
    are created already scheduled (ArielVertex ScheduleGroup), otherwise they stay Open.
    """

    project = forms.ModelChoiceField(queryset=Project.objects.none(), label=_("Project"))
    subjects = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.none(), label=_("Employees to review")
    )
    review_type = forms.ChoiceField(choices=ReviewType.choices, label=_("Review type"))
    assigned_to = forms.ModelChoiceField(
        queryset=Employee.objects.none(), required=False, label=_("Reviewer")
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label=_("Notes"))
    due_date = forms.DateField(widget=_DATE, required=False, label=_("Due date"))
    # optional meeting
    title = forms.CharField(max_length=200, required=False, label=_("Meeting title"))
    scheduled_at = forms.DateTimeField(widget=_DATETIME, required=False, label=_("Meeting time"))
    duration_minutes = forms.IntegerField(min_value=5, max_value=480, initial=30, required=False, label=_("Minutes"))
    meeting_url = forms.URLField(required=False, label=_("Meeting link"))
    extra_attendees = forms.CharField(required=False, label=_("Extra attendee emails"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.order_by("name")
        self.fields["subjects"].queryset = _people()
        self.fields["assigned_to"].queryset = _people()

    def clean(self):
        data = super().clean()
        if data.get("scheduled_at") and not data.get("title"):
            self.add_error("title", _("Give the meeting a title."))
        return data


class ScheduleForm(forms.Form):
    title = forms.CharField(max_length=200, label=_("Meeting title"))
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label=_("Description"))
    scheduled_at = forms.DateTimeField(widget=_DATETIME, label=_("Meeting time"))
    duration_minutes = forms.IntegerField(min_value=5, max_value=480, initial=30, label=_("Minutes"))
    meeting_url = forms.URLField(required=False, label=_("Meeting link"))
    assigned_to = forms.ModelChoiceField(
        queryset=Employee.objects.none(), required=False, label=_("Reviewer")
    )
    extra_attendees = forms.CharField(required=False, label=_("Extra attendee emails"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _people()


class ProjectReviewForm(forms.ModelForm):
    class Meta:
        model = ProjectReview
        fields = [
            "delivery_rating", "ownership_rating", "collaboration_rating",
            "strengths", "observations", "action_items",
        ]
        widgets = {f: forms.Textarea(attrs={"rows": 2}) for f in ("strengths", "observations", "action_items")}


class CodeReviewForm(forms.ModelForm):
    class Meta:
        model = CodeReview
        fields = [
            "code_quality_rating", "architecture_rating", "testing_rating",
            "strengths", "observations", "blockers", "action_items",
        ]
        widgets = {
            f: forms.Textarea(attrs={"rows": 2})
            for f in ("strengths", "observations", "blockers", "action_items")
        }


class MinutesForm(forms.ModelForm):
    class Meta:
        model = MeetingMinute
        fields = ["title", "meeting_date", "location", "attendees", "raw_notes", "minutes_text"]
        widgets = {
            "meeting_date": _DATETIME,
            "attendees": forms.Textarea(attrs={"rows": 2}),
            "raw_notes": forms.Textarea(attrs={"rows": 8}),
            "minutes_text": forms.Textarea(attrs={"rows": 14}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["raw_notes"].required = True
        self.fields["minutes_text"].required = False
        if not (self.instance.pk and self.instance.minutes_text):
            del self.fields["minutes_text"]  # nothing to correct before the first generate
