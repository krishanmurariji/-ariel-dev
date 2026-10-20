"""
models.py

This module is used to register models for onboarding app

"""

import os
from ast import literal_eval
from datetime import datetime
from urllib.parse import urlencode

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department, EmployeeShift, EmployeeType, JobRole, WorkType
from employee.models import Employee
from horilla.models import HorillaModel, sensitive_upload_path, upload_path
from horilla_audit.models import HorillaAuditInfo, HorillaAuditLog
from horilla_views.cbv_methods import render_template
from recruitment.models import Candidate, Recruitment


class OnboardingStage(HorillaModel):
    """
    OnboardingStage models
    """

    stage_title = models.CharField(max_length=200, verbose_name=_("Stage Title"))
    recruitment_id = models.ForeignKey(
        Recruitment,
        verbose_name=_("Recruitment"),
        null=True,
        related_name="onboarding_stage",
        on_delete=models.CASCADE,
    )
    employee_id = models.ManyToManyField(Employee, verbose_name=_("Stage Managers"))
    sequence = models.IntegerField(null=True)
    is_final_stage = models.BooleanField(
        default=False, verbose_name=_("Is Final Stage")
    )
    objects = HorillaCompanyManager("recruitment_id__company_id")

    def __str__(self):
        return f"{self.stage_title}"

    class Meta:
        """
        Meta class for additional options
        """

        verbose_name = _("Onboarding Stage")
        verbose_name_plural = _("Onboarding Stages")
        ordering = ["sequence"]


@receiver(post_save, sender=Recruitment)
def create_initial_stage(sender, instance, created, **kwargs):
    """
    This is post save method, used to create initial stage for the recruitment
    """
    if created or not instance.onboarding_stage.first():
        initial_stage = OnboardingStage()
        initial_stage.sequence = 0
        initial_stage.stage_title = "Initial"
        initial_stage.recruitment_id = instance
        initial_stage.save()


class OnboardingTask(HorillaModel):
    """
    OnboardingTask models
    """

    task_title = models.CharField(max_length=200, verbose_name=_("Task Title"))
    # recruitment_id = models.ManyToManyField(Recruitment, related_name="onboarding_task")
    stage_id = models.ForeignKey(
        OnboardingStage,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="onboarding_task",
    )
    candidates = models.ManyToManyField(
        Candidate,
        blank=True,
        related_name="cand_onboarding_task",
        verbose_name=_("Candidates"),
    )
    employee_id = models.ManyToManyField(
        Employee, related_name="onboarding_task", verbose_name=_("Task Managers")
    )
    is_required = models.BooleanField(
        default=False,
        verbose_name=_("Is Required"),
        help_text=_(
            "Required tasks must be completed by the candidate to move to the next stage."
        ),
    )

    objects = HorillaCompanyManager("stage_id__recruitment_id__company_id")

    def get_detail_url(self):
        """
        To get edit url
        """
        query_params = {
            "task_id": self.pk,
        }
        url = reverse("candidate-tasks-status")
        return f"{url}?{urlencode(query_params)}"

    def __str__(self):
        return f"{self.task_title}"

    class Meta:
        """
        Meta class to add some additional options
        """

        verbose_name = _("Onboarding Task")
        verbose_name_plural = _("Onboarding Tasks")


class OnboardingCandidate(Candidate):

    def get_detail_url_pipeline(self):
        """
        Get detail url pipeline
        """
        return reverse("onboarding-cand-detail-view", kwargs={"pk": self.pk})

    class Meta:
        proxy = True
        verbose_name = _("Onboarding Candidate")
        verbose_name_plural = _("Onboarding Candidates")
        app_label = "onboarding"


class CandidateStage(HorillaModel):
    """
    CandidateStage model
    """

    candidate_id = models.OneToOneField(
        Candidate, on_delete=models.PROTECT, related_name="onboarding_stage"
    )
    onboarding_stage_id = models.ForeignKey(
        OnboardingStage,
        on_delete=models.PROTECT,
        related_name="candidate",
        verbose_name=_("Stage"),
    )
    onboarding_end_date = models.DateField(blank=True, null=True)
    sequence = models.IntegerField(null=True, default=0)
    objects = HorillaCompanyManager("candidate_id__recruitment_id__company_id")

    def __str__(self):
        return f"{self.candidate_id}  |  {self.onboarding_stage_id}"

    def save(self, *args, **kwargs):
        if self.onboarding_stage_id.is_final_stage:
            self.onboarding_end_date = datetime.today()
        super(CandidateStage, self).save(*args, **kwargs)

    def task_completion_ratio(self):
        """
        function that used for getting the numbers between task completed v/s tasks assigned
        """
        cans_tasks = self.candidate_id.candidate_task
        completed_tasks = cans_tasks.filter(status="done")
        return f"{completed_tasks.count()}/{cans_tasks.count()}"

    def pending_required_tasks(self, stage=None):
        """
        Required onboarding tasks assigned to this candidate in ``stage``
        (defaults to the candidate's current stage) that are not yet done.
        """
        stage = stage or self.onboarding_stage_id
        completed_task_ids = CandidateTask.objects.filter(
            candidate_id=self.candidate_id,
            stage_id=stage,
            status="done",
        ).values_list("onboarding_task_id", flat=True)
        return OnboardingTask.objects.filter(
            stage_id=stage,
            is_required=True,
            candidates=self.candidate_id,
        ).exclude(id__in=completed_task_ids)

    def __getattribute__(self, name):
        if name.startswith("get_") and name.endswith("_task"):
            task_id = literal_eval(name[4:-5])
            task = CandidateTask.objects.filter(
                onboarding_task_id__id=task_id,
                candidate_id=self.candidate_id,
                stage_id=self.onboarding_stage_id,
            ).first()

            return render_template(
                "cbv/pipeline/onboarding/tasks.html",
                {"instance": self, "task": task, "task_id": task_id},
            )
        value = super().__getattribute__(name)

        return value

    class Meta:
        """
        Meta class for additional options
        """

        verbose_name = _("Candidate Onboarding Stage")
        ordering = ["sequence"]


class CandidateTask(HorillaModel):
    """
    CandidateTask model
    """

    choice = (
        ("todo", _("Todo")),
        ("scheduled", _("Scheduled")),
        ("ongoing", _("Ongoing")),
        ("stuck", _("Stuck")),
        ("done", _("Done")),
    )
    candidate_id = models.ForeignKey(
        Candidate, on_delete=models.PROTECT, related_name="candidate_task"
    )
    # managers = models.ManyToManyField(Employee)
    stage_id = models.ForeignKey(
        OnboardingStage,
        null=True,
        on_delete=models.PROTECT,
        related_name="candidate_task",
    )
    status = models.CharField(
        max_length=50, choices=choice, blank=True, null=True, default="todo"
    )
    onboarding_task_id = models.ForeignKey(OnboardingTask, on_delete=models.PROTECT)
    objects = HorillaCompanyManager("candidate_id__recruitment_id__company_id")
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
    )

    def __str__(self):
        return f"{self.candidate_id}|{self.onboarding_task_id}"

    def status_col(self):
        """
        This method for get custom column for status.
        """

        return render_template(
            path="cbv/dashboard/status.html",
            context={"instance": self},
        )

    class Meta:
        """
        Meta class to add some additional options
        """

        verbose_name = _("Onboarding Task")
        verbose_name_plural = _("Onboarding Tasks")


class OnboardingPortal(HorillaModel):
    """
    OnboardingPortal model
    """

    candidate_id = models.OneToOneField(
        Candidate, on_delete=models.PROTECT, related_name="onboarding_portal"
    )
    token = models.CharField(max_length=200)
    used = models.BooleanField(default=False)
    count = models.IntegerField(default=0)
    profile = models.ImageField(upload_to=upload_path, null=True, blank=True)
    objects = HorillaCompanyManager("candidate_id__recruitment_id__company_id")

    def __str__(self):
        return f"{self.candidate_id} | {self.token}"


def validate_proof_file(value):
    """
    Retired: CandidateChecklistTask.proof_file (which used this validator)
    has been removed, but historical migrations (0004_...) still reference
    this function by import path, so it must stay importable indefinitely -
    Django replays the full migration history from disk, and that replay
    literally does `from onboarding.models import validate_proof_file`.
    Safe to delete only after squashing/removing those old migrations.
    """
    allowed = {".pdf", ".jpg", ".jpeg", ".png", ".mp4", ".mov", ".mkv"}
    ext = os.path.splitext(value.name)[1].lower()
    if ext not in allowed:
        raise DjangoValidationError(
            _("Proof must be a PDF, image (jpg/png), or video (mp4/mov/mkv) file.")
        )


PHASE_CHOICES = [
    ("pre_joining", _("Pre-Joining")),
    ("post_joining", _("Post-Joining")),
    ("employee_checklist", _("Employee Checklist")),
]

# Task Groups are also used by the Exit Process ("offboarding"). That type is
# deliberately NOT in PHASE_CHOICES: candidates' checklist tasks can only be
# pre-joining / post-joining / employee-checklist, and every onboarding screen
# lists only those three kinds of group.
TEMPLATE_PHASE_CHOICES = PHASE_CHOICES + [("offboarding", _("Offboarding"))]


class TaskTemplate(HorillaModel):
    """
    A Task Group: a reusable, named set of onboarding tasks (e.g.
    "Recruitment Survey"), assigned to candidates from the matching
    checklist step (Pre-Joining, Post-Joining, or Employee Checklist).
    """

    title = models.CharField(
        max_length=100, unique=True, verbose_name=_("Group Name")
    )
    phase = models.CharField(
        max_length=20,
        choices=TEMPLATE_PHASE_CHOICES,
        default="pre_joining",
        verbose_name=_("Phase"),
    )
    default_assignee_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_assignee_for_task_templates",
        verbose_name=_("Default Assigner"),
        help_text=_(
            "Pre-fills every task's assignee when this group is picked in "
            "the Configure Onboarding stepper - still editable per task."
        ),
    )
    default_due_offset = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Default Due Date"),
        help_text=_(
            "Days after the candidate's joining date that each task in this "
            "group is due by default - any number, not just a fixed list. "
            "Pre-fills each task's due date once the group is picked in the "
            "Configure Onboarding stepper, where it's shown as a real date "
            "picker and can still be adjusted per task."
        ),
    )
    company_id = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager("company_id")

    def __str__(self):
        return self.title

    class Meta:
        """
        Meta class for additional options
        """

        verbose_name = _("Task Group")
        verbose_name_plural = _("Task Groups")


class TaskTemplateTask(HorillaModel):
    """
    A single task belonging to a Task Group (TaskTemplate).
    """

    template_id = models.ForeignKey(
        TaskTemplate,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name=_("Task Group"),
    )
    title = models.CharField(max_length=200, verbose_name=_("Task Title"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    sequence = models.IntegerField(default=0, verbose_name=_("Sequence"))

    def __str__(self):
        return self.title

    class Meta:
        """
        Meta class for additional options
        """

        verbose_name = _("Task")
        verbose_name_plural = _("Tasks")
        ordering = ["sequence", "id"]


class CandidateChecklistTask(HorillaModel):
    """
    A per-candidate instance of a TaskTemplateTask, created when a Task
    Group is assigned from the Configure Onboarding stepper's Pre-Joining or
    Post-Joining checklist step.

    Every task has exactly one assignee - the person who does the work and
    updates its status. There is no second/verifying person.
    """

    # Re-exposes the module-level PHASE_CHOICES (shared with TaskTemplate.phase)
    # as a class attribute, since callers reference CandidateChecklistTask.PHASE_CHOICES.
    PHASE_CHOICES = PHASE_CHOICES
    STATUS_CHOICES = [
        ("todo", _("Todo")),
        ("scheduled", _("Scheduled")),
        ("ongoing", _("Ongoing")),
        ("stuck", _("Stuck")),
        ("done", _("Done")),
    ]

    candidate_id = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="checklist_tasks",
        verbose_name=_("Candidate"),
    )
    template_task_id = models.ForeignKey(
        TaskTemplateTask,
        on_delete=models.SET_NULL,
        null=True,
        related_name="candidate_tasks",
        verbose_name=_("Template Task"),
    )
    title = models.CharField(max_length=200, verbose_name=_("Task"))
    phase = models.CharField(
        max_length=20, choices=PHASE_CHOICES, verbose_name=_("Phase")
    )
    assignee_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklist_tasks",
        verbose_name=_("Assigned To"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="todo",
        verbose_name=_("Status"),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Due Date"),
        help_text=_(
            "Computed from the Task Group's Default Due Date offset plus "
            "the candidate's joining date, when the task is assigned."
        ),
    )
    status_updated_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Status Last Updated")
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    objects = HorillaCompanyManager("candidate_id__recruitment_id__company_id")

    def __str__(self):
        return f"{self.candidate_id} | {self.title}"

    def get_edit_modal_url(self):
        """
        Used by the My Tasks Kanban/List views to open the same edit
        modal on card/row click.
        """
        return reverse("checklist-task-edit-modal", kwargs={"pk": self.pk})

    def due_date_display(self):
        """
        The Kanban card's {format} templating auto-invokes any bound
        method it references, but str.format() would otherwise render a
        None due_date literally as the text "None" - this renders it as
        a plain, human date or an em dash instead.
        """
        return self.due_date.strftime("%b %d, %Y") if self.due_date else "—"

    def status_col(self):
        """
        Custom column renderer for the My Tasks List view.
        """
        return render_template(
            path="onboarding/task_templates/_status_pill.html",
            context={"instance": self},
        )

    class Meta:
        """
        Meta class for additional options
        """

        verbose_name = _("Candidate Checklist Task")
        verbose_name_plural = _("Candidate Checklist Tasks")


class OnboardingCandidateDetail(HorillaModel):
    """
    Staging area for the "Onboarding Details" step of the Configure Onboarding
    stepper - only fields that don't already exist on `Candidate`. Kept as
    its own OneToOne rather than added onto `recruitment.Candidate` directly,
    so this feature's data stays self-contained in the `onboarding` app.
    """

    candidate_id = models.OneToOneField(
        Candidate,
        on_delete=models.CASCADE,
        related_name="onboarding_detail",
        verbose_name=_("Candidate"),
    )
    welcome_message = models.TextField(blank=True, null=True, verbose_name=_("Welcome Message"))
    welcome_message_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Welcome Message Updated At"),
        help_text=_(
            "When welcome_message was last actually saved (non-empty) for "
            "this candidate - used to find the most recently written "
            "welcome message across all candidates, as a prefill default "
            "for a candidate who doesn't have one of their own yet."
        ),
    )
    department_id = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Department"),
    )
    job_role_id = models.ForeignKey(
        JobRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Job Role"),
    )
    reporting_manager_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_reportees",
        verbose_name=_("Reporting Manager"),
    )
    shift_id = models.ForeignKey(
        EmployeeShift,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Shift"),
    )
    work_type_id = models.ForeignKey(
        WorkType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Work Type"),
    )
    employee_type_id = models.ForeignKey(
        EmployeeType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Employee Type"),
    )
    offer_letter = models.FileField(
        upload_to=sensitive_upload_path,
        null=True,
        blank=True,
        verbose_name=_("Offer Letter"),
    )
    buddy_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_buddy_for",
        verbose_name=_("Buddy"),
    )
    furthest_step_key = models.CharField(
        max_length=20,
        default="documents",
        verbose_name=_("Furthest Reached Step"),
        help_text=_(
            "The furthest stepper step this candidate has actually reached. "
            "Nav items beyond this stay locked so the flow can't be jumped "
            "past without going through each step in order."
        ),
    )
    configuration_finalized_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Configuration Finalized At"),
        help_text=_(
            "Set once, the first time this candidate's Configure Onboarding "
            "stepper is actually finished via its Finish Configuration "
            "button (stepper.finalize_onboarding) - unlike this row's own "
            "existence, which is created the moment the stepper is merely "
            "opened, this is what 'Onboarding Configured' should actually "
            "mean anywhere that status is shown to a user."
        ),
    )
    portal_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Portal Sent At"),
        help_text=_(
            "Set the first time the onboarding portal link is actually "
            "emailed to this candidate (onboarding.views.email_send) - the "
            "signal for whether a Probation Feedback Cycle can be started, "
            "since that cycle assumes the candidate can already reach "
            "their own portal for their self-assessment."
        ),
    )
    onboarding_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Onboarding Completed At"),
        help_text=_(
            "Set once, the first time every one of this candidate's "
            "checklist tasks reaches Done - never unset afterward."
        ),
    )
    roles_responsibilities = models.TextField(
        blank=True, null=True, verbose_name=_("Roles & Responsibilities")
    )
    background_verification_notes = models.TextField(
        blank=True, null=True, verbose_name=_("Background Verification Notes")
    )
    objects = HorillaCompanyManager("candidate_id__recruitment_id__company_id")

    def __str__(self):
        return f"{self.candidate_id}"

    class Meta:
        """
        Meta class for additional options
        """

        verbose_name = _("Onboarding Candidate Detail")
