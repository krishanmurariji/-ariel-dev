"""
vertex/forms.py
"""

import os

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from employee.models import Employee
from vertex.models import (
    Project,
    ProjectCall,
    ProjectComment,
    ProjectDocument,
    ProjectMember,
    ResourceRequest,
    ResourceRequestComment,
    ResourceRequestStatus,
    StatusUpdate,
)

MAX_UPLOAD_BYTES = 30 * 1000 * 1000  # same limit as ArielVertex
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1", ".vbs", ".js", ".jar",
    ".html", ".htm", ".svg", ".php", ".sh", ".dll",
}

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ProjectForm(ModelForm):
    vm_icon = "folder-open-outline"
    vm_hint = "Code, dates and ownership of the project."
    cols = {"description": 12, "notes": 12}

    class Meta:
        model = Project
        fields = [
            "code", "name", "description", "status", "health", "priority", "start_date",
            "expected_end_date", "client_name", "business_owner", "tags", "notes",
        ]
        widgets = {
            "start_date": _DATE,
            "expected_end_date": _DATE,
            "description": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # the project code is its identity, as in ArielVertex (update does not change it)
            self.fields["code"].disabled = True

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("expected_end_date")
        if start and end and end < start:
            self.add_error("expected_end_date", _("The end date cannot be before the start date."))
        return data


class MemberForm(ModelForm):
    vm_icon = "person-add-outline"
    vm_hint = "Add someone to this project and set their allocation."

    class Meta:
        model = ProjectMember
        fields = ["employee", "role_on_project", "allocation_pct", "start_date", "end_date"]
        widgets = {"start_date": _DATE, "end_date": _DATE}
        labels = {"allocation_pct": _("Allocation (%)"), "role_on_project": _("Role on project")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True).order_by(
            "employee_first_name", "employee_last_name"
        )

    def clean_allocation_pct(self):
        value = self.cleaned_data["allocation_pct"]
        if not 0 <= value <= 100:
            raise forms.ValidationError(_("Allocation must be between 0 and 100."))
        return value


class DocumentForm(ModelForm):
    vm_icon = "document-text-outline"
    vm_hint = "Upload a file (up to 30 MB) or link a recording."

    class Meta:
        model = ProjectDocument
        fields = ["title", "description", "category", "visibility", "is_video_link", "video_url", "file"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        labels = {"is_video_link": _("This is a video link"), "video_url": _("Video URL"), "file": _("File")}

    cols = {"title": 12, "description": 12, "file": 12, "video_url": 12}

    def clean(self):
        data = super().clean()
        if data.get("is_video_link"):
            if not data.get("video_url"):
                self.add_error("video_url", _("Enter the video link."))
            data["file"] = None
        else:
            upload = data.get("file")
            if not upload:
                self.add_error("file", _("Please choose a file to upload."))
            else:
                if upload.size > MAX_UPLOAD_BYTES:
                    self.add_error("file", _("The file is larger than 30 MB."))
                if os.path.splitext(upload.name)[1].lower() in BLOCKED_EXTENSIONS:
                    self.add_error("file", _("This type of file is not allowed."))
        return data


class CommentForm(ModelForm):
    class Meta:
        model = ProjectComment
        fields = ["message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 2})}

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError(_("Comment cannot be empty."))
        return message


class CallForm(ModelForm):
    vm_icon = "calendar-outline"
    vm_hint = "Schedule a customer or internal call."

    class Meta:
        model = ProjectCall
        fields = [
            "title", "agenda", "call_type", "scheduled_at", "duration_minutes",
            "attendees", "meeting_url",
        ]
        widgets = {"scheduled_at": _DATETIME, "agenda": forms.Textarea(attrs={"rows": 2})}
        labels = {"duration_minutes": _("Duration (min)"), "meeting_url": _("Meeting link"), "scheduled_at": _("Date and time")}

    cols = {"title": 12, "agenda": 12, "call_type": 4, "scheduled_at": 4, "duration_minutes": 4, "attendees": 12, "meeting_url": 12}


class StatusUpdateForm(ModelForm):
    """Submit a status update; only for projects the person is an active member of."""
    vm_icon = "pulse-outline"
    vm_hint = "Today's progress on a project you are assigned to."

    cols = {
        "project": 6, "update_date": 6, "hours_spent": 6, "status": 6, "work_completed": 12,
        "next_planned_work": 6, "blockers": 6, "dependencies": 12,
        "client_shareable_summary": 6, "internal_note": 6,
    }
    vm_sections = {"work_completed": "Progress", "client_shareable_summary": "Visibility"}

    class Meta:
        model = StatusUpdate
        fields = [
            "project", "update_date", "hours_spent", "status", "work_completed",
            "next_planned_work", "blockers", "dependencies", "client_shareable_summary",
            "internal_note",
        ]
        widgets = {
            "update_date": _DATE,
            **{
                name: forms.Textarea(attrs={"rows": 2})
                for name in (
                    "work_completed", "next_planned_work", "blockers", "dependencies",
                    "client_shareable_summary", "internal_note",
                )
            },
        }

    def __init__(self, *args, **kwargs):
        from horilla.horilla_middlewares import _thread_locals
        from vertex import access

        super().__init__(*args, **kwargs)
        request = getattr(_thread_locals, "request", None)
        user = getattr(request, "user", None)
        self.fields["project"].queryset = (
            access.assignable_projects(user) if user is not None else Project.objects.none()
        )
        if not self.instance.pk:
            self.fields["update_date"].initial = timezone.localdate()

    def clean_update_date(self):
        value = self.cleaned_data["update_date"]
        if value > timezone.localdate():
            raise forms.ValidationError(_("The date cannot be in the future."))
        return value

    def clean_work_completed(self):
        value = self.cleaned_data["work_completed"].strip()
        if not value:
            raise forms.ValidationError(_("Describe the work completed."))
        return value


class ResourceRequestForm(ModelForm):
    """Raise a hiring / resource request for a project the person can open."""
    vm_icon = "people-outline"
    vm_hint = "Ask HR for more people on a project."

    cols = {"project": 6, "role_title": 6, "count": 4, "priority": 4, "expected_start_date": 4, "skills": 12, "reason": 12}

    class Meta:
        model = ResourceRequest
        fields = [
            "project", "role_title", "count", "priority", "expected_start_date",
            "skills", "reason",
        ]
        labels = {"role_title": _("Role"), "count": _("People needed"), "expected_start_date": _("Expected start")}
        widgets = {
            "expected_start_date": _DATE,
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        from horilla.horilla_middlewares import _thread_locals
        from vertex import access

        super().__init__(*args, **kwargs)
        request = getattr(_thread_locals, "request", None)
        user = getattr(request, "user", None)
        self.fields["project"].queryset = (
            access.visible_projects(user).order_by("name")
            if user is not None
            else Project.objects.none()
        )

    def clean_role_title(self):
        value = self.cleaned_data["role_title"].strip()
        if not value:
            raise forms.ValidationError(_("Enter the role title."))
        return value

    def clean_expected_start_date(self):
        value = self.cleaned_data["expected_start_date"]
        if value and value < timezone.localdate():
            raise forms.ValidationError(_("The expected start cannot be in the past."))
        return value


class ResourceStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ResourceRequestStatus.choices, label=_("Status"))
    comment = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label=_("Comment")
    )


class ResourceCommentForm(ModelForm):
    class Meta:
        model = ResourceRequestComment
        fields = ["message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 2})}

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError(_("Comment cannot be empty."))
        return message
