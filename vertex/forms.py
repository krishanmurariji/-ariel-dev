"""
vertex/forms.py
"""

import os

from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from vertex.models import (
    Project,
    ProjectCall,
    ProjectComment,
    ProjectDocument,
    ProjectMember,
)

MAX_UPLOAD_BYTES = 30 * 1000 * 1000  # same limit as ArielVertex
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1", ".vbs", ".js", ".jar",
    ".html", ".htm", ".svg", ".php", ".sh", ".dll",
}

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
_DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ProjectForm(forms.ModelForm):
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


class MemberForm(forms.ModelForm):
    class Meta:
        model = ProjectMember
        fields = ["employee", "role_on_project", "allocation_pct", "start_date", "end_date"]
        widgets = {"start_date": _DATE, "end_date": _DATE}

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


class DocumentForm(forms.ModelForm):
    class Meta:
        model = ProjectDocument
        fields = ["title", "description", "category", "visibility", "is_video_link", "video_url", "file"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

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


class CommentForm(forms.ModelForm):
    class Meta:
        model = ProjectComment
        fields = ["message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 2})}

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError(_("Comment cannot be empty."))
        return message


class CallForm(forms.ModelForm):
    class Meta:
        model = ProjectCall
        fields = [
            "title", "agenda", "call_type", "scheduled_at", "duration_minutes",
            "attendees", "meeting_url",
        ]
        widgets = {"scheduled_at": _DATETIME, "agenda": forms.Textarea(attrs={"rows": 2})}
