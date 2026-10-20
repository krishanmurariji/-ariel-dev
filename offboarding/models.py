import json
from ast import literal_eval
from collections.abc import Iterable
from datetime import date, timedelta
from urllib.parse import urlencode

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse, reverse_lazy
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department
from employee.models import Employee
from horilla import horilla_middlewares
from horilla.horilla_middlewares import _thread_locals
from horilla.methods import get_horilla_model_class
from horilla.models import HorillaModel, sensitive_upload_path, upload_path
from horilla_audit.models import HorillaAuditInfo, HorillaAuditLog
from horilla_views.cbv_methods import render_template
from notifications.signals import notify

# Create your models here.


class Offboarding(HorillaModel):
    """
    Offboarding model
    """

    statuses = [("ongoing", _("Ongoing")), ("completed", _("Completed"))]
    title = models.CharField(max_length=20)
    description = models.TextField(max_length=255)
    managers = models.ManyToManyField(Employee)
    status = models.CharField(max_length=10, default="ongoing", choices=statuses)
    company_id = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        verbose_name="Company",
    )
    objects = HorillaCompanyManager("company_id")

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        is_new = not self.pk
        super().save(*args, **kwargs)
        if is_new:
            stages = [
                ("Exit interview", "interview"),
                ("Work Handover", "handover"),
                ("FNF", "fnf"),
                ("Farewell", "other"),
                ("Archived", "archived"),
            ]
            for stage in stages:
                new = OffboardingStage()
                new.offboarding_id = self
                new.title = stage[0]
                new.type = stage[1]
                new.save()

        return


class OffboardingStage(HorillaModel):
    """
    Offboarding model
    """

    types = [
        ("notice_period", _("Notice period")),
        ("fnf", _("FnF Settlement")),
        ("other", _("Other")),
        ("interview", _("Interview")),
        ("handover", _("Work handover")),
        ("archived", _("Archived")),
    ]

    title = models.CharField(max_length=20)
    type = models.CharField(max_length=13, choices=types)
    offboarding_id = models.ForeignKey(Offboarding, on_delete=models.CASCADE)
    managers = models.ManyToManyField(Employee)
    sequence = models.IntegerField(default=0, editable=False)

    def __str__(self) -> str:
        return str(self.title)

    def is_archived_stage(self):
        """
        This method is to check the stage is archived or not
        """
        return self.type == "archived"

    def get_delete_url(self):
        """
        This method is used to get delete url
        """
        query = urlencode(
            {
                "model": "offboarding.OffboardingStage",
                "pk": str(self.pk),
                "reload_target": "#applyFilter",
            }
        )
        return f"{reverse('generic-delete')}?{query}"

    def get_update_url(self):
        """
        This method is used to get update url
        """
        return f'{reverse_lazy("create-offboarding-stage", kwargs={"pk": self.pk})}?offboarding_id={self.offboarding_id.pk}'

    def get_add_employee_url(self):
        """
        This method is used to get add employee url
        """
        url = f'{reverse_lazy("add-offboarding-employee")}?stage_id={self.id}'

        return url


@receiver(post_save, sender=Offboarding)
def create_initial_stage(sender, instance, created, **kwargs):
    """
    This is post save method, used to create initial stage for the recruitment
    """
    if created:
        initial_stage = OffboardingStage()
        initial_stage.title = "Notice Period"
        initial_stage.offboarding_id = instance
        initial_stage.type = "notice_period"
        initial_stage.save()


class OffboardingStageMultipleFile(HorillaModel):
    """
    OffboardingStageMultipleFile
    """

    attachment = models.FileField(upload_to=upload_path)


class OffboardingEmployee(HorillaModel):
    """
    OffboardingEmployee model / Employee on stage
    """

    UNIT = [("day", "days"), ("month", "Month")]
    employee_id = models.OneToOneField(
        Employee, on_delete=models.CASCADE, verbose_name="Employee"
    )
    stage_id = models.ForeignKey(
        OffboardingStage, on_delete=models.PROTECT, verbose_name="Stage", null=True
    )
    notice_period = models.IntegerField(null=True)
    unit = models.CharField(max_length=10, choices=UNIT, default="month", null=True)
    notice_period_starts = models.DateField(
        null=True, verbose_name=_("Notice Period Starts")
    )
    notice_period_ends = models.DateField(
        null=True, blank=True, verbose_name=_("Notice Period Ends")
    )

    # --- Exit Process (Keka-style) -------------------------------------
    # One exit "case" per employee. The tab an exit shows under is its
    # status: In Progress (accepted, offboarding underway) -> Exited (last
    # working day passed) - or Cancelled (exit called off, employee stays).
    # `stage_id` and the notice_* fields above are the old pipeline's;
    # notice_period_ends is kept equal to last_working_day so everything
    # that still reads it (payroll_pro's exits step, the API) keeps working.
    EXIT_TYPES = [
        ("resignation", _("Resignation")),
        ("termination", _("Termination")),
    ]
    EXIT_STATUSES = [
        ("in_progress", _("Exit in progress")),
        ("exited", _("Exited")),
        ("cancelled", _("Cancelled")),
    ]
    exit_type = models.CharField(
        max_length=12, choices=EXIT_TYPES, default="resignation"
    )
    status = models.CharField(
        max_length=12, choices=EXIT_STATUSES, default="in_progress", db_index=True
    )
    notice_date = models.DateField(
        null=True, blank=True, verbose_name=_("Notice date")
    )
    last_working_day = models.DateField(
        null=True, blank=True, verbose_name=_("Last working day")
    )
    rehire_eligible = models.BooleanField(
        null=True, blank=True, verbose_name=_("Eligible for rehire")
    )
    rehire_comment = models.TextField(blank=True, default="")
    exited_on = models.DateField(null=True, blank=True)
    # Calendar days already added to the last working day because the
    # employee took approved leave during notice - stops a second click
    # from extending for the same leave twice.
    leave_days_extended = models.PositiveIntegerField(default=0)
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    def __str__(self) -> str:
        return self.employee_id.get_full_name()

    def detail_subtitle(self):
        """
        Return subtitle containing both department and job position information.
        """
        return f"{self.employee_id.get_department()} / {self.employee_id.get_job_position()}"

    def detail_view_task_custom(self):
        """
        This method for get custom column for stage in detail view.
        """
        tasks = self.employeetask_set.all()

        return render_template(
            path="cbv/exit_process/detail_view_tasks.html",
            context={
                "instance": self,
                "tasks": tasks,
            },
        )

    def get_individual_url(self):
        """
        This method is used to get individual view url
        """
        return f'{reverse_lazy("offboarding-individual-view", kwargs={"pk": self.pk})}'

    def get_notice_period_col(self):
        """
        This method for get custom column for notice period in detail view.
        """

        notice_period_ends = self.notice_period_ends
        today = date.today()

        if notice_period_ends:
            col = (
                _("today")
                if notice_period_ends == today
                else (
                    _("Notice period ended")
                    if notice_period_ends < today
                    else (
                        _("In") + " " + timesince(today, notice_period_ends)
                        if notice_period_ends
                        else ""
                    )
                )
            )

        return col if notice_period_ends else ""

    def get_stage_col(self):
        """
        This method for get custom column for stage in Pipeline view.
        """
        from offboarding.forms import StageSelectForm

        return render_template(
            path="cbv/exit_process/pipeline_stage_col.html",
            context={
                "employee": self,
                "stage_form": StageSelectForm(
                    offboarding=self.stage_id.offboarding_id,
                    initial={"stage_id": self.stage_id.pk},
                ),
                "stages": self.stage_id.offboarding_id.offboardingstage_set.all(),
            },
        )

    def pending_required_tasks(self, stage=None):
        """
        Required offboarding tasks assigned to this employee in ``stage``
        (defaults to the employee's current stage) that are not yet completed.
        """
        print(self)
        stage = stage or self.stage_id
        completed_task_ids = EmployeeTask.objects.filter(
            employee_id=self,
            task_id__stage_id=stage,
            status="completed",
        ).values_list("task_id", flat=True)
        return (
            OffboardingTask.objects.filter(
                stage_id=stage,
                is_required=True,
                employeetask__employee_id=self,
            )
            .exclude(id__in=completed_task_ids)
            .distinct()
        )

    def get_task_status_col(self):
        """
        This method for get custom column for task status in Pipeline view.
        """
        completed_tasks = self.employeetask_set.filter(status="completed").count()
        total_tasks = self.employeetask_set.all().count()

        task_status = f"{completed_tasks} / {total_tasks}"
        col = f"""
            <div class="oh-checkpoint-badge oh-checkpoint-badge--primary" title="{_('Completed')} {completed_tasks} {_('of')} {total_tasks} {_('tasks')}">
                {task_status}
            </div>
        """
        return col

    def get_action_col(self):
        """
        This method for get custom column for action in Pipeline view.
        """
        return render_template(
            path="cbv/exit_process/pipeline_action_col.html",
            context={"employee": self, "stage": self.stage_id},
        )

    def __getattribute__(self, name):
        if name.startswith("get_") and name.endswith("_task"):
            task_id = literal_eval(name[4:-5])
            task = EmployeeTask.objects.filter(
                task_id__id=task_id,
                employee_id_id=self.id,
                task_id__stage_id=self.stage_id,
            ).first()

            return render_template(
                "cbv/exit_process/tasks_cols.html",
                {"instance": self, "task": task, "task_id": task_id},
            )
        value = super().__getattribute__(name)

        return value

    def ordered_group_json(self):
        """
        This method is used to get ordered group json
        """
        Offboarding = self.stage_id.offboarding_id
        offboarding_stages = Offboarding.offboardingstage_set.all().order_by("sequence")
        ordered_group_json = json.dumps(
            [
                {
                    "id": stage.id,
                    "stage": stage.title,
                }
                for stage in offboarding_stages
            ]
        )
        return ordered_group_json

    def get_archive_title(self):
        """
        This method is used to get title for the archive in actions
        """
        return "Archive" if self.employee_id.is_active else "Un-Archive"

    def get_mail_send_url(self):
        """
        This method is used to get the mail send url
        """
        return f'{reverse_lazy("send-mail-employee", kwargs={"emp_id": self.pk})}'

    def get_notes_url(self):
        """
        This method is used to get the employee note view url
        """
        return (
            f'{reverse_lazy("view-offboarding-note", kwargs={"employee_id": self.pk})}'
        )

    def get_archive_url(self):
        """
        This method is used to get the mail send url
        """
        return f'{reverse_lazy("employee-archive", kwargs={"obj_id": self.pk})}'

    def get_edit_url(self):
        """
        This method is used to get the mail send url
        """
        return f'{reverse_lazy("add-employee", kwargs={"pk": self.pk})}?stage_id={self.stage_id.id}'

    def get_managing_record_url(self):
        """
        This method is used to get the mail send url
        """
        return f'{reverse_lazy("get-manager-in")}?employee_id={self.employee_id.id}&offboarding=True'


class ResignationLetter(HorillaModel):
    """
    Resignation Request Employee model
    """

    # requested  = Under Review tab
    # approved   = accepted, the exit is in progress / done
    # rejected   = refused at review
    # retained   = HR chose to keep the employee (counter-offer etc.)
    # withdrawn  = the employee took the resignation back before review
    # cancelled  = accepted earlier, then the exit was called off
    statuses = [
        ("requested", _("Requested")),
        ("approved", _("Approved")),
        ("rejected", _("Rejected")),
        ("retained", _("Retained")),
        ("withdrawn", _("Withdrawn")),
        ("cancelled", _("Cancelled")),
    ]
    employee_id = models.ForeignKey(
        Employee, on_delete=models.PROTECT, verbose_name="Employee"
    )
    exit_type = models.CharField(
        max_length=12,
        choices=OffboardingEmployee.EXIT_TYPES,
        default="resignation",
    )
    # Who raised it: the employee (resignation) or the manager/HR (either).
    initiated_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="exit_requests_initiated",
    )
    notice_date = models.DateField(null=True, blank=True)
    review_comment = models.TextField(blank=True, default="")
    # Optional supporting document when HR/a manager raises the exit.
    attachment = models.FileField(
        upload_to=sensitive_upload_path, null=True, blank=True, editable=False
    )
    reviewed_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="exit_requests_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    title = models.CharField(max_length=100, null=True)
    description = models.TextField(null=True, max_length=255)
    planned_to_leave_on = models.DateField(verbose_name=_("Planned To Leave On"))
    status = models.CharField(max_length=10, choices=statuses, default="requested")
    offboarding_employee_id = models.ForeignKey(
        OffboardingEmployee, on_delete=models.CASCADE, editable=False, null=True
    )

    # Reasons used to be a fixed list stored as these codes; they are now
    # managed in Exit settings (ExitReasonOption) and the *title* is stored
    # here as a snapshot, so renaming or deleting a reason later never
    # rewrites history. The codes stay readable for rows saved earlier.
    legacy_reasons = {
        "explore_other_careers": _("Explore Other Careers"),
        "other": _("Other"),
        "personal_reasons": _("Personal Reasons"),
        "relocating": _("Relocating"),
        "resignation": _("Resignation"),
        "retirement": _("Retirement"),
    }
    reason = models.CharField(
        max_length=100, blank=True, default="", verbose_name=_("Reason")
    )
    reason_option = models.ForeignKey(
        "offboarding.ExitReasonOption",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="letters",
    )
    discussed_with_manager = models.BooleanField(
        null=True, blank=True, verbose_name=_("Discussed with manager")
    )
    discussion_summary = models.TextField(
        blank=True, default="", verbose_name=_("Discussion summary")
    )
    preferred_last_working_day = models.DateField(
        null=True, blank=True, verbose_name=_("Preferred last working day")
    )
    # Snapshot of the notice-period policy at the moment of submission, so
    # the request keeps saying what the employee was told even if the policy
    # is changed later.
    notice_period_days = models.PositiveIntegerField(null=True, blank=True)
    notice_period_ends = models.DateField(
        null=True, blank=True, verbose_name=_("Notice period ends")
    )
    additional_comment = models.TextField(
        blank=True, default="", verbose_name=_("Additional comment")
    )
    # People the employee chose to be told as well - they get the same mail
    # and in-app notice as the reporting manager. Telling them confers no
    # access to the request itself.
    notify_employees = models.ManyToManyField(
        Employee,
        blank=True,
        related_name="resignations_to_notify",
        verbose_name=_("Persons to notify"),
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    def can_withdraw(self):
        """Shown on the employee's own tab: still under review and withdrawing is allowed."""
        from offboarding.exit_service import _settings_for

        return self.status == "requested" and _settings_for(self.employee_id).allow_withdrawal

    def tab_actions_column(self):
        return render_template(
            path="cbv/resignation/tab_actions.html", context={"instance": self}
        )

    def get_reason_col(self):
        return self.legacy_reasons.get(self.reason) or self.reason or "—"

    def get_discussion_col(self):
        if self.discussed_with_manager is None:
            return "—"
        return _("Yes") if self.discussed_with_manager else _("No")

    def get_preferred_lwd_col(self):
        return self.preferred_last_working_day or "—"

    def get_notice_end_col(self):
        return self.notice_period_ends or "—"

    def get_status(self):
        """
        Display status
        """
        return dict(self.statuses).get(self.status)

    def get_status_badge(self):
        """
        Display status as a styled badge for the detail view.
        """
        badge_colors = {
            "requested": "#808080",
            "approved": "#8db600",
            "rejected": "#ff0000",
        }
        label = dict(self.statuses).get(self.status, self.status)
        color = badge_colors.get(self.status, "#808080")
        return (
            f'<span style="background-color:{color}; color:#fff; '
            f'padding:3px 10px; border-radius:4px; font-size:0.8rem;">'
            f"{label}</span>"
        )

    def option_column(self):
        """
        This method for get custome coloumn .
        """

        return render_template(
            path="cbv/resignation/options.html",
            context={"instance": self},
        )

    def actions_column(self):
        """
        This method for get custome coloumn .
        """

        return render_template(
            path="cbv/resignation/actions.html",
            context={"instance": self},
        )

    def description_col(self):
        """
        This method for get custome column .
        """

        return render_template(
            path="cbv/resignation/description.html",
            context={"instance": self},
        )

    def detail_description_col(self):
        """
        This method for get custome column .
        """

        return render_template(
            path="cbv/resignation/detail_description.html",
            context={"instance": self},
        )

    def resignation_subtitle(self):
        """
        Detail view subtitle
        """

        return f"{self.employee_id.get_department()} / {self.employee_id.get_job_position()}"

    def get_detail_url(self):
        """
        Detail view url
        """
        url = reverse_lazy("resignation-requests-detail-view", kwargs={"pk": self.pk})
        return url

    def get_detail_tab_url(self):
        """
        Detail view url
        """
        url = reverse_lazy(
            "tab-resignation-requests-detail-view", kwargs={"pk": self.pk}
        )
        return url

    def clean(self):
        super().clean()
        if self.pk:
            original = (
                ResignationLetter.objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if original == "rejected":
                raise ValidationError(
                    _("A rejected resignation letter cannot be modified.")
                )
        if self.employee_id_id:
            existing = ResignationLetter.objects.filter(
                employee_id=self.employee_id_id,
                status__in=["requested", "approved"],
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError(
                    _(
                        "A resignation letter for this employee is already in "
                        "'Requested' or 'Approved' status."
                    )
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.status == "approved":
            pass
        return

    def to_offboarding_employee(
        self, offboarding, notice_period_starts, notice_period_ends
    ):
        """
        This method is used to convert/add employee to offboarding
        """
        offboarding_employee = OffboardingEmployee.objects.filter(
            employee_id=self.employee_id
        ).first()
        offboarding_employee = (
            offboarding_employee if offboarding_employee else OffboardingEmployee()
        )
        offboarding_employee.employee_id = self.employee_id
        offboarding_employee.stage_id = (
            OffboardingStage.objects.order_by("created_at")
            .filter(offboarding_id=offboarding)
            .first()
        )
        default_notice_end = (
            get_horilla_model_class(
                app_label="payroll", model="payrollgeneralsetting"
            ).objects.first()
            if apps.is_installed("payroll")
            else None
        )

        try:
            if not notice_period_ends:
                notice_period_ends = (
                    notice_period_starts + timedelta(default_notice_end.notice_period)
                    if default_notice_end
                    else notice_period_ends
                )
        except:
            notice_period_ends = notice_period_ends

        offboarding_employee.notice_period_starts = notice_period_starts
        offboarding_employee.notice_period_ends = notice_period_ends
        if (
            notice_period_starts
            and notice_period_ends
            and not isinstance(notice_period_starts, str)
            and not isinstance(notice_period_ends, str)
        ):
            diffs = date(
                day=notice_period_ends.day,
                month=notice_period_ends.month,
                year=notice_period_ends.year,
            ) - date(
                day=notice_period_starts.day,
                month=notice_period_starts.month,
                year=notice_period_starts.year,
            )
            diffs = diffs.days
            offboarding_employee.notice_period = diffs if diffs > 0 else None
            offboarding_employee.unit = "day" if diffs > 0 else None
        offboarding_employee.save()


class OffboardingTask(HorillaModel):
    """
    OffboardingTask model
    """

    title = models.CharField(max_length=30)
    managers = models.ManyToManyField(Employee)
    stage_id = models.ForeignKey(
        OffboardingStage,
        on_delete=models.PROTECT,
        verbose_name="Stage",
        null=True,
        blank=True,
    )
    is_required = models.BooleanField(default=False, verbose_name=_("Is Required"))

    class Meta:
        unique_together = ["title", "stage_id"]

    def __str__(self) -> str:
        return self.title


class EmployeeTask(HorillaModel):
    """
    EmployeeTask model
    """

    statuses = [
        ("todo", _("Todo")),
        ("in_progress", _("In progress")),
        ("stuck", _("Stuck")),
        ("completed", _("Completed")),
    ]
    employee_id = models.ForeignKey(
        OffboardingEmployee,
        on_delete=models.CASCADE,
        verbose_name="Employee",
        null=True,
    )
    status = models.CharField(max_length=20, choices=statuses, default="todo")
    task_id = models.ForeignKey(OffboardingTask, on_delete=models.CASCADE)
    description = models.TextField(null=True, editable=False, max_length=255)
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
    )

    class Meta:
        unique_together = ["employee_id", "task_id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        request = getattr(_thread_locals, "request", None)
        notify.send(
            request.user.employee_get,
            recipient=self.employee_id.employee_id.employee_user_id,
            verb=f'Offboarding task "{self.task_id.title}" has been assigned',
            verb_ar=f"",
            verb_de=f"",
            verb_es=f"",
            verb_fr=f"",
            redirect="offboarding/offboarding-pipeline",
            icon="information",
        )


class ExitReason(HorillaModel):
    """
    ExitReason model
    """

    title = models.CharField(max_length=50)
    description = models.TextField(max_length=255)
    offboarding_employee_id = models.ForeignKey(
        OffboardingEmployee, on_delete=models.CASCADE
    )
    attachments = models.ManyToManyField(OffboardingStageMultipleFile)


class OffboardingNote(HorillaModel):
    """
    OffboardingNote
    """

    attachments = models.ManyToManyField(
        OffboardingStageMultipleFile, blank=True, editable=False
    )
    description = models.TextField(null=True, blank=True, max_length=255)
    note_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, editable=False
    )
    employee_id = models.ForeignKey(
        OffboardingEmployee, on_delete=models.CASCADE, null=True, editable=False
    )
    stage_id = models.ForeignKey(
        OffboardingStage, on_delete=models.PROTECT, null=True, editable=False
    )

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        if request:
            updated_by = request.user.employee_get
            self.note_by = updated_by
        if self.employee_id:
            self.stage_id = self.employee_id.stage_id
        return super().save(*args, **kwargs)


class OffboardingGeneralSetting(HorillaModel):
    """
    OffboardingGeneralSettings
    """

    # "Allow employees to resign"
    resignation_request = models.BooleanField(default=False)
    allow_withdrawal = models.BooleanField(
        default=True,
        verbose_name=_("Allow withdrawal of resignation"),
        help_text=_("Employees can take a resignation back before it is reviewed."),
    )
    allow_early_last_working_day = models.BooleanField(
        default=True,
        verbose_name=_("Allow preference on early last working day"),
    )
    show_approval_status = models.BooleanField(
        default=True,
        verbose_name=_("Show approval status to employees"),
    )
    REVIEW_MODES = [
        ("approval", _("Approval - a reviewer must accept it before the exit starts")),
        ("acknowledgement", _("Acknowledgement - the exit starts straight away")),
    ]
    review_mode = models.CharField(
        max_length=15, choices=REVIEW_MODES, default="approval"
    )
    notice_starts_on_resignation_date = models.BooleanField(
        default=True,
        verbose_name=_("Consider the resignation date as the start of the notice period"),
    )
    LWD_HOLIDAY_RULES = [
        ("none", _("Leave the last working day as it is")),
        ("before", _("Move it to the working day before")),
        ("after", _("Move it to the next working day")),
    ]
    last_working_day_holiday_rule = models.CharField(
        max_length=6,
        choices=LWD_HOLIDAY_RULES,
        default="none",
        verbose_name=_("If the last working day is a holiday or weekly off"),
    )
    exit_survey_auto_send = models.BooleanField(
        default=False,
        verbose_name=_("Send the exit survey automatically"),
    )
    SURVEY_SEND_TIMES = [
        (7, _("1 week before the last working day")),
        (2, _("2 days before the last working day")),
        (1, _("1 day before the last working day")),
    ]
    # Empty means "when the exit starts".
    exit_survey_send_before_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=SURVEY_SEND_TIMES,
        verbose_name=_("When to send the exit survey"),
    )
    company_id = models.ForeignKey(Company, on_delete=models.CASCADE, null=True)
    objects = HorillaCompanyManager("company_id")

    @classmethod
    def for_editing(cls, request):
        """
        The row Exit settings should edit and save: the selected company's
        own row, or the global one in "All Company" mode - created (unsaved,
        with defaults) when it doesn't exist yet.
        """
        session = getattr(request, "session", None)
        selected = session.get("selected_company") if session is not None else None
        rows = cls.objects.entire().order_by("pk")
        if selected and str(selected).isdigit():
            return rows.filter(company_id=selected).first() or cls(
                company_id_id=int(selected)
            )
        return rows.filter(company_id__isnull=True).first() or cls()

    @classmethod
    def effective_for_request(cls, request):
        """
        The governing row, or - when nothing has been saved yet - an unsaved
        row carrying the defaults above, so callers never have to handle None.
        """
        return cls.for_request(request) or cls()

    @classmethod
    def for_request(cls, request):
        """
        The one settings row that governs this request: the selected
        company's own row if there is one, otherwise the global (no-company)
        row. "All Company" mode means the global row.

        Every feature-flag gate and the admin toggle go through this, so
        they can never disagree about whether the feature is on - the
        tab, the Create form and the toggle used to each look the row up
        differently (first row / company-or-global / global only).
        """
        session = getattr(request, "session", None)
        selected = session.get("selected_company") if session is not None else None
        rows = cls.objects.entire().order_by("pk")
        if selected and str(selected).isdigit():
            row = rows.filter(company_id=selected).first()
            if row is not None:
                return row
        return rows.filter(company_id__isnull=True).first()

    @classmethod
    def resignation_enabled_for(cls, request):
        return bool(getattr(cls.for_request(request), "resignation_request", False))


class ExitReasonOption(HorillaModel):
    """
    A reason an exit can be raised for ("Relocating", "Misconduct" ...),
    managed in Exit settings. A company's own list wins over the global one.
    """

    title = models.CharField(max_length=100, verbose_name=_("Reason"))
    exit_type = models.CharField(
        max_length=12,
        choices=OffboardingEmployee.EXIT_TYPES,
        default="resignation",
        verbose_name=_("Applies to"),
    )
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, blank=True
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        ordering = ["exit_type", "title"]

    def __str__(self):
        return self.title

    @classmethod
    def for_company(cls, exit_type, company_id=None):
        """Reasons to offer: the organisation-wide ones plus the company's own."""
        rows = cls.objects.entire().filter(exit_type=exit_type)
        scope = models.Q(company_id__isnull=True)
        if company_id:
            scope |= models.Q(company_id=company_id)
        return rows.filter(scope).order_by("title")


class NoticePeriodPolicy(HorillaModel):
    """A named notice period ("Standard - 90 days"), assignable per employee."""

    title = models.CharField(max_length=100, verbose_name=_("Policy name"))
    description = models.CharField(
        max_length=255, blank=True, default="", verbose_name=_("Description")
    )
    days = models.PositiveIntegerField(verbose_name=_("Notice period (days)"))
    is_default = models.BooleanField(default=False, verbose_name=_("Default policy"))
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, blank=True
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} ({self.days} days)"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            # One default per company (or one global default).
            NoticePeriodPolicy.objects.entire().filter(
                company_id=self.company_id, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)


class EmployeeNoticePolicy(HorillaModel):
    """Which notice period policy an employee is on (none = the default one)."""

    employee_id = models.OneToOneField(
        Employee, on_delete=models.CASCADE, related_name="notice_policy_assignment"
    )
    # PROTECT: a policy somebody is on can't be deleted out from under them.
    policy_id = models.ForeignKey(
        NoticePeriodPolicy, on_delete=models.PROTECT, related_name="assignments"
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )


class ExitActivity(HorillaModel):
    """
    Plain-language audit trail of everything that happens to an exit - who
    raised it, who accepted it, date changes and why, cancellations.
    """

    ACTIONS = [
        ("initiated", _("Exit initiated")),
        ("accepted", _("Accepted")),
        ("rejected", _("Rejected")),
        ("retained", _("Employee retained")),
        ("withdrawn", _("Withdrawn by employee")),
        ("lwd_changed", _("Last working day changed")),
        ("cancelled", _("Exit cancelled")),
        ("rejoined", _("Employee rejoined")),
        ("exited", _("Exited")),
        ("reminder", _("Reminder sent")),
        ("asset_ask", _("Asset return requested")),
        ("asset_back", _("Asset recovered")),
        ("team_moved", _("Team handed over")),
        ("task_done", _("Exit task completed")),
        ("task_approved", _("Exit task approved")),
        ("task_rejected", _("Exit task sent back")),
        ("task_skipped", _("Exit task skipped")),
        ("task_owner", _("Exit task reassigned")),
        ("survey_sent", _("Exit survey sent")),
        ("survey_done", _("Exit survey filled in")),
    ]
    employee_id = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="exit_activities"
    )
    letter_id = models.ForeignKey(
        ResignationLetter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    action = models.CharField(max_length=20, choices=ACTIONS)
    comment = models.TextField(blank=True, default="")
    from_value = models.CharField(max_length=100, blank=True, default="")
    to_value = models.CharField(max_length=100, blank=True, default="")
    performed_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exit_activities_performed",
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        ordering = ["-created_at", "-pk"]


# --------------------------------------------------------------------------
# Exit tasks
#
# The groups and their tasks are onboarding's own Task Groups (TaskTemplate /
# TaskTemplateTask) with the "offboarding" type, so the same building blocks
# serve both processes. Everything that only makes sense for an exit - who
# owns a task, who approves it, when it fires relative to the last working
# day - is kept here, next to the exit, and onboarding never sees it.
# --------------------------------------------------------------------------


class ExitTaskConfig(HorillaModel):
    """Exit-only settings of one task in an offboarding Task Group."""

    OWNER_TYPES = [
        ("employee", _("The employee who is leaving")),
        ("reporting_manager", _("Their reporting manager")),
        ("specific", _("A specific person")),
    ]
    APPROVER_TYPES = [
        ("none", _("No approval needed")),
        ("reporting_manager", _("Their reporting manager")),
        ("specific", _("A specific person")),
    ]
    task_id = models.OneToOneField(
        "onboarding.TaskTemplateTask",
        on_delete=models.CASCADE,
        related_name="exit_config",
    )
    owner_type = models.CharField(
        max_length=20, choices=OWNER_TYPES, default="reporting_manager"
    )
    owner_person = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approver_type = models.CharField(
        max_length=20, choices=APPROVER_TYPES, default="none"
    )
    approver_person = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Days from the last working day when the task is handed to its owner:
    # negative = before it, 0 = on the day, positive = after it.
    trigger_offset_days = models.IntegerField(default=-7)
    due_after_days = models.PositiveIntegerField(default=3)
    mandatory_attachment = models.BooleanField(default=False)
    objects = HorillaCompanyManager(related_company_field="task_id__template_id__company_id")


class ExitTaskGroupRule(HorillaModel):
    """Who a Task Group applies to - Keka's include / exclude rules."""

    EXIT_TYPES = [
        ("all", _("Resignations and terminations")),
        ("resignation", _("Resignations only")),
        ("termination", _("Terminations only")),
    ]
    template_id = models.OneToOneField(
        "onboarding.TaskTemplate",
        on_delete=models.CASCADE,
        related_name="exit_rule",
    )
    is_active = models.BooleanField(default=True)
    exit_type = models.CharField(max_length=20, choices=EXIT_TYPES, default="all")
    include_departments = models.ManyToManyField(
        Department, blank=True, related_name="+"
    )
    exclude_departments = models.ManyToManyField(
        Department, blank=True, related_name="+"
    )
    objects = HorillaCompanyManager(related_company_field="template_id__company_id")


class ExitTask(HorillaModel):
    """One task for one person's exit - a snapshot of the template task."""

    STATUSES = [
        ("scheduled", _("Scheduled")),
        ("pending", _("Pending")),
        ("review", _("Waiting for approval")),
        ("done", _("Done")),
        ("skipped", _("Skipped")),
        ("cancelled", _("Cancelled")),
    ]
    OPEN = ("scheduled", "pending", "review")
    case_id = models.ForeignKey(
        OffboardingEmployee, on_delete=models.CASCADE, related_name="exit_tasks"
    )
    template_task_id = models.ForeignKey(
        "onboarding.TaskTemplateTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exit_tasks",
    )
    group_title = models.CharField(max_length=100, blank=True, default="")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    sequence = models.IntegerField(default=0)
    owner_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exit_tasks_owned",
    )
    approver_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exit_tasks_to_approve",
    )
    trigger_date = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUSES, default="scheduled")
    mandatory_attachment = models.BooleanField(default=False)
    attachment = models.FileField(
        upload_to=sensitive_upload_path, null=True, blank=True
    )
    note = models.TextField(blank=True, default="")
    done_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    done_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    review_comment = models.TextField(blank=True, default="")
    skipped_reason = models.TextField(blank=True, default="")
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    objects = HorillaCompanyManager(
        related_company_field="case_id__employee_id__employee_work_info__company_id"
    )

    class Meta:
        ordering = ["trigger_date", "sequence", "pk"]

    def __str__(self):
        return f"{self.title} - {self.case_id}"


# --------------------------------------------------------------------------
# Exit survey
#
# One list of questions per company (or organisation-wide). When a survey is
# sent for an exit, the questions are copied onto it, so editing the list
# later never rewrites feedback that was already given.
# --------------------------------------------------------------------------


class ExitSurveyQuestion(HorillaModel):
    TYPES = [
        ("text", _("Short text")),
        ("textarea", _("Long text")),
        ("options", _("Single choice")),
        ("multiple", _("Multiple choice")),
        ("checkbox", _("Yes / No")),
        ("number", _("Number")),
        ("date", _("Date")),
        ("rating", _("Rating (1 to 5)")),
    ]
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, blank=True
    )
    question = models.TextField(max_length=500)
    type = models.CharField(max_length=15, choices=TYPES, default="text")
    options = models.TextField(
        blank=True, default="", help_text=_("Choices, separated by commas")
    )
    is_mandatory = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sequence = models.IntegerField(default=0)
    objects = HorillaCompanyManager("company_id")

    class Meta:
        ordering = ["sequence", "pk"]

    def __str__(self):
        return self.question[:60]


class ExitSurvey(HorillaModel):
    STATUSES = [
        ("sent", _("Waiting for the employee")),
        ("completed", _("Filled in")),
        ("cancelled", _("Cancelled")),
    ]
    case_id = models.ForeignKey(
        OffboardingEmployee, on_delete=models.CASCADE, related_name="exit_surveys"
    )
    status = models.CharField(max_length=12, choices=STATUSES, default="sent")
    # False once the exit it belonged to was cancelled: kept as history, but
    # a later exit of the same person gets a fresh survey.
    is_current = models.BooleanField(default=True)
    sent_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    questions = models.JSONField(default=list)
    answers = models.JSONField(default=dict)
    objects = HorillaCompanyManager(
        related_company_field="case_id__employee_id__employee_work_info__company_id"
    )

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"Exit survey - {self.case_id}"
