"""
models.py

This module is used to register models for recruitment app

"""

import ast
import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlencode
from uuid import uuid4

import django
import requests
from django import forms
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.storage import default_storage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Min
from django.templatetags.static import static
from django.urls import reverse, reverse_lazy
from django.utils import timezone as tz
from django.utils.functional import cached_property
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, JobPosition
from employee.models import Employee
from horilla.horilla_middlewares import _thread_locals
from horilla.models import HorillaModel, upload_path
from horilla_audit.methods import get_diff
from horilla_audit.models import HorillaAuditInfo, HorillaAuditLog
from horilla_auth.models import HorillaUser
from horilla_views.cbv_methods import render_template

# Create your models here.


def validate_mobile(value):
    """
    This method is used to validate the mobile number using regular expression
    """
    pattern = r"^\+[0-9 ]+$|^[0-9 ]+$"

    if re.match(pattern, value) is None:
        if "+" in value:
            raise forms.ValidationError(
                "Invalid input: Plus symbol (+) should only appear at the beginning \
                    or no other characters allowed."
            )
        raise forms.ValidationError(
            "Invalid input: Only digits and spaces are allowed."
        )


def validate_pdf(value):
    """
    This method is used to validate pdf
    """
    ext = os.path.splitext(value.name)[1]  # Get file extension
    if ext.lower() != ".pdf":
        raise ValidationError(_("File must be a PDF."))


def validate_image(value):
    """
    This method is used to validate the image
    """
    return value


def candidate_photo_upload_path(instance, filename):
    ext = filename.split(".")[-1]
    filename = f"{instance.name.replace(' ', '_')}_{filename}_{uuid4()}.{ext}"
    return os.path.join("recruitment/profile/", filename)


class SurveyTemplate(HorillaModel):
    """
    SurveyTemplate Model
    """

    title = models.CharField(max_length=50, unique=True)
    description = models.TextField(null=True, blank=True)
    is_general_template = models.BooleanField(default=False, editable=False)
    company_id = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager("company_id")

    def __str__(self) -> str:
        return self.title

    class Meta:
        verbose_name = _("Survey Template")
        verbose_name_plural = _("Survey Templates")
        ordering = ["-id"]


class Skill(HorillaModel):
    title = models.CharField(max_length=100)

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        title = self.title
        self.title = title.capitalize()
        super().save(*args, **kwargs)

    def get_sino(self):
        """
        for get serial nos
        """
        all_instances = list(Skill.objects.order_by("id"))
        sino = all_instances.index(self) + 1
        return sino

    def get_update_url(self):
        """
        This method to get update url
        """
        url = reverse_lazy("settings-update-skills", kwargs={"pk": self.pk})
        return url

    def get_delete_url(self):
        """
        This method to get delete url
        """
        base_url = reverse_lazy("delete-skills")
        skill_id = self.pk
        url = f"{base_url}?ids={skill_id}"
        return url

    def get_delete_instance(self):
        """
        to get instance for delete
        """

        return self.pk

    def __str__(self) -> str:
        return f"{self.title}"

    class Meta:
        verbose_name = _("Skill")
        verbose_name_plural = _("Skills")


class Platform(HorillaModel):
    """
    A candidate's source (LinkedIn, Indeed, Manual Entry, Email Intake,
    ...). Displayed to users as "Source" - also used to tag dynamic
    application links for a Recruitment / Job Opening.
    """

    title = models.CharField(max_length=100, unique=True)

    def save(self, *args, **kwargs):
        self.title = self.title.strip()
        super().save(*args, **kwargs)

    def get_update_url(self):
        """
        This method to get update url
        """
        return reverse_lazy("settings-update-platforms", kwargs={"pk": self.pk})

    def get_delete_url(self):
        """
        This method to get delete url
        """
        base_url = reverse_lazy("delete-platforms")
        return f"{base_url}?ids={self.pk}"

    def get_delete_instance(self):
        """
        to get instance for delete
        """
        return self.pk

    def __str__(self) -> str:
        return self.title

    class Meta:
        verbose_name = _("Source")
        verbose_name_plural = _("Sources")


class Recruitment(HorillaModel):
    """
    Recruitment model
    """

    title = models.CharField(
        max_length=50, null=True, blank=True, verbose_name=_("Title")
    )
    description = models.TextField(null=True, verbose_name=_("Description"))
    is_event_based = models.BooleanField(
        default=False,
        help_text=_("To start recruitment for multiple job positions"),
    )
    closed = models.BooleanField(
        default=False,
        help_text=_(
            "To close the recruitment, If closed then not visible on pipeline view."
        ),
        verbose_name=_("Closed"),
    )
    is_published = models.BooleanField(
        default=True,
        help_text=_(
            "To publish a recruitment in website, if false then it \
            will not appear on open recruitment page."
        ),
        verbose_name=_("Is Published"),
    )
    open_positions = models.ManyToManyField(
        JobPosition,
        related_name="open_positions",
        blank=True,
        verbose_name=_("Job Position"),
    )
    job_position_id = models.ForeignKey(
        JobPosition,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_constraint=False,
        related_name="recruitment",
        verbose_name=_("Job Position"),
        editable=False,
    )
    vacancy = models.IntegerField(default=0, null=True, verbose_name=_("Vacancy"))
    recruitment_managers = models.ManyToManyField(Employee, verbose_name=_("Interviewers"))
    recruitment_heads = models.ManyToManyField(
        Employee,
        blank=True,
        related_name="recruitment_heads",
        verbose_name=_("Recruitment Heads"),
    )
    survey_templates = models.ManyToManyField(
        SurveyTemplate, blank=True, verbose_name=_("Survey Templates")
    )
    company_id = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("Company"),
    )
    start_date = models.DateField(
        default=django.utils.timezone.now, verbose_name=_("Start Date")
    )
    end_date = models.DateField(blank=True, null=True, verbose_name=_("End Date"))
    end_date_reminder_sent = models.BooleanField(
        default=False,
        editable=False,
        help_text=_(
            "Whether the recruitment managers have already been notified "
            "that this recruitment's end date is 5 days away."
        ),
    )
    skills = models.ManyToManyField(Skill, blank=True, verbose_name=_("Skills"))
    linkedin_account_id = models.ForeignKey(
        "recruitment.LinkedInAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("LinkedIn Account"),
    )
    linkedin_post_id = models.CharField(max_length=150, null=True, blank=True)
    publish_in_linkedin = models.BooleanField(
        default=True,
        help_text=_(
            "To publish a recruitment in Linkedin, if active is false then it \
            will not post on LinkedIn."
        ),
        verbose_name=_("Post on LinkedIn"),
    )
    objects = HorillaCompanyManager()
    default = models.manager.Manager()
    optional_profile_image = models.BooleanField(
        default=False,
        help_text=_(
            "If off, the profile image upload is hidden on the application "
            "form and never required. If on, applicants must upload a "
            "profile image."
        ),
        verbose_name=_("Optional Profile Image?"),
    )
    optional_resume = models.BooleanField(
        default=False,
        help_text=_("Resume not mandatory for candidate creation"),
        verbose_name=_("Optional Resume"),
    )

    class Meta:
        """
        Meta class to add the additional info
        """

        unique_together = [
            (
                "job_position_id",
                "start_date",
            ),
            ("job_position_id", "start_date", "company_id"),
        ]
        permissions = (("archive_recruitment", "Archive Recruitment"),)
        verbose_name = _("Recruitment")
        verbose_name_plural = _("Recruitments")
        ordering = ["-id"]

    def total_hires(self):
        """
        This method is used to get the count of
        hired candidates
        """
        return self.candidate.filter(hired=True).count()

    def __str__(self):
        title = (
            f"{self.job_position_id.job_position} {self.start_date}"
            if self.title is None and self.job_position_id
            else self.title
        )

        if not self.is_event_based and self.job_position_id is not None:
            self.open_positions.add(self.job_position_id)

        return str(title)

    def clean(self):
        if self.title is None:
            raise ValidationError({"title": _("This field is required")})
        if self.is_published:
            if self.vacancy <= 0:
                raise ValidationError(
                    _(
                        "Vacancy must be greater than zero if the recruitment is publishing."
                    )
                )

        if self.end_date is not None and (
            self.start_date is not None and self.start_date > self.end_date
        ):
            raise ValidationError(
                {"end_date": _("End date cannot be less than start date.")}
            )
        return super().clean()

    def save(self, *args, **kwargs):
        if not self.publish_in_linkedin:
            self.linkedin_account_id = None
            self.linkedin_post_id = None
        if self.pk:
            previous_end_date = (
                Recruitment.objects.filter(pk=self.pk)
                .values_list("end_date", flat=True)
                .first()
            )
            # Re-arm the reminder if the deadline moved, so extending a
            # recruitment's end date gets a fresh 5-day-out notice instead
            # of staying silent because the old date already fired one.
            if previous_end_date != self.end_date:
                self.end_date_reminder_sent = False
        super().save(*args, **kwargs)  # Save the Recruitment instance first
        if self.is_event_based and self.open_positions is None:
            raise ValidationError({"open_positions": _("This field is required")})

    def ordered_stages(self):
        """
        This method will returns all the stage respectively to the ascending order of stages
        """
        return self.stage_set.order_by("sequence")

    def recruitment_column(self):
        """
        This method for get custom column for recruitment.
        """

        return render_template(
            path="cbv/recruitment/recruitment_col.html",
            context={"instance": self},
        )

    def recruitment_detail_view(self):
        """
        detail view
        """
        url = reverse("recruitment-detail-view", kwargs={"pk": self.pk})
        return url

    def managers_column(self):
        """
        This method for get custom column for managers.
        """

        return render_template(
            path="cbv/recruitment/managers_col.html",
            context={"instance": self},
        )

    def managers_detail(self):
        """
        manager in detail view
        """
        employees = self.recruitment_managers.all()
        if employees:
            employee_names_string = "<br>".join(
                [str(employee) for employee in employees]
            )
            return f'<span class="oh-timeoff-modal__stat-count">{employee_names_string}</span>'
        else:
            return ""

    def managers(self):
        manager_list = self.recruitment_managers.all()
        formatted_managers = [
            f"<div>{i + 1}. {manager}</div>" for i, manager in enumerate(manager_list)
        ]
        return "".join(formatted_managers)

    def detail_actions(self):
        """
        This method for get custom column for managers.
        """

        return render_template(
            path="cbv/recruitment/detail_action.html",
            context={"instance": self},
        )

    def open_job_col(self):
        """
        This method for get custom column for open jobs.
        """

        return render_template(
            path="cbv/recruitment/open_jobs.html",
            context={"instance": self},
        )

    def open_job_detail(self):
        """
        open jobs in detail view
        """
        jobs = self.open_positions.all()
        if jobs:
            jobs_names_string = "<br>".join([str(job) for job in jobs])
            return (
                f'<span class="oh-timeoff-modal__stat-count">{jobs_names_string}</span>'
            )
        else:
            return ""

    def tot_hires(self):
        """
        This method for get custom column for Total hires.
        """

        return render_template(
            path="cbv/recruitment/total_hires.html",
            context={"instance": self},
        )

    def status_col(self):
        if self.closed:
            return "Closed"
        else:
            return "Open"

    def closed_toggle(self):
        """
        For toggle the closed field from the Recruitment grid - checked
        means the recruitment is open.
        """
        url = f"update-recruitment-closed/{self.id}"
        return render_template(
            path="cbv/recruitment/closed_toggle.html",
            context={"instance": self, "url": url},
        )

    def rec_actions(self):
        """
        This method for get custom column for actions.
        """

        return render_template(
            path="cbv/recruitment/actions.html",
            context={"instance": self},
        )

    def get_avatar(self):
        """
        Method will retun the api to the avatar or path to the profile image
        """
        url = f"https://ui-avatars.com/api/?name={self.title}&background=random"
        return url

    def is_vacancy_filled(self):
        """
        This method is used to check wether the vaccancy for the recruitment is completed or not
        """
        hired_stage = Stage.objects.filter(
            recruitment_id=self, stage_type="hired"
        ).first()
        if hired_stage:
            hired_candidate = hired_stage.candidate_set.all().exclude(canceled=True)
            if len(hired_candidate) >= self.vacancy:
                return True


class Stage(HorillaModel):
    """
    Stage model
    """

    stage_types = [
        ("initial", _("Initial")),
        ("applied", _("Applied")),
        ("test", _("Test")),
        ("interview", _("Interview")),
        ("cancelled", _("Cancelled")),
        ("hired", _("Hired")),
    ]
    recruitment_id = models.ForeignKey(
        Recruitment,
        on_delete=models.CASCADE,
        related_name="stage_set",
        verbose_name=_("Recruitment"),
    )
    stage_managers = models.ManyToManyField(Employee, verbose_name=_("Stage Managers"))
    stage = models.CharField(max_length=50, verbose_name=_("Stage"))
    stage_type = models.CharField(
        max_length=20,
        choices=stage_types,
        default="interview",
        verbose_name=_("Stage Type"),
    )
    sequence = models.IntegerField(null=True, default=0)
    objects = HorillaCompanyManager(related_company_field="recruitment_id__company_id")

    class Meta:
        """
        Meta class to add the additional info
        """

        permissions = (("archive_Stage", "Archive Stage"),)
        unique_together = ["recruitment_id", "stage"]
        ordering = ["sequence"]
        verbose_name = _("Stage")
        verbose_name_plural = _("Stages")

    def __str__(self):
        return f"{self.stage} - ({self.recruitment_id.title})"

    def active_candidates(self):
        """
        This method is used to get all the active candidate like related objects
        """
        return {
            "all": Candidate.objects.filter(
                stage_id=self, canceled=False, is_active=True
            )
        }

    def stage_detail_view(self):
        """
        detail view
        """
        url = reverse("stage-detail-view", kwargs={"pk": self.pk})
        return url

    def detail_action(self):
        """
        For answerable employees  column
        """

        return render_template(
            path="cbv/stages/detail_action.html",
            context={"instance": self},
        )

    def title_col(self):
        """
        This method for get custome coloumn for title.
        """
        return render_template(
            path="cbv/stages/title.html",
            context={"instance": self},
        )

    def managers_col(self):
        """
        This method for get custome coloumn for managers.
        """

        return render_template(
            path="cbv/stages/managers.html",
            context={"instance": self},
        )

    def get_avatar(self):
        """
        Method will retun the api to the avatar or path to the profile image
        """
        url = (
            f"https://ui-avatars.com/api/?name={self.recruitment_id}&background=random"
        )
        return url

    def detail_managers_col(self):
        """
        Manager in detail view
        """
        employees = self.stage_managers.all()
        employee_names_string = "<br>".join([str(employee) for employee in employees])
        return employee_names_string

    def actions_col(self):
        """
        This method for get custome coloumn for actions.
        """

        return render_template(
            path="cbv/stages/actions.html",
            context={"instance": self},
        )

    def get_type(self):
        """
        Display type
        """
        stage_types = [
            ("initial", _("Initial")),
            ("test", _("Test")),
            ("interview", _("Interview")),
            ("cancelled", _("Cancelled")),
            ("hired", _("Hired")),
        ]

        return dict(stage_types).get(self.stage_type)

    def get_stage_update_url(self):
        """
        This method to get update url
        """
        return reverse("stage-update-pipeline", kwargs={"pk": self.id})

    def get_add_candidate_url(self):
        """
        This method to get add candidate url
        """
        return f'{reverse_lazy("add-candidate-to-stage")}?stage_id={self.id}'

    def get_send_email_url(self):
        """
        This method to get send email url
        """
        return f'{reverse_lazy("send-mail")}?stage_id={self.id}'

    def get_delete_url(self):
        """
        This method to get delete url
        """
        return f"{reverse_lazy('generic-delete')}?model=recruitment.Stage&pk={self.pk}"


def candidate_upload_path(instance, filename):
    """
    Generates a unique file path for candidate profile & resume uploads.
    """
    ext = filename.split(".")[-1]
    name_slug = slugify(instance.name) or "candidate"
    unique_filename = f"{name_slug}-{uuid4().hex[:8]}.{ext}"
    return f"recruitment/{name_slug}/{unique_filename}"


class Candidate(HorillaModel):
    """
    Candidate model
    """

    choices = [("male", _("Male")), ("female", _("Female")), ("other", _("Other"))]
    offer_letter_statuses = [
        ("not_sent", _("Not Sent")),
        ("sent", _("Sent")),
        ("accepted", _("Accepted")),
        ("rejected", _("Rejected")),
        ("joined", _("Joined")),
    ]
    name = models.CharField(max_length=100, null=True, verbose_name=_("Name"))
    profile = models.ImageField(upload_to=upload_path, null=True)  # 853
    portfolio = models.URLField(max_length=200, blank=True, verbose_name=_("Portfolio"))
    recruitment_id = models.ForeignKey(
        Recruitment,
        on_delete=models.PROTECT,
        null=True,
        related_name="candidate",
        verbose_name=_("Recruitment"),
    )
    job_position_id = models.ForeignKey(
        JobPosition,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("Job Position"),
    )
    stage_id = models.ForeignKey(
        Stage,
        on_delete=models.PROTECT,
        null=True,
        verbose_name=_("Stage"),
    )
    converted_employee_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="candidate_get",
        verbose_name=_("Employee"),
    )
    schedule_date = models.DateTimeField(
        blank=True, null=True, verbose_name=_("Schedule date")
    )
    email = models.EmailField(max_length=254, verbose_name=_("Email"))
    mobile = models.CharField(
        max_length=15,
        blank=True,
        validators=[
            validate_mobile,
        ],
        verbose_name=_("Mobile"),
    )
    resume = models.FileField(
        upload_to=upload_path,  # 853
        validators=[
            validate_pdf,
        ],
        verbose_name=_("Resume"),
    )
    referral = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="candidate_referral",
        verbose_name=_("Referral"),
    )
    address = models.TextField(
        null=True, blank=True, verbose_name=_("Address"), max_length=255
    )
    country = models.CharField(
        max_length=30, null=True, blank=True, verbose_name=_("Country")
    )
    dob = models.DateField(null=True, blank=True, verbose_name=_("Date of Birth"))
    state = models.CharField(
        max_length=30, null=True, blank=True, verbose_name=_("State")
    )
    city = models.CharField(
        max_length=30, null=True, blank=True, verbose_name=_("City")
    )
    zip = models.CharField(
        max_length=30, null=True, blank=True, verbose_name=_("Zip Code")
    )
    gender = models.CharField(
        max_length=15,
        choices=choices,
        null=True,
        default="male",
        verbose_name=_("Gender"),
    )
    experience_years = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(60)],
        verbose_name=_("Experience (Years)"),
    )
    experience_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(11)],
        verbose_name=_("Experience (Months)"),
    )
    current_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name=_("Current Salary (per month)"),
    )
    expected_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name=_("Expected Salary"),
    )
    is_salary_negotiable = models.BooleanField(
        default=False,
        verbose_name=_("Open to Salary Negotiation"),
    )
    available_to_join_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(365)],
        verbose_name=_("Available To Join (Days)"),
    )
    skills = models.ManyToManyField(
        Skill,
        blank=True,
        related_name="candidates",
        verbose_name=_("Skills"),
    )
    reason_to_leave = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        verbose_name=_("Reason to Leave"),
    )
    platform = models.ForeignKey(
        Platform,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Source"),
    )
    start_onboard = models.BooleanField(default=False, verbose_name=_("Start Onboard"))
    hired = models.BooleanField(default=False, verbose_name=_("Hired"))
    canceled = models.BooleanField(default=False, verbose_name=_("Canceled"))
    converted = models.BooleanField(default=False, verbose_name=_("Converted"))
    joining_date = models.DateField(
        blank=True, null=True, verbose_name=_("Joining Date")
    )
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
    )
    sequence = models.IntegerField(null=True, default=0)
    latest_ai_score = models.IntegerField(
        null=True,
        blank=True,
        editable=False,
        verbose_name=_("Latest AI Score"),
        help_text=_(
            "Denormalized copy of the most recent CandidateAIScore.score, "
            "kept in sync by recruitment/ai_scoring.py, so the candidate "
            "grid and pipeline can sort/filter on it directly."
        ),
    )
    has_better_job_suggestion = models.BooleanField(
        default=False,
        editable=False,
        verbose_name=_("Has Better Job Suggestion"),
        help_text=_(
            "Denormalized copy of whether the latest CandidateJobSuggestion "
            "found a stronger-matching open job, kept in sync by "
            "recruitment/ai_job_match.py, so the pipeline card can show a "
            "notification badge without a query per card."
        ),
    )

    probation_end = models.DateField(null=True, editable=False)
    offer_letter_status = models.CharField(
        max_length=10,
        choices=offer_letter_statuses,
        default="not_sent",
        editable=False,
        verbose_name=_("Offer Letter Status"),
    )
    objects = HorillaCompanyManager(related_company_field="recruitment_id__company_id")
    last_updated = models.DateField(null=True, auto_now=True)

    converted_employee_id.exclude_from_automation = True
    mail_to_related_fields = [
        ("stage_id__stage_managers__get_mail", "Stage Managers"),
        ("recruitment_id__recruitment_managers__get_mail", "Recruitment Interviewers"),
    ]
    hired_date = models.DateField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"{self.name}"

    def stage_drop_down(self):
        """
        Stage drop down
        """
        request = getattr(_thread_locals, "request", None)
        all_rec_stages = getattr(request, "all_rec_stages", {})
        if all_rec_stages.get(self.stage_id.recruitment_id.pk) is None:
            stages = Stage.objects.filter(recruitment_id=self.stage_id.recruitment_id)
            all_rec_stages[self.stage_id.recruitment_id.pk] = stages
            request.all_rec_stages = all_rec_stages
        return render_template(
            path="cbv/pipeline/stage_drop_down.html",
            context={
                "instance": self,
                "stages": request.all_rec_stages[self.stage_id.recruitment_id.pk],
            },
        )

    def rating_bar(self):
        """
        Rating bar
        """
        return render_template(
            path="cbv/pipeline/rating.html", context={"instance": self}
        )

    @property
    def salary_comparison_max(self):
        """
        The larger of current/expected salary, used as the shared 100%
        reference point so the two comparison bars on the profile page
        are actually proportional to each other instead of fixed widths.
        """
        return max(self.current_salary or 0, self.expected_salary or 0) or 1

    def get_avg_rating(self):
        """
        Docstring for get_avg_rating

        :param self: Candidate instance
        :return: Avg rating got for the candidate
        :rtype: float/int
        """
        return self.candidate_rating.aggregate(avg=Avg("rating"))["avg"]

    def get_total_interview(self):
        """
        Docstring for get_total_interview

        :param self: Total interview assigned for candidate
        :return: Total assigned
        :rtype: Any
        """
        return self.candidate_interview.count()

    def get_interview_count(self):
        """
        Scheduled interviews count
        """
        return render_template(
            path="cbv/pipeline/count_of_interviews.html", context={"instance": self}
        )

    def mail_indication(self):
        """
        Rating bar
        """
        return render_template(
            path="cbv/pipeline/mail_status.html", context={"instance": self}
        )

    def candidate_name(self):
        """
        Rating bar
        """
        now = tz.now()
        return render_template(
            path="cbv/pipeline/candidate_column.html",
            context={"instance": self, "now": now},
        )

    def get_contact(self):
        """
        to get contact no of candidates
        """
        return self.mobile

    def get_resume_url(self):
        return self.resume.url

    def onboarding_portal_html(self):
        return format_html(
            '<div class="oh-checkpoint-badge oh-checkpoint-badge--secondary">{}/4</div>',
            self.onboarding_portal.count,
        )

    def rating(self):
        """
        This method for get custome coloumn for rating.
        """

        return render_template(
            path="cbv/candidates/rating.html",
            context={"instance": self},
        )

    def ai_score_number(self):
        """
        This method for get custom column showing the latest AI score as
        a plain colored number (no "/100" suffix) - used in both the
        Candidates grid and the Recruitment Pipeline card.
        """
        return render_template(
            path="cbv/candidates/ai_score_number.html",
            context={"instance": self},
        )

    def job_suggestion_badge(self):
        """
        This method for get custom column showing a notification badge
        when the AI found this candidate a stronger match for a
        different open job - used in the Recruitment Pipeline card.
        """
        return render_template(
            path="cbv/candidates/job_suggestion_badge.html",
            context={"instance": self},
        )

    def onboarding_status_col(self):
        """
        This method for get custome coloumn for status.
        """

        return render_template(
            path="cbv/onboarding_view/status.html",
            context={"instance": self},
        )

    def onboarding_task_col(self):
        """
        This method for get custome coloumn for tasks.
        """
        from onboarding.models import CandidateStage, CandidateTask

        cand_stage = self.onboarding_stage.id
        cand_stage_obj = CandidateStage.objects.get(id=cand_stage)
        choices = CandidateTask.choice

        return render_template(
            path="cbv/onboarding_view/task.html",
            context={
                "instance": self,
                "candidate": cand_stage_obj,
                "choices": choices,
                "single_view": True,
            },
        )

    def archive_status(self):
        """
        archive status
        """
        if self.is_active:
            return _("Archive")
        else:
            return _("Un-Archive")

    def resume_pdf(self):
        """
        This method for get custome coloumn for resume.
        """

        return render_template(
            path="cbv/candidates/resume.html",
            context={"instance": self},
        )

    def options(self):
        """
        This method for get custom coloumn for options.
        """

        request = getattr(_thread_locals, "request", None)
        mails = getattr(request, "mails", None)

        if not mails:
            mails = list(Candidate.objects.values_list("email", flat=True))
            setattr(request, "mails", mails)

        emp_list = HorillaUser.objects.filter(username__in=mails).values_list(
            "email", flat=True
        )

        return render_template(
            path="cbv/candidates/option.html",
            context={"instance": self, "emp_list": emp_list},
        )

    def get_profile_url(self):
        """
        This method to get profile url
        """
        url = reverse_lazy("candidate-view", kwargs={"pk": self.pk})
        return url

    def get_update_url(self):
        """
        This method to get update url
        """
        url = reverse_lazy("rec-candidate-update", kwargs={"cand_id": self.pk})
        return url

    def get_skill_zone_url(self):
        """
        This method to get update url
        """
        url = reverse_lazy("to-skill-zone", kwargs={"cand_id": self.pk})
        return url

    def get_rejected_candidate_url(self):
        """
        This method to get the update URL with cand_id as a query parameter.
        """
        base_url = reverse_lazy("add-to-rejected-candidates")
        query_params = urlencode({"candidate_id": self.pk})
        return f"{base_url}?{query_params}"

    def get_move_pipeline_url(self):
        """
        This method to get the url for the move-to-another-pipeline modal.
        """
        return reverse_lazy("candidate-move-pipeline-form", kwargs={"pk": self.pk})

    def get_document_request(self):
        """
        This method to get the update URL with cand_id as a query parameter.
        """
        base_url = reverse_lazy("candidate-document-request")
        query_params = urlencode({"candidate_id": self.pk})
        return f"{base_url}?{query_params}"

    def get_view_note_url(self):
        """
        This method to get update url
        """
        url = reverse_lazy("view-note", kwargs={"cand_id": self.pk})
        return url

    def get_individual_url(self):
        """
        This method to get update url
        """
        url = reverse_lazy("candidate-view-individual", kwargs={"cand_id": self.pk})
        return url

    def get_push_url(self):
        """
        This method to get update url
        """
        url = reverse_lazy("candidate-view-individual", kwargs={"cand_id": self.pk})
        return url

    def get_convert_to_emp(self):
        """
        This method to get covert to employee url
        """
        url = reverse_lazy("candidate-conversion", kwargs={"cand_id": self.pk})
        return url

    def get_add_to_skill(self):
        """
        This method to get add to skill zone employee url
        """
        url = reverse_lazy("to-skill-zone", kwargs={"cand_id": self.pk})
        return url

    def get_add_to_reject(self):
        """
        This method to get add to reject zone employee url
        """
        url = reverse_lazy("add-to-rejected-candidates")
        return f"{url}?candidate_id={self.pk}"

    def get_archive_url(self):
        """
        This method to get archive  url
        """

        if self.is_active:
            action = "archive"
        else:
            action = "un-archive"

        message = f"Do you want to {action} this candidate?"
        url = reverse_lazy("rec-candidate-archive", kwargs={"cand_id": self.pk})

        return f"'{url}','{message}'"

    def get_archive_action_url(self):
        """
        This method returns just the archive/un-archive endpoint URL
        (without JS-formatted arguments), suitable for direct HTMX use.
        """
        return reverse_lazy("rec-candidate-archive", kwargs={"cand_id": self.pk})

    def get_delete_url(self):
        """
        This method to get delete url
        """
        url = reverse_lazy("rec-candidate-delete", kwargs={"cand_id": self.pk})
        return url

    def get_self_tracking_url(self):
        """
        This method to get self tracking url
        """
        url = reverse_lazy(
            "candidate-self-status-tracking", kwargs={"cand_id": self.pk}
        )
        return url

    def get_document_request_doc(self):
        """
        This method to get document request url
        """
        url = reverse_lazy("candidate-document-request") + f"?candidate_id={self.pk}"
        return url

    def is_employee_converted(self):
        """
        The method to get converted employee
        """
        request = getattr(_thread_locals, "request", None)
        if not getattr(request, "employees", None):
            request.employees = Employee.objects.all()

        if request.employees.filter(email=self.email).exists():
            return 'style="background-color: #f1ffd5;"'

    def get_details_candidate(self):
        """
        Candidate detail
        """
        url = reverse_lazy("candidate-detail", kwargs={"pk": self.pk})
        return url

    def detail_actions(self):
        """
        Candidate actions
        """
        return render_template(
            path="cbv/candidates/actions.html",
            context={"instance": self},
        )

    def get_send_mail(self):
        """
        Candidate detail
        """
        url = reverse_lazy("send-mail", kwargs={"cand_id": self.pk})
        return url

    def is_offer_rejected(self):
        """
        Is offer rejected checking method
        """
        first = RejectedCandidate.objects.filter(candidate_id=self).first()
        if first:
            return first.reject_reason_id.count() > 0
        return first

    def get_full_name(self):
        """
        Method will return employee full name
        """
        return str(self.name)

    def applied_on_display(self):
        """
        Human-readable date the candidate applied (created_at doubles as the
        application date - there's no separate "applied on" field).
        """
        return self.created_at.strftime("%b %d, %Y") if self.created_at else ""

    def get_avatar(self):
        if self.profile and default_storage.exists(self.profile.name):
            return self.profile.url
        return static("images/ui/default_avatar.jpg")

    def get_company(self):
        """
        This method is used to return the company
        """
        return getattr(
            getattr(getattr(self, "recruitment_id", None), "company_id", None),
            "company",
            None,
        )

    def get_job_position(self):
        """
        This method is used to return the job position of the candidate
        """
        return self.job_position_id.job_position

    def get_email(self):
        """
        Return email
        """
        return self.email

    def get_mail(self):
        """ """
        return self.get_email()

    def phone(self):
        return self.mobile

    def tracking(self):
        """
        This method is used to return the tracked history of the instance
        """
        return get_diff(self)

    def get_last_sent_mail(self):
        """
        This method is used to get last send mail
        """
        from base.models import EmailLog

        return (
            EmailLog.objects.filter(to__icontains=self.email)
            .order_by("-created_at")
            .first()
        )

    def get_schedule_interview(self):
        url = reverse_lazy("interview-schedule", kwargs={"cand_id": self.pk})
        return url

    def get_interview(self):
        """
        This method is used to get the interview dates and times
        for the candidate for the mail templates
        """

        interviews = InterviewSchedule.objects.filter(candidate_id=self.id)
        if interviews:
            interview_info = "<table>"
            interview_info += "<tr><th>Sl No.</th><th>Date</th><th>Time</th><th>Is Completed</th></tr>"
            for index, interview in enumerate(interviews, start=1):
                interview_info += f"<tr><td>{index}</td>"
                interview_info += (
                    f"<td class='dateformat_changer'>{interview.interview_date}</td>"
                )
                interview_info += (
                    f"<td class='timeformat_changer'>{interview.interview_time}</td>"
                )
                interview_info += (
                    f"<td>{'Yes' if interview.completed else 'No'}</td></tr>"
                )
            interview_info += "</table>"
            return interview_info
        else:
            return ""

    def candidate_interview_view(self):
        interviews = InterviewSchedule.objects.filter(candidate_id=self.pk)
        return render_template(
            path="cbv/pipeline/interview_template.html",
            context={"instance": self, "interviews": interviews},
        )

    def save(self, *args, **kwargs):
        if self.stage_id is not None:
            self.hired = self.stage_id.stage_type == "hired"

        should_validate_job_position = not self.pk
        if self.pk:
            previous = (
                Candidate.objects.filter(pk=self.pk)
                .values("recruitment_id", "job_position_id")
                .first()
            )
            if previous:
                should_validate_job_position = (
                    previous["recruitment_id"] != self.recruitment_id_id
                    or previous["job_position_id"] != self.job_position_id_id
                )

        if not self.recruitment_id.is_event_based and self.job_position_id is None:
            self.job_position_id = self.recruitment_id.job_position_id
        if should_validate_job_position:
            if self.job_position_id not in self.recruitment_id.open_positions.all():
                raise ValidationError({"job_position_id": _("Choose valid choice")})
            if self.recruitment_id.is_event_based and self.job_position_id is None:
                raise ValidationError({"job_position_id": _("This field is required.")})
        if self.stage_id and self.stage_id.stage_type == "cancelled":
            self.canceled = True
        if self.canceled:
            cancelled_stage = Stage.objects.filter(
                recruitment_id=self.recruitment_id, stage_type="cancelled"
            ).first()
            if not cancelled_stage:
                cancelled_stage = Stage.objects.create(
                    recruitment_id=self.recruitment_id,
                    stage="Cancelled Candidates",
                    stage_type="cancelled",
                    sequence=50,
                )
            self.stage_id = cancelled_stage
        if (
            self.converted_employee_id
            and Candidate.objects.filter(
                converted_employee_id=self.converted_employee_id
            )
            .exclude(id=self.id)
            .exists()
        ):
            raise ValidationError(_("Employee is uniques for candidate"))

        if self.converted:
            self.hired = False
            self.canceled = False

        super().save(*args, **kwargs)

    def last_email(self):
        """
        for last send mail column

        """

        return render_template(
            path="cbv/onboarding_candidates/cand_email.html",
            context={"instance": self},
        )

    def date_of_joining(self):
        """
        for joining date column

        """

        return render_template(
            path="cbv/onboarding_candidates/date_of_joining.html",
            context={"instance": self},
        )

    def probation_date(self):
        """
        for probation date column

        """

        return render_template(
            path="cbv/onboarding_candidates/probation_date.html",
            context={"instance": self},
        )

    def offer_letter(self):
        """
        for offer letter  column

        """

        return render_template(
            path="cbv/onboarding_candidates/offer_letter.html",
            context={"instance": self},
        )

    def rejected_candidate_class(self):
        """
        Returns the appropriate style and title attributes for rejected candidates.
        """
        if self.is_offer_rejected():
            return f'style="background: #ff4500a3 !important; color: white;" title="{_("Added In Rejected Candidates")}"'
        else:
            return f'title="{_("Add To Rejected Candidates")}"'

    def actions(self):
        """
        for actions  column

        """

        return render_template(
            path="cbv/onboarding_candidates/actions.html",
            context={"instance": self},
        )

    @classmethod
    @lru_cache(maxsize=1)
    def get_unique_questions(cls):
        return dict(
            RecruitmentSurvey.objects.values("question")
            .annotate(pk=Min("pk"))
            .values_list("pk", "question")
        )

    @cached_property
    def survey_answer_dict(self):
        answer_instance = (
            RecruitmentSurveyAnswer.objects.filter(candidate_id=self)
            .only("answer_json")
            .first()
        )

        if answer_instance and answer_instance.answer_json:
            return json.loads(
                answer_instance.answer_json
            )  # faster than ast.literal_eval
        return {}

    def __getattr__(self, name):
        if name.startswith("get_survey_question_"):
            try:
                question_id = int(name.split("_")[-1])

                unique_questions = self.get_unique_questions()
                question_text = unique_questions.get(question_id)

                if not question_text:
                    return None

                result = self.survey_answer_dict.get(question_text)

                if isinstance(result, list):
                    return ",".join(result)

                return result
            except Exception:
                return None

        try:
            return super().__getattribute__(name)
        except ObjectDoesNotExist:
            raise
        except AttributeError:
            raise

    class Meta:
        """
        Meta class to add the additional info
        """

        unique_together = (
            "email",
            "recruitment_id",
        )
        permissions = (
            ("view_history", "View Candidate History"),
            ("archive_candidate", "Archive Candidate"),
        )
        ordering = ["sequence"]
        verbose_name = _("Candidate")
        verbose_name_plural = _("Candidates")


class CandidateEducation(HorillaModel):
    """
    One education entry (degree) for a candidate. A candidate can have
    multiple - collected on the public application form.
    """

    candidate_id = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="education_set",
        verbose_name=_("Candidate"),
    )
    degree = models.CharField(max_length=100, verbose_name=_("Course"))
    specialization = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("Branch/Specialization"),
    )
    percentage_cgpa = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        verbose_name=_("CGPA/Percentage"),
    )
    start_date = models.DateField(
        null=True, blank=True, verbose_name=_("Start of Course")
    )
    end_date = models.DateField(
        null=True, blank=True, verbose_name=_("End of Course")
    )
    university = models.CharField(
        max_length=150,
        null=True,
        blank=True,
        verbose_name=_("University/College"),
    )
    location = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("Location")
    )

    def __str__(self):
        return f"{self.degree} - {self.university or ''}".strip(" -")

    class Meta:
        verbose_name = _("Candidate Education")
        verbose_name_plural = _("Candidate Education")
        ordering = ["-end_date"]


class CandidateExperience(HorillaModel):
    """
    One work-experience entry for a candidate. A candidate can have
    multiple - collected on the public application form.
    """

    candidate_id = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="experience_set",
        verbose_name=_("Candidate"),
    )
    company_name = models.CharField(max_length=150, verbose_name=_("Company Name"))
    designation = models.CharField(max_length=100, verbose_name=_("Job Title"))
    is_current_job = models.BooleanField(
        default=False, verbose_name=_("Currently working here")
    )
    start_date = models.DateField(
        null=True, blank=True, verbose_name=_("Date of Joining")
    )
    end_date = models.DateField(
        null=True, blank=True, verbose_name=_("Date of Relieving")
    )
    location = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("Location")
    )

    def __str__(self):
        return f"{self.designation} - {self.company_name}".strip(" -")

    class Meta:
        verbose_name = _("Candidate Experience")
        verbose_name_plural = _("Candidate Experience")
        ordering = ["-end_date"]


class CandidateEmergencyContact(HorillaModel):
    """
    One emergency contact for a candidate. A candidate can have multiple -
    collected on the public application form.
    """

    candidate_id = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="emergency_contact_set",
        verbose_name=_("Candidate"),
    )
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    phone = models.CharField(
        max_length=15,
        validators=[validate_mobile],
        verbose_name=_("Phone"),
    )
    relation = models.CharField(
        max_length=50, null=True, blank=True, verbose_name=_("Relation")
    )

    def __str__(self):
        return f"{self.name} ({self.relation or _('Emergency Contact')})"

    class Meta:
        verbose_name = _("Candidate Emergency Contact")
        verbose_name_plural = _("Candidate Emergency Contacts")


class RejectReason(HorillaModel):
    """
    RejectReason
    """

    title = models.CharField(
        max_length=50,
    )
    description = models.TextField(null=True, blank=True, max_length=255)
    company_id = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager()

    def __str__(self) -> str:
        return self.title

    def get_update_url(self):
        """
        This method to get update url
        """

        url = reverse_lazy("update-reject-reason-view", kwargs={"pk": self.pk})
        return url

    def get_delete_url(self):
        """
        This method to get delete url
        """
        base_url = reverse_lazy("delete-reject-reasons")
        rej_id = self.pk
        url = f"{base_url}?id={rej_id}"
        return url

    def get_instance_id(self):
        return self.id

    class Meta:
        verbose_name = _("Reject Reason")
        verbose_name_plural = _("Reject Reasons")


class RejectedCandidate(HorillaModel):
    """
    RejectedCandidate
    """

    candidate_id = models.OneToOneField(
        Candidate,
        on_delete=models.PROTECT,
        verbose_name="Candidate",
        related_name="rejected_candidate",
    )
    reject_reason_id = models.ManyToManyField(
        RejectReason, verbose_name="Reject reason", blank=True
    )
    description = models.TextField(max_length=255)
    objects = HorillaCompanyManager(
        related_company_field="candidate_id__recruitment_id__company_id"
    )
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
    )

    def __str__(self) -> str:
        reasons = ", ".join(self.reject_reason_id.values_list("title", flat=True))
        return f"{self.candidate_id} - {reasons if reasons else _('No Reason')}"


class StageFiles(HorillaModel):
    files = models.FileField(upload_to=upload_path, blank=True, null=True)

    def __str__(self):
        return self.files.name.split("/")[-1]


class StageNote(HorillaModel):
    """
    StageNote model
    """

    candidate_id = models.ForeignKey(Candidate, on_delete=models.CASCADE)
    description = models.TextField(verbose_name=_("Description"))  # 905
    stage_id = models.ForeignKey(Stage, on_delete=models.CASCADE)
    stage_files = models.ManyToManyField(StageFiles, blank=True)
    updated_by = models.ForeignKey(
        Employee, on_delete=models.CASCADE, null=True, blank=True
    )
    candidate_can_view = models.BooleanField(default=False)
    objects = HorillaCompanyManager(
        related_company_field="candidate_id__recruitment_id__company_id"
    )

    def __str__(self) -> str:
        return f"{self.description}"

    def updated_user(self):
        if self.updated_by:
            return self.updated_by
        else:
            return self.candidate_id


class RecruitmentSurvey(HorillaModel):
    """
    RecruitmentSurvey model
    """

    question_types = [
        ("checkbox", _("Yes/No")),
        ("options", _("Choices")),
        ("multiple", _("Multiple Choice")),
        ("text", _("Text")),
        ("number", _("Number")),
        ("percentage", _("Percentage")),
        ("date", _("Date")),
        ("textarea", _("Textarea")),
        ("file", _("File Upload")),
        ("rating", _("Rating")),
    ]
    template_id = models.ManyToManyField(
        SurveyTemplate, verbose_name="Template", blank=True
    )
    is_mandatory = models.BooleanField(default=False)
    recruitment_ids = models.ManyToManyField(
        Recruitment,
        verbose_name=_("Recruitment"),
    )
    question = models.TextField(null=False)
    job_position_ids = models.ManyToManyField(
        JobPosition, verbose_name=_("Job Positions"), editable=False
    )
    sequence = models.IntegerField(null=True, default=0)
    type = models.CharField(
        max_length=15,
        choices=question_types,
    )
    options = models.TextField(
        null=True, default="", help_text=_("Separate choices by ',  '"), max_length=255
    )
    objects = HorillaCompanyManager(related_company_field="recruitment_ids__company_id")

    def __str__(self) -> str:
        return str(self.question)

    def options_col(self):
        if self.type in ["options", "multiple"]:
            return render_template(
                "cbv/recruitment_survey/option_col.html",
                {"instance": self},
            )
        return ""

    def detail_actions(self):
        """
        This method for get custom column for details actions.
        """
        return render_template(
            path="cbv/recruitment_survey/detail_actions.html",
            context={"instance": self},
        )

    def get_edit_url(self):

        url = reverse(
            "recruitment-survey-question-template-edit", kwargs={"pk": self.pk}
        )
        return url

    def get_delete_url(self):

        url = reverse(
            "recruitment-survey-question-template-delete", kwargs={"survey_id": self.pk}
        )
        return url

    def recruitment_col(self):
        """
        Manager in detail view
        """
        recruitment = self.recruitment_ids.all()
        recruitment_string = "<br>".join([str(rec) for rec in recruitment])
        return recruitment_string

    def get_question_type(self):
        return dict(self.question_types).get(self.type)

    def choices(self):
        """
        Used to split the choices
        """
        return self.options.split(", ")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.template_id is None:
            general_template = SurveyTemplate.objects.filter(
                is_general_template=True
            ).first()
            if general_template:
                self.template_id.add(general_template)
                super().save(*args, **kwargs)

    class Meta:
        ordering = [
            "sequence",
        ]


class QuestionOrdering(HorillaModel):
    """
    Survey Template model
    """

    question_id = models.ForeignKey(RecruitmentSurvey, on_delete=models.CASCADE)
    recruitment_id = models.ForeignKey(Recruitment, on_delete=models.CASCADE)
    sequence = models.IntegerField(default=0)
    objects = HorillaCompanyManager(related_company_field="recruitment_ids__company_id")


class RecruitmentSurveyAnswer(HorillaModel):
    """
    RecruitmentSurveyAnswer
    """

    candidate_id = models.ForeignKey(Candidate, on_delete=models.CASCADE)
    recruitment_id = models.ForeignKey(
        Recruitment,
        on_delete=models.PROTECT,
        verbose_name=_("Recruitment"),
        null=True,
    )
    job_position_id = models.ForeignKey(
        JobPosition,
        on_delete=models.PROTECT,
        verbose_name=_("Job Position"),
        null=True,
    )
    answer_json = models.JSONField()
    attachment = models.FileField(upload_to=upload_path, null=True, blank=True)
    objects = HorillaCompanyManager(related_company_field="recruitment_id__company_id")

    @property
    def answer(self):
        """
        Used to convert the json to dict
        """
        # Convert the JSON data to a dictionary
        try:
            return json.loads(self.answer_json)
        except json.JSONDecodeError:
            return {}  # Return an empty dictionary if JSON is invalid or empty

    def __str__(self) -> str:
        return f"{self.candidate_id.name}-{self.recruitment_id}"


class SkillZone(HorillaModel):
    """ "
    Model for talent pool
    """

    title = models.CharField(max_length=50, verbose_name="Skill Zone")
    description = models.TextField(verbose_name=_("Description"), max_length=255)
    company_id = models.ForeignKey(
        Company,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager()

    class Meta:
        verbose_name = _("Skill Zone")
        verbose_name_plural = _("Skill Zones")

    def get_active(self):
        return SkillZoneCandidate.objects.filter(is_active=True, skill_zone_id=self)

    def __str__(self) -> str:
        return self.title

    def get_avatar(self):
        """
        Method will retun the api to the avatar or path to the profile image
        """
        url = f"https://ui-avatars.com/api/?name={self.title}&background=random"
        return url

    def candidate_count_display(self):
        count = self.skillzonecandidate_set.count()
        if count != 1:
            return f"{count} { _('Candidates') }"
        else:
            return f"{count} { _('Candidate') }"

    def get_skill_zone_url(self):
        """
        This method returns the skill zone URL with the title as a query parameter.
        """
        base_url = reverse("skill-zone-view")
        query_string = urlencode({"search": self.title})
        return f"{base_url}?{query_string}"


class SkillZoneCandidate(HorillaModel):
    """
    Model for saving candidate data's for future recruitment
    """

    skill_zone_id = models.ForeignKey(
        SkillZone,
        verbose_name=_("Skill Zone"),
        related_name="skillzonecandidate_set",
        on_delete=models.PROTECT,
        null=True,
    )
    candidate_id = models.ForeignKey(
        Candidate,
        on_delete=models.PROTECT,
        null=True,
        related_name="skillzonecandidate_set",
        verbose_name=_("Candidate"),
    )
    # job_position_id=models.ForeignKey(
    #     JobPosition,
    #     on_delete=models.PROTECT,
    #     null=True,
    #     related_name="talent_pool",
    #     verbose_name=_("Job Position")
    # )

    reason = models.CharField(max_length=200, verbose_name=_("Reason"))
    added_on = models.DateField(auto_now_add=True)
    objects = HorillaCompanyManager(
        related_company_field="candidate_id__recruitment_id__company_id"
    )

    def clean(self):
        # Check for duplicate entries in the database
        duplicate_exists = (
            SkillZoneCandidate.objects.filter(
                candidate_id=self.candidate_id, skill_zone_id=self.skill_zone_id
            )
            .exclude(pk=self.pk)
            .exists()
        )

        if duplicate_exists:
            raise ValidationError(
                _(
                    f"Candidate {self.candidate_id} already exists in Skill Zone {self.skill_zone_id}."
                )
            )

        super().clean()

    def __str__(self) -> str:
        return str(self.candidate_id.get_full_name())

    class Meta:
        ordering = ["-id"]


class CandidateRating(HorillaModel):
    employee_id = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="candidate_rating"
    )
    candidate_id = models.ForeignKey(
        Candidate, on_delete=models.PROTECT, related_name="candidate_rating"
    )
    rating = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(5)]
    )

    class Meta:
        unique_together = ["employee_id", "candidate_id"]

    def __str__(self) -> str:
        return f"{self.employee_id} - {self.candidate_id} rating {self.rating}"


class RecruitmentGeneralSetting(HorillaModel):
    """
    RecruitmentGeneralSettings model
    """

    candidate_self_tracking = models.BooleanField(default=False)
    show_overall_rating = models.BooleanField(default=False)
    send_candidate_portal_mail = models.BooleanField(
        default=False,
        verbose_name=_("Send Candidate Portal Mail"),
        help_text=_(
            "When enabled, applicants receive an email with their "
            "Candidate Portal login details right after submitting an "
            "application. Only takes effect while Application Tracking "
            "is also enabled."
        ),
    )
    show_platform_conversion = models.BooleanField(
        default=True,
        verbose_name=_("Show Source Conversion Widget"),
        help_text=_(
            "Shows the 'Source Conversion Rate' chart (hire rate per "
            "candidate source - LinkedIn, Indeed, etc.) on the "
            "Recruitment Dashboard."
        ),
    )
    meeting_providers = [
        ("google_meet", _("Google Meet")),
        ("teams", _("Microsoft Teams")),
    ]
    default_meeting_provider = models.CharField(
        max_length=15,
        choices=meeting_providers,
        default="google_meet",
        verbose_name=_("Default Meeting Provider"),
        help_text=_(
            "Platform used to create the meeting link when 'Send Meeting "
            "Link' is clicked on an interview."
        ),
    )
    company_id = models.OneToOneField(
        Company, on_delete=models.CASCADE, null=True, blank=True, unique=True
    )
    objects = HorillaCompanyManager()


class InterviewSchedule(HorillaModel):
    """
    Interview Scheduling Model
    """

    candidate_id = models.ForeignKey(
        Candidate,
        verbose_name=_("Candidate"),
        related_name="candidate_interview",
        on_delete=models.CASCADE,
    )
    employee_id = models.ManyToManyField(Employee, verbose_name=_("Interviewer"))
    interview_date = models.DateField(verbose_name=_("Interview Date"))
    interview_time = models.TimeField(verbose_name=_("Interview Time"))
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
    )
    completed = models.BooleanField(
        default=False, verbose_name=_("Is Interview Completed")
    )
    objects = HorillaCompanyManager("candidate_id__recruitment_id__company_id")

    def __str__(self) -> str:
        return f"{self.candidate_id} -Interview."

    def candidate_custom_col(self):
        """
        method for candidate coloumn
        """
        return render_template(
            path="cbv/interview/candidate_custom_col.html",
            context={"instance": self},
        )

    def interviewer_custom_col(self):
        """
        method for interviewer coloumn
        """
        return render_template(
            path="cbv/interview/interviewer_custom_col.html",
            context={"instance": self},
        )

    def custom_color(self):
        """
        Custom background color for all rows with hover effect
        """
        # interviews = InterviewSchedule.objects.filter(
        #     employee_id=self.user.employee_get.id
        # )
        request = getattr(_thread_locals, "request", None)
        if not getattr(self, "request", None):
            self.request = request
        user = request.user
        if user.employee_get in self.employee_id.all():
            color = "rgba(255, 166, 0, 0.158)"
            hovering = "white"

            return (
                f'style="background-color: {color};" '
                f"onmouseover=\"this.style.backgroundColor='{hovering}';\" "
                f"onmouseout=\"this.style.backgroundColor='{color}';\""
            )

    def interviewer_detail(self):
        """
        interviewer in detail view
        """
        employees = self.employee_id.all()
        employee_names_string = ", ".join([str(employee) for employee in employees])
        return employee_names_string

    def detail_subtitle(self):
        """
        Return subtitle for detail view
        """
        return (
            f"{self.candidate_id.recruitment_id} / {self.candidate_id.job_position_id}"
        )

    def get_description(self):
        """
        get description
        """
        if self.description:
            return self.description
        else:
            return _("None")

    def status_custom_col(self):
        """
        method for status coloumn
        """
        now = datetime.now(tz=timezone.utc if settings.USE_TZ else None)
        return render_template(
            path="cbv/interview/status_custom_col.html",
            context={"instance": self, "now": now},
        )

    def custom_action_col(self):
        """
        method for actions coloumn
        """
        return render_template(
            path="cbv/interview/interview_actions.html",
            context={"instance": self},
        )

    def detail_view(self):
        """
        for detail view
        """

        url = reverse("interview-detail-view", kwargs={"pk": self.pk})
        return url

    def detail_view_actions(self):
        """
        detail view actions
        """
        return render_template(
            path="cbv/interview/detail_view_actions.html",
            context={"instance": self},
        )

    class Meta:
        verbose_name = _("Schedule Interview")
        verbose_name_plural = _("Schedule Interviews")


class Resume(models.Model):
    file = models.FileField(
        upload_to=upload_path,
        validators=[
            validate_pdf,
        ],
    )
    recruitment_id = models.ForeignKey(
        Recruitment, on_delete=models.CASCADE, related_name="resume"
    )
    is_candidate = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.recruitment_id} - Resume {self.pk}"


STATUS = [
    ("requested", "Requested"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
]

FORMATS = [
    ("any", "Any"),
    ("pdf", "PDF"),
    ("txt", "TXT"),
    ("docx", "DOCX"),
    ("xlsx", "XLSX"),
    ("jpg", "JPG"),
    ("png", "PNG"),
    ("jpeg", "JPEG"),
]


class CandidateDocumentRequest(HorillaModel):
    title = models.CharField(max_length=100, verbose_name=_("Title"))
    candidate_id = models.ManyToManyField(Candidate)
    format = models.CharField(choices=FORMATS, max_length=10, verbose_name=_("Format"))
    max_size = models.IntegerField(
        blank=True, null=True, verbose_name=_("Max size (In MB)")
    )
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    objects = HorillaCompanyManager(
        related_company_field="candidate_id__recruitment_id__company_id"
    )

    def __str__(self):
        return self.title


class CandidateDocument(HorillaModel):
    title = models.CharField(max_length=250, verbose_name=_("Title"))
    candidate_id = models.ForeignKey(
        Candidate, on_delete=models.PROTECT, verbose_name=_("Candidate")
    )
    document_request_id = models.ForeignKey(
        CandidateDocumentRequest, on_delete=models.PROTECT, null=True
    )
    document = models.FileField(upload_to=upload_path, null=True)
    status = models.CharField(
        choices=STATUS, max_length=10, default="requested", verbose_name=_("Status")
    )
    reject_reason = models.TextField(
        blank=True, null=True, max_length=255, verbose_name=_("Reject Reason")
    )

    def __str__(self):
        return f"{self.candidate_id} - {self.title}"

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        file = self.document

        if len(self.title) < 3:
            raise ValidationError({"title": _("Title must be at least 3 characters")})

        if file and self.document_request_id:
            format = self.document_request_id.format
            max_size = self.document_request_id.max_size
            if max_size:
                if file.size > max_size * 1024 * 1024:
                    raise ValidationError(
                        {"document": _("File size exceeds the limit")}
                    )

            # Use the true final extension so a double extension such as
            # "file.pdf.html" cannot bypass the format check and enable stored
            # XSS when served. See GHSA-p68r-g665-5cm9.
            ext = os.path.splitext(file.name)[1].lstrip(".").lower()
            if format == "any":
                pass
            elif ext != format:
                raise ValidationError(
                    {"document": _("Please upload {} file only.").format(format)}
                )


class LinkedInAccount(HorillaModel):
    username = models.CharField(max_length=250, verbose_name=_("App Name"))
    email = models.EmailField(max_length=254, verbose_name=_("Email"))
    api_token = models.CharField(max_length=500, verbose_name=_("API Token"))
    sub_id = models.CharField(max_length=250, unique=True)
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, verbose_name=_("Company")
    )

    class Meta:
        verbose_name = _("LinkedIn Account")
        verbose_name_plural = _("LinkedIn Accounts")

    def __str__(self):
        return str(self.username)

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        url = "https://api.linkedin.com/v2/userinfo"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if not data["email"] == self.email:
                raise ValidationError({"email": _("Email mismatched.")})
            self.sub_id = response.json()["sub"]
        else:
            raise ValidationError(_("Check the credentials"))

    def action_template(self):
        """
        This method for get custom column for managers.
        """
        return render_template(
            path="linkedin/linkedin_action.html",
            context={"instance": self},
        )

    def is_active_toggle(self):
        """
        For toggle is_active field
        """
        url = f"update-isactive-linkedin-account/{self.id}"
        return render_template(
            path="is_active_toggle.html",
            context={"instance": self, "url": url},
        )


class CandidateIntakeMailbox(HorillaModel):
    """
    Connects an outlook_auth or gmail_auth mailbox to the recruitment
    module so incoming candidate application emails can be polled and
    turned into Candidate records automatically. See
    recruitment/intake_providers/ for how `provider` maps to actual
    polling/fetch code - nothing outside that package should branch on
    provider directly.
    """

    providers = [
        ("outlook", _("Outlook")),
        ("gmail", _("Gmail")),
    ]

    provider = models.CharField(
        max_length=10,
        choices=providers,
        default="outlook",
        verbose_name=_("Provider"),
    )
    azure_api = models.OneToOneField(
        "outlook_auth.AzureApi",
        on_delete=models.CASCADE,
        related_name="candidate_intake_mailbox",
        null=True,
        blank=True,
        verbose_name=_("Outlook Mailbox"),
    )
    google_api = models.OneToOneField(
        "gmail_auth.GoogleApi",
        on_delete=models.CASCADE,
        related_name="candidate_intake_mailbox",
        null=True,
        blank=True,
        verbose_name=_("Gmail Mailbox"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))
    fallback_recruitment_id = models.ForeignKey(
        Recruitment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Fallback Recruitment"),
        help_text=_(
            "Used only when the job opening cannot be confidently \
            matched from the email/resume content."
        ),
    )
    daily_candidate_cap = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Daily Candidate Cap"),
        help_text=_(
            "Max candidates this mailbox may auto-create per day. Leave "
            "blank to use the site-wide default (INTAKE_DAILY_CANDIDATE_CAP)."
        ),
    )
    last_polled_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.TextField(
        blank=True,
        editable=False,
        verbose_name=_("Last Error"),
        help_text=_(
            "Set when the last poll failed before it could even read the "
            "inbox (e.g. expired Outlook login) - cleared on the next "
            "successful poll."
        ),
    )
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, verbose_name=_("Company")
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        verbose_name = _("Candidate Intake Mailbox")
        verbose_name_plural = _("Candidate Intake Mailboxes")

    def __str__(self):
        return str(self.get_provider_account())

    def get_provider_account(self):
        """
        Returns whichever provider-specific account row (AzureApi /
        GoogleApi) matches `provider` - the single object
        recruitment/intake_providers/ adapters need to do anything.
        """
        return self.azure_api if self.provider == "outlook" else self.google_api

    def clean(self):
        super().clean()
        account = self.get_provider_account()
        if account is None:
            raise ValidationError(
                {
                    "provider": _(
                        "Choose the matching mailbox for this provider "
                        "(Outlook Mailbox for Outlook, Gmail Mailbox for Gmail)."
                    )
                }
            )
        other_account = self.google_api if self.provider == "outlook" else self.azure_api
        if other_account is not None:
            raise ValidationError(
                {
                    "provider": _(
                        "Only one mailbox account may be set, matching the "
                        "selected provider."
                    )
                }
            )

    def action_template(self):
        """
        This method for get custom column for managers.
        """
        return render_template(
            path="mailbox/mailbox_action.html",
            context={"instance": self},
        )

    def is_active_toggle(self):
        """
        For toggle is_active field
        """
        url = f"update-isactive-intake-mailbox/{self.id}"
        return render_template(
            path="is_active_toggle.html",
            context={"instance": self, "url": url},
        )

    def error_status(self):
        """
        Custom column showing whether the last poll of this mailbox failed
        before it could even read the inbox (e.g. expired Outlook login).
        """
        return render_template(
            path="mailbox/mailbox_error_status.html",
            context={"instance": self},
        )

    def mailbox_account_display(self):
        """
        Custom column showing the connected account, regardless of provider.
        """
        return str(self.get_provider_account())


class CandidateIntakeLog(models.Model):
    """
    Audit trail for every email processed by a CandidateIntakeMailbox,
    whether it resulted in a new candidate or not.
    """

    statuses = [
        ("created", _("Candidate Created")),
        ("duplicate", _("Duplicate Candidate")),
        ("no_resume", _("No Resume Attached")),
        ("invalid_resume_format", _("Invalid Resume Format")),
        ("attachment_too_large", _("Attachment Too Large")),
        ("no_job_match", _("No Matching Job Opening")),
        ("parse_failed", _("Resume/Email Parsing Failed")),
        ("daily_cap_exceeded", _("Daily Candidate Cap Exceeded")),
        ("error", _("Unexpected Error")),
    ]

    mailbox = models.ForeignKey(
        CandidateIntakeMailbox,
        on_delete=models.CASCADE,
        related_name="intake_logs",
        verbose_name=_("Mailbox"),
    )
    message_id = models.CharField(max_length=255, unique=True)
    from_email = models.EmailField(verbose_name=_("From"))
    subject = models.CharField(max_length=255, blank=True, verbose_name=_("Subject"))
    received_at = models.DateTimeField(verbose_name=_("Received At"))
    status = models.CharField(max_length=25, choices=statuses, verbose_name=_("Status"))
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Candidate"),
    )
    matched_recruitment = models.ForeignKey(
        Recruitment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Matched Recruitment"),
    )
    error_message = models.TextField(blank=True, verbose_name=_("Reason"))
    resume = models.FileField(
        upload_to=upload_path,
        null=True,
        blank=True,
        verbose_name=_("Resume"),
        help_text=_(
            "Saved whenever a PDF attachment was found, regardless of "
            "outcome, so it can be reviewed even for failed/no-match emails."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, editable=False
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        verbose_name = _("Candidate Intake Log")
        verbose_name_plural = _("Candidate Intake Logs")
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.subject} <{self.from_email}> - {self.status}"

    def status_badge(self):
        """
        This method for get custom column showing a colored status badge.
        """
        return render_template(
            path="mailbox/intake_log_status.html",
            context={"instance": self},
        )

    def resume_link(self):
        """
        This method for get custom column showing a resume download link.
        """
        return render_template(
            path="mailbox/intake_log_resume.html",
            context={"instance": self},
        )


class IntakePollLock(models.Model):
    """
    Singleton row (always pk=1) used as a real DB-level mutex around
    recruitment.intake.run_guarded_poll(), so the candidate-intake poll
    can never run concurrently across multiple app-server processes (e.g.
    several gunicorn workers, each running its own copy of the
    APScheduler job) - a per-process cache lock can't provide that.
    """

    locked_at = models.DateTimeField(null=True, blank=True, editable=False)
    locked_by = models.CharField(
        max_length=100, blank=True, editable=False, verbose_name=_("Locked By")
    )

    class Meta:
        verbose_name = _("Intake Poll Lock")
        verbose_name_plural = _("Intake Poll Locks")

    def __str__(self):
        return f"IntakePollLock(locked_at={self.locked_at})"


class CandidateAIScore(models.Model):
    """
    A local-AI-generated ATS-style match score for a candidate against the
    job they applied for, with the reasoning behind the score. Kept fully
    separate from CandidateRating (the manual per-employee star rating).
    One row per scoring attempt - re-scoring keeps history rather than
    overwriting, and failed attempts are recorded too (why it couldn't
    score), mirroring CandidateIntakeLog's audit-trail shape.
    """

    statuses = [
        ("scored", _("Scored")),
        ("no_resume", _("No Resume Attached")),
        ("parse_failed", _("Resume Parsing Failed")),
        ("error", _("Unexpected Error")),
    ]

    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="ai_scores",
        verbose_name=_("Candidate"),
    )
    scored_against = models.ForeignKey(
        Recruitment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Scored Against"),
        help_text=_("The job opening this score was evaluated against."),
    )
    status = models.CharField(max_length=20, choices=statuses, verbose_name=_("Status"))
    score = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name=_("Score"),
    )
    key_points = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Key Points"),
        help_text=_("Short, specific reasons behind the score."),
    )
    reasoning = models.TextField(blank=True, verbose_name=_("Reasoning"))
    confidence = models.CharField(
        max_length=10,
        choices=[("high", _("High")), ("low", _("Low"))],
        blank=True,
        verbose_name=_("Confidence"),
        help_text=_(
            "Automated confidence signal - 'low' means this score was "
            "flagged (e.g. inconsistent re-scoring, or the candidate's "
            "name wasn't found in the resume) and needs manual review."
        ),
    )
    flags = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Flags"),
        help_text=_("Machine-readable reasons this score was flagged for review."),
    )
    error_message = models.TextField(blank=True, verbose_name=_("Reason"))
    model_used = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = models.Manager()
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, editable=False
    )

    class Meta:
        verbose_name = _("Candidate AI Score")
        verbose_name_plural = _("Candidate AI Scores")
        ordering = ["-created_at"]

    def __str__(self):
        if self.status == "scored":
            return f"{self.candidate} - {self.score}/100"
        return f"{self.candidate} - {self.status}"

    def score_badge(self):
        """
        This method for get custom column showing a colored score badge.
        """
        return render_template(
            path="candidate/ai_score_badge.html",
            context={"instance": self},
        )

    @property
    def score_remaining(self):
        """
        100 minus the score - the unfilled portion of the AI Score gauge
        chart on the candidate profile.
        """
        return 100 - self.score if self.score is not None else 0


class CandidateJobSuggestion(models.Model):
    """
    A local-AI-generated suggestion that a candidate's resume may be a
    stronger match for a different currently-open job in the same
    company than the one they applied to. Kept fully separate from
    CandidateAIScore (which only scores against the applied job) - see
    recruitment/ai_job_match.py. One row per check attempt, history kept.
    """

    statuses = [
        ("suggested", _("Better Match Found")),
        ("no_better_match", _("No Better Match")),
        ("no_resume", _("No Resume Attached")),
        ("parse_failed", _("Resume Parsing Failed")),
        ("error", _("Unexpected Error")),
    ]

    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="job_suggestions",
        verbose_name=_("Candidate"),
    )
    current_recruitment = models.ForeignKey(
        Recruitment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Applied Job"),
    )
    suggested_recruitment = models.ForeignKey(
        Recruitment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Suggested Job"),
    )
    status = models.CharField(max_length=20, choices=statuses, verbose_name=_("Status"))
    current_score = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name=_("Current Job Score"),
    )
    suggested_score = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name=_("Suggested Job Score"),
    )
    reasoning = models.TextField(blank=True, verbose_name=_("Reasoning"))
    error_message = models.TextField(blank=True, verbose_name=_("Reason"))
    model_used = models.CharField(max_length=100, blank=True)
    acted_on = models.BooleanField(
        default=False,
        verbose_name=_("Acted On"),
        help_text=_("Whether the candidate was moved to the suggested job."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    objects = models.Manager()
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, editable=False
    )

    class Meta:
        verbose_name = _("Candidate Job Suggestion")
        verbose_name_plural = _("Candidate Job Suggestions")
        ordering = ["-created_at"]

    def __str__(self):
        if self.status == "suggested":
            return f"{self.candidate} -> {self.suggested_recruitment} ({self.suggested_score}/100)"
        return f"{self.candidate} - {self.status}"

    @property
    def current_score_remaining(self):
        """100 minus current_score - for the applied-job gauge chart."""
        return 100 - self.current_score if self.current_score is not None else 100

    @property
    def suggested_score_remaining(self):
        """100 minus suggested_score - for the suggested-job gauge chart."""
        return 100 - self.suggested_score if self.suggested_score is not None else 100


class CandidateHistorySummary(models.Model):
    """
    A local-AI-generated, step-by-step plain-language narrative of a
    candidate's tracked history (stage moves, field edits, who made
    them and when) - one formal sentence per recorded event, rather
    than a compressed summary. One row per generation attempt -
    re-generating keeps history rather than overwriting, mirroring
    CandidateAIScore's shape.
    """

    statuses = [
        ("summarized", _("Summarized")),
        ("no_history", _("No History Available")),
        ("error", _("Unexpected Error")),
    ]

    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="history_summaries",
        verbose_name=_("Candidate"),
    )
    status = models.CharField(max_length=20, choices=statuses, verbose_name=_("Status"))
    summary = models.TextField(blank=True, verbose_name=_("Summary"))
    milestones = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Steps"),
        help_text=_(
            "One formal, plain-language sentence per recorded event, "
            "in chronological order."
        ),
    )
    error_message = models.TextField(blank=True, verbose_name=_("Reason"))
    model_used = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = models.Manager()
    company_id = models.ForeignKey(
        Company, on_delete=models.CASCADE, null=True, editable=False
    )

    class Meta:
        verbose_name = _("Candidate History Summary")
        verbose_name_plural = _("Candidate History Summaries")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.candidate} - {self.status}"


class AISettings(models.Model):
    """
    Single global switch + credentials for which LLM provider recruitment's
    AI features (email intake, resume autofill, AI scoring, job description/
    skill generation, history summaries) use. See recruitment/ai_client.py -
    nothing outside that module should branch on provider directly.

    Always exactly one row (pk=1) - see get_solo().
    """

    PROVIDERS = [
        ("ollama", _("Ollama (Local)")),
        ("openai", _("OpenAI")),
    ]

    provider = models.CharField(
        max_length=10,
        choices=PROVIDERS,
        default="ollama",
        verbose_name=_("AI Provider"),
    )
    openai_api_key = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("OpenAI API Key"),
    )
    openai_model = models.CharField(
        max_length=100,
        blank=True,
        default="gpt-4o-mini",
        verbose_name=_("OpenAI Model"),
    )
    ollama_base_url = models.CharField(
        max_length=255,
        blank=True,
        default="http://localhost:11434",
        verbose_name=_("Ollama Base URL"),
    )
    ollama_model = models.CharField(
        max_length=100,
        blank=True,
        default="llama3.2:3b",
        verbose_name=_("Ollama Model"),
    )

    def __str__(self):
        return f"AI Settings ({self.get_provider_display()})"

    @classmethod
    def get_solo(cls):
        """
        Fetches the single settings row, creating it with defaults on first
        use - callers that need to bind a row to a form (the AI Settings
        page) should use this rather than .first(), which stays None until
        someone has actually saved the form once.
        """
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
