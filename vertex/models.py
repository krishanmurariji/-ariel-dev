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

from django.core.validators import MaxValueValidator, MinValueValidator
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

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-project-edit", args=[self.pk])

    def redirect(self):
        """onclick attribute that opens the project workspace (used by the list rows)."""
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_avatar(self):
        return f"https://ui-avatars.com/api/?name={self.code}&background=random&bold=true"

    def get_initials(self):
        words = [w for w in self.name.replace("-", " ").split() if w[:1].isalnum()]
        letters = "".join(w[0] for w in words[:2]) or self.code[:2]
        return letters.upper()

    def get_member_count(self):
        return sum(1 for m in self.members.all() if m.is_active)

    def get_health_html(self):
        from django.utils.html import format_html

        colour = {"green": "#16a34a", "amber": "#f59e0b", "red": "#dc2626"}.get(self.health, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_health_display(),
        )

    def get_card_view_subtitle(self):
        from django.utils.html import format_html

        return format_html(
            '<div class="my-2">{}: <span class="font-semibold">{}</span></div>'
            '<div class="mb-2">{}: {}</div>'
            "<div>{}: <span class=\"font-semibold\">{}</span> &middot; {}: {}</div>",
            _("Status"), self.get_status_display(),
            _("Health"), self.get_health_html(),
            _("Priority"), self.get_priority_display(),
            _("Team"), self.get_member_count(),
        )

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


# ---------------------------------------------------------------------------
# Phase 3: reviews and meeting minutes (ArielVertex ReviewRequest, ReviewMeeting,
# ProjectReview, CodeReview, MeetingMinute)
#
# * view_reviewrequest          see the Reviews page (own reviews: subject, reviewer or requester)
# * view_allreviewrequests      see every review request    (proxy "Review request (all)")
# * add_reviewrequest           request / schedule reviews  (also sees all requests, as in Vertex)
# * change_reviewrequest        schedule a request (and be PM/PC of its project)
# * add_projectreview / add_codereview   submit the outcome (assigned reviewer, or see-all)
# * view/add/change/delete_meetingminute minutes; only the person who captured them can touch them
# ---------------------------------------------------------------------------


class ReviewType(models.TextChoices):
    PROJECT_REVIEW = "project_review", _("Project review")
    CODE_REVIEW = "code_review", _("Code review")
    GENERAL_FEEDBACK = "general_feedback", _("General feedback")


def _when(value):
    """Readable local date and time for list columns, '-' when empty."""
    from django.utils import timezone

    if not value:
        return "-"
    return timezone.localtime(value).strftime("%d %b %Y, %H:%M")


class ReviewRequestStatus(models.TextChoices):
    OPEN = "open", _("Open")
    SCHEDULED = "scheduled", _("Scheduled")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class MeetingResponse(models.TextChoices):
    NO_RESPONSE = "no_response", _("No response")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")
    TENTATIVE = "tentative", _("Tentative")


class ReviewRequest(models.Model):
    """HR / management request for a project, code or general review of one employee."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="review_requests")
    subject = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_reviews_about"
    )
    requested_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="vertex_reviews_requested"
    )
    assigned_to = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vertex_reviews_assigned",
    )
    review_type = models.CharField(
        max_length=20, choices=ReviewType.choices, default=ReviewType.PROJECT_REVIEW
    )
    status = models.CharField(
        max_length=15, choices=ReviewRequestStatus.choices, default=ReviewRequestStatus.OPEN
    )
    notes = models.TextField(blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Review request")
        verbose_name_plural = _("Review requests")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_review_type_display()} of {self.subject} ({self.project.code})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-review", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_project_url(self):
        return self.project.get_absolute_url()

    def get_project_code(self):
        return self.project.code

    def get_subject_name(self):
        return self.subject.get_full_name()

    def get_reviewer_name(self):
        return self.assigned_to.get_full_name() if self.assigned_to else "-"

    def get_meeting_time(self):
        meeting = getattr(self, "meeting", None)
        return _when(meeting.scheduled_at) if meeting else "-"

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {
            "open": "#3b82f6", "scheduled": "#8b5cf6", "in_progress": "#f59e0b",
            "completed": "#16a34a", "cancelled": "#6b7280",
        }.get(self.status, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )


class AllReviewRequests(ReviewRequest):
    """Permission switch only: ``view_allreviewrequests`` shows every review request."""

    class Meta:
        proxy = True
        verbose_name = _("Review request (all)")
        verbose_name_plural = _("Review requests (all)")


class ReviewMeeting(models.Model):
    """The scheduled review meeting. The meeting link is entered by hand (no Outlook/Teams yet)."""

    review_request = models.OneToOneField(
        ReviewRequest, on_delete=models.CASCADE, related_name="meeting"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    attendees = models.CharField(max_length=1000, blank=True, default="")
    meeting_url = models.URLField(blank=True, default="")
    response_status = models.CharField(
        max_length=15, choices=MeetingResponse.choices, default=MeetingResponse.NO_RESPONSE
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    scheduled_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("Review meeting")
        verbose_name_plural = _("Review meetings")


_RATING = [MinValueValidator(1), MaxValueValidator(5)]


class ProjectReview(models.Model):
    """Project contribution review submitted by the assigned reviewer (PM / PC)."""

    review_request = models.OneToOneField(
        ReviewRequest, on_delete=models.CASCADE, related_name="project_review"
    )
    reviewer = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    delivery_rating = models.PositiveSmallIntegerField(validators=_RATING)
    ownership_rating = models.PositiveSmallIntegerField(validators=_RATING)
    collaboration_rating = models.PositiveSmallIntegerField(validators=_RATING)
    strengths = models.TextField(blank=True, default="")
    observations = models.TextField(blank=True, default="")
    action_items = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Project review")
        verbose_name_plural = _("Project reviews")


class CodeReview(models.Model):
    """Technical review outcome captured by a Technical Lead."""

    review_request = models.OneToOneField(
        ReviewRequest, on_delete=models.CASCADE, related_name="code_review"
    )
    reviewer = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    code_quality_rating = models.PositiveSmallIntegerField(validators=_RATING)
    architecture_rating = models.PositiveSmallIntegerField(validators=_RATING)
    testing_rating = models.PositiveSmallIntegerField(validators=_RATING)
    strengths = models.TextField(blank=True, default="")
    observations = models.TextField(blank=True, default="")
    blockers = models.TextField(blank=True, default="")
    action_items = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Code review")
        verbose_name_plural = _("Code reviews")


class MinuteStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PREVIEW = "preview", _("Preview")
    APPROVED = "approved", _("Approved")
    SENT = "sent", _("Sent")


class MeetingMinute(models.Model):
    """
    Notes captured -> minutes generated -> previewed -> approved -> sent to attendees.
    Editing approved or sent minutes sends them back to Preview so they are approved again.
    """

    title = models.CharField(max_length=200)
    meeting_date = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True, default="")
    attendees = models.TextField(blank=True, default="", help_text=_("Emails, comma or line separated"))
    raw_notes = models.TextField(blank=True, default="")
    minutes_text = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=MinuteStatus.choices, default=MinuteStatus.DRAFT)
    created_by = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_minutes"
    )
    approved_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Meeting minute")
        verbose_name_plural = _("Meeting minutes")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-minute", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-minute-edit", args=[self.pk])

    def get_delete_url(self):
        from django.urls import reverse

        return reverse("vertex-minute-delete", args=[self.pk])

    def get_meeting_when(self):
        return _when(self.meeting_date)

    def get_sent_display(self):
        return f"{_when(self.sent_at)} ({self.sent_count}x)" if self.sent_at else "-"

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {
            "draft": "#6b7280", "preview": "#8b5cf6", "approved": "#16a34a", "sent": "#0ea5e9",
        }.get(self.status, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )


# ---------------------------------------------------------------------------
# Status updates (ArielVertex StatusUpdate): a developer daily / weekly update on a project
# the person is assigned to.
#
# * view_statusupdate        see the Status Updates page (own updates) and the project tab
# * add_statusupdate         submit an update (only for projects the person is a member of)
# * view_allstatusupdates    see internal notes and the consolidated summary (proxy "Status update (all)")
# ---------------------------------------------------------------------------


class UpdateStatus(models.TextChoices):
    ON_TRACK = "on_track", _("On track")
    AT_RISK = "at_risk", _("At risk")
    BLOCKED = "blocked", _("Blocked")


class StatusUpdate(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="status_updates")
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_status_updates"
    )
    update_date = models.DateField(verbose_name=_("Date"))
    work_completed = models.TextField(verbose_name=_("Work completed"), max_length=4000)
    next_planned_work = models.TextField(blank=True, default="", verbose_name=_("Next planned work"))
    blockers = models.TextField(blank=True, default="")
    dependencies = models.TextField(blank=True, default="")
    hours_spent = models.DecimalField(
        max_digits=4, decimal_places=1, default=8,
        validators=[MinValueValidator(0), MaxValueValidator(24)], verbose_name=_("Hours"),
    )
    status = models.CharField(
        max_length=10, choices=UpdateStatus.choices, default=UpdateStatus.ON_TRACK
    )
    internal_note = models.TextField(
        blank=True, default="", verbose_name=_("Internal note"),
        help_text=_("Never shown to business people."),
    )
    client_shareable_summary = models.TextField(
        blank=True, default="", max_length=2000, verbose_name=_("Client-shareable summary"),
        help_text=_("The only part rolled into the business summary."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Status update")
        verbose_name_plural = _("Status updates")
        ordering = ["-update_date", "-created_at"]

    def __str__(self):
        return f"{self.employee} - {self.project.code} - {self.update_date}"

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {"on_track": "#16a34a", "at_risk": "#f59e0b", "blocked": "#dc2626"}.get(
            self.status, "#6b7280"
        )
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )

    def get_project_code(self):
        return self.project.code

    def get_project_url(self):
        return self.project.get_absolute_url()


class AllStatusUpdates(StatusUpdate):
    """Permission switch only: see internal notes and the consolidated summary."""

    class Meta:
        proxy = True
        verbose_name = _("Status update (all)")
        verbose_name_plural = _("Status updates (all)")


# ---------------------------------------------------------------------------
# Resources (ArielVertex ResourcesController + ResourceRequestsController)
#
# * view_resourcerequest        see the Hiring Requests page (own requests)
# * view_allresourcerequests    see every request     (proxy "Resource request (all)")
# * add_resourcerequest         raise a request for a project the person can see
# * change_resourcerequest      move a request through its status flow (HR); also sees all
# * view_resourcevisibility     the Resource Visibility page (allocation of every person)
# ---------------------------------------------------------------------------


class ResourceRequestStatus(models.TextChoices):
    OPEN = "open", _("Open")
    UNDER_REVIEW = "under_review", _("Under review")
    NEED_MORE_INFORMATION = "need_more_information", _("Need more information")
    APPROVED = "approved", _("Approved")
    IN_HIRING_PROCESS = "in_hiring_process", _("In hiring process")
    CANDIDATE_SHARED = "candidate_shared", _("Candidate shared")
    FULFILLED = "fulfilled", _("Fulfilled")
    REJECTED = "rejected", _("Rejected")
    CLOSED = "closed", _("Closed")


class ResourceRequest(models.Model):
    """Hiring / resource request raised by a project manager or coordinator."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="resource_requests")
    requested_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="vertex_resource_requests"
    )
    role_title = models.CharField(max_length=200, verbose_name=_("Role title"))
    skills = models.CharField(max_length=500, blank=True, default="", help_text=_("Comma separated"))
    reason = models.TextField(blank=True, default="")
    priority = models.IntegerField(choices=Priority.choices, default=Priority.MEDIUM)
    count = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(100)], verbose_name=_("Count")
    )
    expected_start_date = models.DateField(null=True, blank=True, verbose_name=_("Expected start"))
    status = models.CharField(
        max_length=25, choices=ResourceRequestStatus.choices, default=ResourceRequestStatus.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Resource request")
        verbose_name_plural = _("Resource requests")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.role_title} x{self.count} ({self.project.code})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-resource-request", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_project_url(self):
        return self.project.get_absolute_url()

    def get_project_code(self):
        return self.project.code

    def get_requested_by_name(self):
        return self.requested_by.get_full_name() if self.requested_by else "-"

    def get_raised_on(self):
        return _when(self.created_at)

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {
            "open": "#3b82f6", "under_review": "#8b5cf6", "need_more_information": "#f59e0b",
            "approved": "#16a34a", "in_hiring_process": "#0ea5e9", "candidate_shared": "#14b8a6",
            "fulfilled": "#15803d", "rejected": "#dc2626", "closed": "#6b7280",
        }.get(self.status, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )


class AllResourceRequests(ResourceRequest):
    """Permission switch only: ``view_allresourcerequests`` shows every request."""

    class Meta:
        proxy = True
        verbose_name = _("Resource request (all)")
        verbose_name_plural = _("Resource requests (all)")


class ResourceRequestComment(models.Model):
    resource_request = models.ForeignKey(
        ResourceRequest, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Resource request comment")
        verbose_name_plural = _("Resource request comments")
        ordering = ["created_at"]


class ResourceVisibility(ProjectMember):
    """Permission switch only: ``view_resourcevisibility`` opens the Resource Visibility page."""

    class Meta:
        proxy = True
        verbose_name = _("Resource visibility")
        verbose_name_plural = _("Resource visibility")
