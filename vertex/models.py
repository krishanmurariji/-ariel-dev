"""
vertex/models.py

Ariel Vertex projects, ported from ArielVertex (Project, ProjectMember, ProjectDocument,
ProjectComment, ProjectCall). ArielVertex is the source of truth.

Access is plain Ariel HR Groups and Permissions. Every model gets the standard
view/add/change/delete permissions, which appear in the existing permission grid:

* view_project         see the Vertex Projects pages (only projects the user is a member of)
* view_allprojects     see every project   (proxy model "Project - all projects")
* add_project          create projects
* change_project       edit a project the user manages (PM / PC on it)
* change_allprojects   edit any project
* add/change/delete_projectmember, add/delete_projectdocument, add_projectcall ...

Superusers (admin) always pass every permission check, as in the rest of Ariel HR.
"""

import os

from django.db import models
from django.utils.translation import gettext_lazy as _


class ProjectStatus(models.TextChoices):
    PLANNING = "planning", _("Planning")
    ACTIVE = "active", _("Active")
    ON_HOLD = "on_hold", _("On hold")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class ProjectHealth(models.TextChoices):
    GREEN = "green", _("Green")
    AMBER = "amber", _("Amber")
    RED = "red", _("Red")


class Priority(models.IntegerChoices):
    LOW = 0, _("Low")
    MEDIUM = 1, _("Medium")
    HIGH = 2, _("High")
    CRITICAL = 3, _("Critical")


class ProjectRole(models.TextChoices):
    """Role a person holds on one specific project."""

    PROJECT_MANAGER = "project_manager", _("Project Manager")
    PROJECT_COORDINATOR = "project_coordinator", _("Project Coordinator")
    TECHNICAL_LEAD = "technical_lead", _("Technical Lead")
    BUSINESS_PERSON = "business_person", _("Business Person")
    DEVELOPER = "developer", _("Developer")
    QA = "qa", _("QA")
    MEMBER = "member", _("Member")


class DocumentCategory(models.TextChoices):
    REQUIREMENT = "requirement", _("Requirement")
    TECHNICAL = "technical", _("Technical")
    DEMO = "demo", _("Demo")
    CUSTOMER_CALL_RECORDING = "customer_call_recording", _("Customer call recording")
    MEETING_NOTES = "meeting_notes", _("Meeting notes")
    DEPLOYMENT = "deployment", _("Deployment")
    SCOPE = "scope", _("Scope")
    OTHER = "other", _("Other")


class Visibility(models.IntegerChoices):
    """Who may see a document. Ascending = more open."""

    INTERNAL = 0, _("Internal")
    BUSINESS_VISIBLE = 1, _("Business visible")
    CLIENT_SHAREABLE = 2, _("Client shareable")


class CallType(models.TextChoices):
    CUSTOMER = "customer", _("Customer")
    INTERNAL = "internal", _("Internal")


class Project(models.Model):
    """Project master record and root of the project workspace."""

    code = models.CharField(max_length=30, unique=True, verbose_name=_("Code"))
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    description = models.TextField(blank=True, default="", verbose_name=_("Description"))
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )
    health = models.CharField(
        max_length=10, choices=ProjectHealth.choices, default=ProjectHealth.GREEN
    )
    priority = models.IntegerField(choices=Priority.choices, default=Priority.MEDIUM)
    start_date = models.DateField(verbose_name=_("Start date"))
    expected_end_date = models.DateField(
        null=True, blank=True, verbose_name=_("Expected end date")
    )
    client_name = models.CharField(max_length=200, blank=True, default="")
    business_owner = models.CharField(max_length=200, blank=True, default="")
    tags = models.CharField(max_length=300, blank=True, default="", help_text=_("Comma separated"))
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "employee.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Project")
        verbose_name_plural = _("Projects")
        ordering = ["-priority", "name"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-project", args=[self.pk])

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class AllProjects(Project):
    """
    Permission switch only: holding ``view_allprojects`` shows every project (not just the
    ones the user is a member of) and ``change_allprojects`` allows editing any project.
    A proxy so it appears as its own row in the permission grid. It has no table.
    """

    class Meta:
        proxy = True
        verbose_name = _("Project (all projects)")
        verbose_name_plural = _("Projects (all projects)")


class ProjectMember(models.Model):
    """Join of an employee to a project with their role and allocation."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_memberships"
    )
    role_on_project = models.CharField(
        max_length=30, choices=ProjectRole.choices, default=ProjectRole.DEVELOPER
    )
    allocation_pct = models.PositiveSmallIntegerField(default=100)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Project member")
        verbose_name_plural = _("Project members")
        constraints = [
            models.UniqueConstraint(
                fields=["project", "employee"], name="vertex_one_membership_per_project"
            )
        ]

    def __str__(self):
        return f"{self.employee} on {self.project.code}"


def document_path(instance, filename):
    return f"vertex/projectdocument/file/{filename}"


class ProjectDocument(models.Model):
    """A project file or video link. Downloads go through an authorised view."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=30, choices=DocumentCategory.choices, default=DocumentCategory.OTHER
    )
    visibility = models.IntegerField(choices=Visibility.choices, default=Visibility.INTERNAL)
    is_video_link = models.BooleanField(default=False)
    video_url = models.URLField(blank=True, default="")
    file = models.FileField(upload_to=document_path, blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Project document")
        verbose_name_plural = _("Project documents")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def delete(self, *args, **kwargs):
        stored = self.file
        super().delete(*args, **kwargs)
        if stored:
            stored.delete(save=False)

    @property
    def display_name(self):
        return self.file_name or os.path.basename(self.file.name) if self.file else ""


class ProjectComment(models.Model):
    """Collaboration note visible to every member of the project."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Project comment")
        verbose_name_plural = _("Project comments")
        ordering = ["created_at"]


class ProjectCall(models.Model):
    """Customer or internal call scheduled from the project screen.

    The Outlook/Teams scheduling of ArielVertex is not ported yet: the meeting link is
    entered by hand.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="calls")
    title = models.CharField(max_length=200)
    agenda = models.TextField(blank=True, default="")
    call_type = models.CharField(
        max_length=10, choices=CallType.choices, default=CallType.CUSTOMER
    )
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    attendees = models.CharField(max_length=500, blank=True, default="", help_text=_("Comma separated emails"))
    meeting_url = models.URLField(blank=True, default="")
    meeting_notes = models.TextField(blank=True, default="")
    follow_up_action_items = models.TextField(blank=True, default="")
    scheduled_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Project call")
        verbose_name_plural = _("Project calls")
        ordering = ["-scheduled_at"]
