"""
recruitment/cbv/pipeline.py
"""

from typing import Any

from django.contrib import messages
from django.core.cache import cache as CACHE
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from horilla.decorators import hx_request_required
from horilla.decorators import login_required as fbv_login_required
from horilla_views.cbv_methods import login_required
from horilla_views.generic.cbv.kanban import HorillaKanbanView
from horilla_views.generic.cbv.views import (
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    HorillaTabView,
    TemplateView,
    get_short_uuid,
)
from recruitment import filters, forms, models
from recruitment.cbv_decorators import manager_can_enter
from recruitment.decorators import manager_can_enter as fbv_manager_can_enter
from recruitment.templatetags.recruitmentfilters import (
    recruitment_manages,
    stage_manages,
)
from recruitment.views import ai_scoring
from recruitment.views.views import is_recruitmentmanager, is_stagemanager


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class PipelineView(TemplateView):
    """
    PipelineView
    """

    template_name = "cbv/pipeline/pipeline.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentTabView(HorillaTabView):
    """
    RecruitmentTabView
    """

    filter_class = filters.RecruitmentFilter

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        recruitments = self.filter_class(self.request.GET).qs.filter(
            is_active=True, closed=False
        )
        view_type = self.request.GET.get("view", "card")
        CACHE.set(
            self.request.session.session_key + "pipeline",
            {
                "stages": GetStages.filter_class(self.request.GET).qs.order_by(
                    "sequence"
                ),
                "recruitments": recruitments,
                "candidates": False,
            },
            timeout=600,
        )
        self.tabs = []
        view_perm = self.request.user.has_perm("recruitment.view_recruitment")
        change_perm = self.request.user.has_perm("recruitment.change_recruitment")
        add_cand_perm = self.request.user.has_perm("recruitment.add_candidate")
        delete_perm = self.request.user.has_perm("recruitment.delete_recruitment")
        add_stage_perm = self.request.user.has_perm("recruitment.add_stage")
        for rec in recruitments:
            rec_manager_perm = recruitment_manages(self.request.user, rec)
            stage_manage_perm = stage_manages(self.request.user, rec)
            tab = {}
            tab["title"] = rec
            url = reverse("candidate-card-cbv", kwargs={"pk": rec.pk})

            if view_type == "list":
                url = (
                    reverse("get-stages-recruitment", kwargs={"rec_id": rec.pk})
                    + f"?view={view_type}"
                )
            tab["url"] = url

            self.query_params["view"] = view_type
            tab["badge_label"] = _("Stages")
            tab["badge"] = rec.stage_set.filter(is_active=True).count()
            tab["actions"] = []
            if rec_manager_perm or change_perm:
                if add_stage_perm or rec_manager_perm or change_perm:
                    tab["actions"].append(
                        {
                            "action": _("Add Stage"),
                            "attrs": f"""
                                data-toggle="oh-modal-toggle"
                                data-target="#genericModal"
                                hx-get="{reverse('rec-stage-create')}?recruitment_id={rec.pk}"
                                hx-target="#genericModalBody"
                                style="cursor: pointer;"
                            """,
                        },
                    )

                if change_perm or rec_manager_perm:
                    tab["actions"].append(
                        {
                            "action": _("Edit"),
                            "attrs": f"""
                                data-toggle="oh-modal-toggle"
                                data-target="#genericModal"
                                hx-get="{reverse("recruitment-update-pipeline", kwargs={"pk": rec.pk})}"
                                hx-target="#genericModalBody"
                                style="cursor: pointer;"
                            """,
                        },
                    )

                if add_cand_perm or rec_manager_perm or change_perm:
                    tab["actions"].append(
                        {
                            "action": _("Resume Shortlisting"),
                            "attrs": f"""
                                data-toggle="oh-modal-toggle"
                                data-target="#bulkResumeUpload"
                                hx-get="{reverse('view-bulk-resume')}?rec_id={rec.pk}"
                                hx-target="#bulkResumeUploadBody"
                                style="cursor: pointer;"
                            """,
                        },
                    )

                if add_stage_perm or rec_manager_perm or change_perm:
                    tab["actions"].append(
                        {
                            "action": _("Manage Stage Order"),
                            "attrs": f"""
                                data-toggle="oh-modal-toggle"
                                data-target="#genericModal"
                                hx-get="{reverse("rec-update-stage-seq", kwargs={"pk": rec.pk})}"
                                hx-target="#genericModalBody"
                                style="cursor: pointer;"
                            """,
                        }
                    )

                if change_perm or rec_manager_perm:
                    if rec.closed:
                        tab["actions"].append(
                            {
                                "action": _("Reopen"),
                                "attrs": f"""
                                    href="{reverse("recruitment-reopen-pipeline", kwargs={"rec_id": rec.pk})}"
                                    style="cursor: pointer;"
                                    onclick="return confirm('Are you sure you want to reopen this recruitment?');"
                                """,
                            },
                        )
                    else:
                        tab["actions"].append(
                            {
                                "action": _("Close"),
                                "attrs": f"""
                                    href="{reverse("recruitment-close-pipeline", kwargs={"rec_id": rec.pk})}"
                                    style="cursor: pointer;"
                                    onclick="return confirm('Are you sure you want to close this recruitment?');"
                                """,
                            },
                        )
                if delete_perm:
                    tab["actions"].append(
                        {
                            "action": _("Delete"),
                            "attrs": f"""
                                data-toggle="oh-modal-toggle"
                                data-target="#deleteConfirmation"
                                hx-get="{reverse('generic-delete')}?model=recruitment.Recruitment&pk={rec.pk}"
                                hx-target="#deleteConfirmationBody"
                                style="cursor: pointer;"
                            """,
                        }
                    )
            if stage_manage_perm or view_perm:
                self.tabs.append(tab)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_filter_tags"] = True

        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(hx_request_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class GetStages(TemplateView):
    """
    GetStages
    """

    filter_class = filters.StageFilter

    template_name = "cbv/pipeline/stages.html"
    stages = None

    def get(self, request, *args, **kwargs):
        """
        get method
        """
        rec_id = kwargs["rec_id"]
        cache = CACHE.get(request.session.session_key + "pipeline")
        if cache is None:
            cache = {
                "stages": self.filter_class(request.GET).qs.order_by("sequence"),
                "candidates": False,
            }
            CACHE.set(request.session.session_key + "pipeline", cache, timeout=600)
        if not cache.get("candidates"):
            cache["candidates"] = CandidateList.filter_class(
                self.request.GET
            ).qs.filter(is_active=True)
            CACHE.set(request.session.session_key + "pipeline", cache)

        self.stages = cache["stages"].filter(recruitment_id=rec_id)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stages"] = self.stages
        context["view_id"] = get_short_uuid(6, "hsv")
        context["rec_id"] = kwargs["rec_id"]
        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class CandidateList(HorillaListView):
    """
    CandidateList
    """

    model = models.Candidate
    filter_class = filters.CandidateFilter
    filter_selected = False
    quick_export = False
    next_prev = False
    show_filter_tags = True
    filter_keys_to_remove = ["rec_id", "obj_id"]
    records_per_page = 10
    records_count_in_tab = False

    custom_empty_template = "cbv/pipeline/empty.html"
    header_attrs = {
        "action": """ style="width:400px;" """,
        "mobile": """ style="width:100px;" """,
        "Stage": """ style="width:100px;" """,
        "get_interview_count": """ style="width:200px;" """,
        "option": """ style="width:280px !important" """,
    }
    columns = [
        (_("Name"), "candidate_name", "get_avatar"),
        (_("Email"), "mail_indication"),
        (_("Stage"), "stage_drop_down"),
        (_("Rating"), "rating_bar"),
        (_("AI Score"), "ai_score_number"),
        (_("Date of Apply"), "applied_on_display"),
        (_("Hired Date"), "hired_date"),
        (_("Scheduled Interview"), "get_interview_count"),
        (_("Job Position"), "job_position_id__job_position"),
        (_("Contact"), "mobile"),
    ]

    export_columns = [
        (_("Name"), "candidate_name", "get_avatar"),
        (_("Email"), "mail_indication"),
        (_("Stage"), "stage_id"),
        (_("Rating"), "get_avg_rating"),
        (_("Date of Apply"), "applied_on_display"),
        (_("Hired Date"), "hired_date"),
        (_("Scheduled Interview"), "get_total_interview"),
        (_("Job Position"), "job_position_id__job_position"),
        (_("Contact"), "mobile"),
    ]

    default_columns = [
        (_("Name"), "candidate_name", "get_avatar"),
        (_("Email"), "mail_indication"),
        (_("Stage"), "stage_drop_down"),
        (_("AI Score"), "ai_score_number"),
        (_("Date of Apply"), "applied_on_display"),
    ]

    bulk_update_fields = [
        "stage_id",
        "hired_date",
    ]

    row_attrs = """
        class="cursor-pointer"
        onclick="window.location.href = '{get_profile_url}?next=' + encodeURIComponent(window.location.pathname + window.location.search)"
    """

    actions = [
        {
            "action": _("Schedule Interview"),
            "icon": "time-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                hx-get = "{get_schedule_interview}"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
            """,
        },
        {
            "action": _("Send Mail"),
            "icon": "mail-open-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                hx-get = "{get_send_mail}"
                data-toggle="oh-modal-toggle"
                data-target="#objectDetailsModal"
                hx-target="#objectDetailsModalTarget"
            """,
        },
        {
            "action": _("Add to Skill Zone"),
            "icon": "heart-circle-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100 disabled"
                data-toggle="oh-modal-toggle"
                hx-get="{get_skill_zone_url}"
                data-target="#genericModal"
                hx-target="#genericModalBody"
            """,
        },
        {
            "action": _("Reject Candidate"),
            "icon": "thumbs-down-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                data-toggle="oh-modal-toggle"
                hx-get="{get_rejected_candidate_url}"
                {rejected_candidate_class}
                data-target="#genericModal"
                hx-target="#genericModalBody"
            """,
        },
        {
            "action": _("View Note"),
            "icon": "newspaper-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100 oh-activity-sidebar__open"
                hx-get="{get_view_note_url}"
                data-target="#activitySidebar"
                hx-target="#activitySidebar"
                onclick="$('#activitySidebar').addClass('oh-activity-sidebar--show')"
            """,
        },
        {
            "action": _("Document Request"),
            "icon": "document-attach-outline",
            "attrs": """
                hx-get="{get_document_request}"
                data-target="#genericModal"
                hx-target="#genericModalBody"
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                data-toggle="oh-modal-toggle"
            """,
        },
        {
            "action": _("Resume"),
            "icon": "document-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                href="{get_resume_url}" target="_blank"
            """,
        },
        {
            "action": _("Move to Pipeline"),
            "icon": "swap-horizontal-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                hx-get="{get_move_pipeline_url}"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
            """,
        },
    ]

    def get_bulk_form(self):
        form = super().get_bulk_form()
        form.fields["stage_id"].queryset = form.fields["stage_id"].queryset.filter(
            recruitment_id=self.kwargs["rec_id"]
        )
        return form

    def bulk_update_accessibility(self):
        """
        Bulk Update accessiblity
        """
        if not self.kwargs.get("stage_id"):
            return super().bulk_update_accessibility()
        first_cand_in_stage = self.queryset.first()
        return super().bulk_update_accessibility() or (
            first_cand_in_stage
            and (
                self.request.user.employee_get
                in first_cand_in_stage.stage_id.stage_managers.all()
                or self.request.user.employee_get
                in first_cand_in_stage.recruitment_id.recruitment_managers.all()
            )
        )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.search_url = self.request.path

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not self.bulk_update_accessibility():
            context["actions"] = []
        return context

    def get(self, request, *args, **kwargs):
        self.selected_instances_key_id = f"selectedCandidateRecords{kwargs['stage_id']}"
        return super().get(request, *args, **kwargs)

    def get_queryset(self, *args, **kwargs):
        if self.queryset is None:
            queryset = CACHE.get(self.request.session.session_key + "pipeline")[
                "candidates"
            ].filter(stage_id=self.kwargs["stage_id"])
            queryset = queryset.filter(stage_id=self.kwargs["stage_id"])
            # Highest AI-scored candidate first; not-yet-scored candidates
            # (NULL) sort last regardless of DB backend.
            queryset = queryset.order_by(F("latest_ai_score").desc(nulls_last=True))
            super().get_queryset(queryset=queryset, filtered=True)

        return self.queryset


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class CandidateCard(HorillaKanbanView):
    model = models.Candidate
    filter_class = filters.CandidateFilter
    group_filter_class = filters.StageFilter
    group_key = "stage_id"
    records_per_page = 10
    filter_keys_to_remove = ["rec_id", "obj_id"]
    group_label_key = "stage"
    # Highest AI-scored candidate first within each stage by default;
    # not-yet-scored candidates (NULL) sort last regardless of DB backend.
    instance_order_by = F("latest_ai_score").desc(nulls_last=True)

    kanban_attrs = """
        onclick="window.location.href = '{get_profile_url}?next=' + encodeURIComponent(window.location.pathname + window.location.search)"
    """

    details = {
        "image_src": "{get_avatar}",
        "title": "{get_full_name}",
        "email": "{email}",
        "position": "{job_position_id__job_position}",
        "date of apply": "{applied_on_display}",
        "rating": "{rating_bar}",
    }

    # The generic kanban card template derives each detail row's label from
    # its dict key via Django's `title` filter, which mangles the "AI"
    # acronym into "Ai" (and leaves the underscore in "ai_score" untouched)
    # - so this row is rendered separately with the label spelled out
    # correctly instead of going through `details`.
    custom_card_content_template = "cbv/pipeline/ai_score_kanban_row.html"

    group_actions = [
        {
            "action": _("Add Candidate"),
            "accessibility": "recruitment.accessibility.add_candidate_accessibility",
            "attrs": """
                hx-target="#objectCreateModalTarget"
                hx-get="{get_add_candidate_url}"
                data-toggle="oh-modal-toggle"
                data-target="#objectCreateModal"
            """,
        },
        {
            "action": _("Edit"),
            "accessibility": "recruitment.accessibility.edit_stage_accessibility",
            "attrs": """
                hx-target="#genericModalBody"
                hx-get="{get_stage_update_url}"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
            """,
        },
        {
            "action": _("Bulk Mail"),
            "accessibility": "recruitment.accessibility.edit_stage_accessibility",
            "attrs": """
                hx-target="#objectCreateModalTarget"
                hx-get="{get_send_email_url}"
                data-toggle="oh-modal-toggle"
                data-target="#objectCreateModal"
            """,
        },
        {
            "action": _("Delete"),
            "accessibility": "recruitment.accessibility.delete_stage_accessibility",
            "attrs": """
                hx-target="#deleteConfirmationBody"
                hx-get="{get_delete_url}"
                data-toggle="oh-modal-toggle"
                data-target="#deleteConfirmation"
            """,
        },
    ]

    actions = [
        {
            "action": _("Schedule Interview"),
            "attrs": """
                hx-get = "{get_schedule_interview}"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
            """,
        },
        {
            "action": _("Send Mail"),
            "attrs": """
                hx-get = "{get_send_mail}"
                data-toggle="oh-modal-toggle"
                data-target="#objectDetailsModal"
                hx-target="#objectDetailsModalTarget"
            """,
        },
        {
            "action": "Add to Skill Zone",
            "accessibility": "recruitment.cbv.accessibility.add_skill_zone",
            "attrs": """
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-get="{get_add_to_skill}"
                hx-target="#genericModalBody"
                class="oh-dropdown__link"

            """,
        },
        {
            "action": "View candidate self tracking",
            "accessibility": "recruitment.cbv.accessibility.check_candidate_self_tracking",
            "attrs": """
                href="{get_self_tracking_url}"
                class="oh-dropdown__link"
            """,
        },
        {
            "action": "Request Document",
            "accessibility": "recruitment.cbv.accessibility.request_document",
            "attrs": """
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-get="{get_document_request_doc}"
                hx-target="#genericModalBody"
                class="oh-dropdown__link"
            """,
        },
        {
            "action": "Add to Rejected",
            "accessibility": "recruitment.cbv.accessibility.add_reject",
            "attrs": """
                hx-target="#genericModalBody"
                hx-swap="innerHTML"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-get="{get_add_to_reject}"
                class="oh-dropdown__link"
            """,
        },
        {
            "action": "Edit Rejected Candidate",
            "accessibility": "recruitment.cbv.accessibility.edit_reject",
            "attrs": """
                hx-target="#genericModalBody"
                hx-swap="innerHTML"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-get="{get_add_to_reject}"
                class="oh-dropdown__link"
            """,
        },
        {
            "action": _("View Note"),
            "attrs": """
                hx-get="{get_view_note_url}"
                data-target="#activitySidebar"
                hx-target="#activitySidebar"
                onclick="$('#activitySidebar').addClass('oh-activity-sidebar--show')"
            """,
        },
        {
            "action": _("Resume"),
            "attrs": """
                href="{get_resume_url}" target="_blank"
            """,
        },
        {
            "action": _("Move to Pipeline"),
            "attrs": """
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-get="{get_move_pipeline_url}"
                hx-target="#genericModalBody"
                class="oh-dropdown__link"
            """,
        },
        {
            "action": "archive_status",
            "attrs": """
                class="oh-dropdown__link"
                onclick="archiveCandidate({get_archive_url});"
            """,
        },
        {
            "action": "Delete",
            "attrs": """
                class="oh-dropdown__link oh-dropdown__link--danger"
                onclick="event.stopPropagation();
                deleteCandidate('{get_delete_url}'); "
            """,
        },
    ]

    def get_related_groups(self, *args, **kwargs):
        related_groups = super().get_related_groups(*args, **kwargs)
        rec_id = self.kwargs.get("pk")
        if rec_id:
            related_groups = related_groups.filter(recruitment_id=rec_id)

        return related_groups


@fbv_login_required
@fbv_manager_can_enter(perm="recruitment.change_candidate")
def recruitment_stage_options(request, rec_id):
    """
    JSON list of {id, stage, initial} for a recruitment's stages -
    powers the job-opening -> stage cascading dropdown on the "Move to
    Pipeline" modal (candidate/move_pipeline_form.html).
    """
    stages = models.Stage.objects.filter(recruitment_id=rec_id).order_by("sequence")
    return JsonResponse(
        {
            "stages": [
                {
                    "id": stage.pk,
                    "stage": stage.stage,
                    "initial": stage.stage_type == "initial",
                }
                for stage in stages
            ]
        }
    )


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.change_candidate"), name="dispatch"
)
class CandidateMovePipelineFormView(HorillaFormView):
    """
    Modal form to move a candidate from their current recruitment
    pipeline to a different open job (and a specific stage in it) in the
    same company - available as an action from the Pipeline card and
    list views, and as the "Move to suggested job" action on the AI Job
    Match card (see recruitment/templates/candidate/job_suggestion_status.html).
    """

    model = models.Candidate
    form_class = forms.CandidateMovePipelineForm
    new_display_title = _("Move to Pipeline")
    template_name = "candidate/move_pipeline_form.html"

    def get_initial(self):
        initial = super().get_initial()
        suggested_recruitment_id = self.request.GET.get("suggested_recruitment")
        if suggested_recruitment_id:
            initial["recruitment_id"] = suggested_recruitment_id
        return initial

    def form_valid(self, form: forms.CandidateMovePipelineForm) -> HttpResponse:
        if not form.is_valid():
            return super().form_valid(form)

        candidate = form.instance
        target_recruitment = form.cleaned_data["recruitment_id"]
        target_stage = form.cleaned_data["stage_id"]

        is_authorized = (
            self.request.user.is_superuser
            or is_stagemanager(self.request)[1]
            .filter(recruitment_id=target_recruitment)
            .exists()
            or is_recruitmentmanager(self.request, rec_id=target_recruitment.id)[0]
        )
        if not is_authorized:
            messages.error(
                self.request, _("You don't have manager access to the target job.")
            )
            return self.HttpResponse()

        # Candidate.save() requires job_position_id to be one of the target
        # recruitment's open_positions whenever recruitment_id changes -
        # without reassigning it here, the save below raises a
        # ValidationError that htmx silently swallows. recruitment.job_position_id
        # is frequently unset even for non-event-based postings (only
        # open_positions gets filled in when the recruitment is created), so
        # that field can't be trusted on its own - fall back to open_positions.
        target_job_position = target_recruitment.job_position_id
        if (
            target_job_position is None
            or not target_recruitment.open_positions.filter(
                pk=target_job_position.pk
            ).exists()
        ):
            target_job_position = target_recruitment.open_positions.first()

        if target_job_position is None:
            messages.error(
                self.request,
                _(
                    "%(job)s has no open job position configured, so no "
                    "candidate can be moved into it. Add one under Open "
                    "Positions on that recruitment first."
                )
                % {"job": target_recruitment},
            )
            return self.HttpResponse()

        candidate.job_position_id = target_job_position
        candidate.stage_id = target_stage
        candidate.hired = bool(target_stage and target_stage.stage_type == "hired")
        candidate.canceled = bool(
            target_stage and target_stage.stage_type == "cancelled"
        )
        candidate.start_onboard = False
        candidate.has_better_job_suggestion = False
        try:
            candidate.save()
        except DjangoValidationError as exc:
            messages.error(
                self.request, _("Could not move candidate: %(error)s") % {"error": exc}
            )
            return self.HttpResponse()

        # Keep the AI Job Match card's "already moved" state in sync,
        # regardless of whether the move happened via this generic modal
        # or the AI-suggestion-specific flow.
        from recruitment.models import CandidateJobSuggestion

        CandidateJobSuggestion.objects.filter(
            candidate=candidate,
            status="suggested",
            acted_on=False,
            suggested_recruitment=target_recruitment,
        ).update(acted_on=True)

        # The Pipeline board caches its candidate queryset per session (see
        # RecruitmentTabView/GetStages above) - without clearing it here,
        # the old recruitment's board keeps showing this candidate under
        # their old stage until that cache naturally expires.
        if self.request.session.session_key:
            CACHE.delete(self.request.session.session_key + "pipeline")

        # The AI Score was computed against the job the candidate just
        # left - refresh it in the background so it doesn't keep showing
        # a score against a job they're no longer applying for.
        ai_scoring.kick_off_rescoring(candidate.pk)

        messages.success(
            self.request,
            _("Candidate moved to %(job)s.") % {"job": target_recruitment},
        )
        return self.HttpResponse()


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class PipelineNav(HorillaNavView):
    """
    HorillaNavView
    """

    search_url = reverse_lazy("cbv-pipeline-tab")
    nav_title = _("Pipeline")
    search_swap_target = "#pipelineContainer"
    filter_body_template = "cbv/pipeline/pipeline_filter.html"
    filter_instance = filters.RecruitmentFilter()
    filter_form_context_name = "form"
    apply_first_filter = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.request.user.has_perm("recruitment.add_recruitment"):
            self.create_attrs = f"""
                hx-get="{reverse_lazy('recruitment-create')}?{urlencode({'pipeline': 'true'})}"
                hx-target="#genericModalBody"
                data-target="#genericModal"
                data-toggle="oh-modal-toggle"
            """
        else:
            self.create_attrs = None

        rec_id = self.request.GET.get("obj_id", "")
        id_suffix = f"&obj_id={rec_id}" if rec_id else ""
        self.view_types = [
            {
                "type": "list",
                "icon": "list-outline",
                "url": f'{reverse_lazy("cbv-pipeline-tab")}?view=list{id_suffix}',
                "attrs": f"""
                    title ='List'
                """,
            },
            {
                "type": "card",
                "icon": "grid-outline",
                "url": f'{reverse_lazy("cbv-pipeline-tab")}?view=card{id_suffix}',
                "attrs": f"""
                    title ='Card'
                """,
            },
        ]

    def get_context_data(self, **kwargs):
        """
        context data
        """
        context = super().get_context_data(**kwargs)
        stage_filter_obj = GetStages.filter_class()
        candidate_filter_obj = CandidateList.filter_class()
        context["stage_filter_obj"] = stage_filter_obj
        context["candidate_filter_obj"] = candidate_filter_obj
        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.view_recruitment"), name="dispatch"
)
class ChangeStage(HorillaFormView):
    """
    Change Candidate stage
    """

    model = models.Candidate
    form_class = forms.StageChangeForm

    def form_valid(self, form):
        if form.is_valid():
            messages.success(self.request, _("Stage Updated"))
            form.save()
            return self.HttpResponse()
        messages.info(self.request, _("Stage not updated"))

        return self.HttpResponse()
