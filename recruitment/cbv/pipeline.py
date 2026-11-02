"""
recruitment/cbv/pipeline.py
"""

from typing import Any

from django.contrib import messages
from django.core.cache import cache as CACHE
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, F
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
        recruitments = list(
            self.filter_class(self.request.GET).qs.filter(
                is_active=True, closed=False
            )
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

        # This tab bar isn't paginated (every open recruitment is a tab), so
        # recruitment_manages/stage_manages/stage count used to run 3-4
        # queries EACH, per recruitment, on every single load. Bulk-fetch
        # all three into plain dicts/sets up front instead - 3 queries
        # total no matter how many recruitments there are.
        rec_ids = [rec.pk for rec in recruitments]
        employee = getattr(self.request.user, "employee_get", None)
        managed_rec_ids = set()
        stage_managed_rec_ids = set()
        if employee is not None:
            managed_rec_ids = set(
                models.Recruitment.objects.filter(
                    id__in=rec_ids, recruitment_managers=employee
                ).values_list("id", flat=True)
            )
            stage_managed_rec_ids = set(
                models.Stage.objects.filter(
                    recruitment_id__in=rec_ids, stage_managers=employee
                ).values_list("recruitment_id", flat=True)
            )
        stage_counts = dict(
            models.Stage.objects.filter(recruitment_id__in=rec_ids, is_active=True)
            .values_list("recruitment_id")
            .annotate(cnt=Count("id"))
        )

        for rec in recruitments:
            rec_manager_perm = rec.pk in managed_rec_ids
            stage_manage_perm = rec.pk in stage_managed_rec_ids or rec_manager_perm
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
            tab["badge"] = stage_counts.get(rec.pk, 0)
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

                # Resume Shortlisting is disabled - not currently in use.
                # if add_cand_perm or rec_manager_perm or change_perm:
                #     tab["actions"].append(
                #         {
                #             "action": _("Resume Shortlisting"),
                #             "attrs": f"""
                #                 data-toggle="oh-modal-toggle"
                #                 data-target="#bulkResumeUpload"
                #                 hx-get="{reverse('view-bulk-resume')}?rec_id={rec.pk}"
                #                 hx-target="#bulkResumeUploadBody"
                #                 style="cursor: pointer;"
                #             """,
                #         },
                #     )

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
                                    data-post-link
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
                                    data-post-link
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
        # the applied filter is shown as a red dot on the Filter button instead
        context["show_filter_tags"] = False

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
    show_filter_tags = False
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
        (_("LOI Status"), "loi_status_badge"),
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
            "action": _("Send LOI"),
            "icon": "document-text-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                hx-get="{get_send_loi_url}"
                data-toggle="oh-modal-toggle"
                data-target="#objectCreateModal"
                hx-target="#objectCreateModalTarget"
            """,
        },
        {
            "action": _("Request Document"),
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
            "action": _("Update Status"),
            "icon": "swap-horizontal-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100"
                hx-get="{get_update_status_url}"
                {rejected_candidate_class}
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-target="#genericModalBody"
            """,
        },
        {
            "action": _("Chat & Notes"),
            "icon": "chatbubbles-outline",
            "attrs": """
                class="oh-btn oh-btn--danger-outline oh-btn--light-bkg w-100 oh-activity-sidebar__open"
                hx-get="{get_view_note_url}"
                data-target="#activitySidebar"
                hx-target="#activitySidebar"
                onclick="$('#activitySidebar').addClass('oh-activity-sidebar--show')"
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
    show_filter_tags = False
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
    kanban_card_template = "cbv/pipeline/candidate_kanban_card.html"
    kanban_empty_template = "cbv/pipeline/candidate_kanban_empty.html"

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
            "action": _("Send LOI"),
            "attrs": """
                hx-get="{get_send_loi_url}"
                data-toggle="oh-modal-toggle"
                data-target="#objectCreateModal"
                hx-target="#objectCreateModalTarget"
            """,
        },
        {
            "action": _("Request Document"),
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
            "action": _("Update Status"),
            "attrs": """
                hx-target="#genericModalBody"
                hx-swap="innerHTML"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                hx-get="{get_update_status_url}"
                class="oh-dropdown__link"
            """,
        },
        {
            "action": _("Chat & Notes"),
            "attrs": """
                hx-get="{get_view_note_url}"
                data-target="#activitySidebar"
                hx-target="#activitySidebar"
                onclick="$('#activitySidebar').addClass('oh-activity-sidebar--show')"
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
                    "initial": stage.stage_type == "applied",
                }
                for stage in stages
            ]
        }
    )


def _candidate_reject_eligible(candidate) -> bool:
    """
    Mirrors recruitment.cbv.accessibility.add_reject/edit_reject's core
    condition - a candidate who has already been converted to a platform
    user (an Employee login exists for their email) can't sensibly be
    rejected anymore. Used to hide the Add to Reject section in the
    Update Status modal, and to reject the submission server-side too if
    someone gets past that regardless (a stale page, a direct POST).
    """
    from horilla_auth.models import HorillaUser

    return not HorillaUser.objects.filter(username=candidate.email).exists()


def _apply_candidate_move(request, candidate, target_recruitment, target_stage) -> bool:
    """
    Shared by CandidateMovePipelineFormView and CandidateUpdateStatusFormView:
    moves `candidate` to `target_recruitment`/`target_stage`, handling
    authorization, job-position reassignment, AI-suggestion bookkeeping,
    pipeline cache invalidation, and AI re-scoring. Returns True on success;
    on failure it has already set a messages.error() and the caller should
    just return its own HttpResponse without doing anything further.
    """
    # Holding the "change candidate" permission (for example an HR Manager group) is enough to move a
    # candidate into any job; without it you must manage the target job as a recruitment or stage manager.
    is_authorized = (
        request.user.is_superuser
        or request.user.has_perm("recruitment.change_candidate")
        or is_stagemanager(request)[1].filter(recruitment_id=target_recruitment).exists()
        or is_recruitmentmanager(request, rec_id=target_recruitment.id)[0]
    )
    if not is_authorized:
        messages.error(request, _("You don't have manager access to the target job."))
        return False

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
        or not target_recruitment.open_positions.filter(pk=target_job_position.pk).exists()
    ):
        target_job_position = target_recruitment.open_positions.first()

    if target_job_position is None:
        messages.error(
            request,
            _(
                "%(job)s has no open job position configured, so no "
                "candidate can be moved into it. Add one under Open "
                "Positions on that recruitment first."
            )
            % {"job": target_recruitment},
        )
        return False

    candidate.job_position_id = target_job_position
    candidate.stage_id = target_stage
    candidate.hired = bool(target_stage and target_stage.stage_type == "hired")
    candidate.canceled = bool(target_stage and target_stage.stage_type == "cancelled")
    candidate.start_onboard = False
    candidate.has_better_job_suggestion = False
    try:
        candidate.save()
    except DjangoValidationError as exc:
        messages.error(request, _("Could not move candidate: %(error)s") % {"error": exc})
        return False

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
    if request.session.session_key:
        CACHE.delete(request.session.session_key + "pipeline")

    # The AI Score was computed against the job the candidate just
    # left - refresh it in the background so it doesn't keep showing
    # a score against a job they're no longer applying for.
    ai_scoring.kick_off_rescoring(candidate.pk)
    return True


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

        if not _apply_candidate_move(self.request, candidate, target_recruitment, target_stage):
            return self.HttpResponse()

        messages.success(
            self.request,
            _("Candidate moved to %(job)s.") % {"job": target_recruitment},
        )
        return self.HttpResponse()


@method_decorator(login_required, name="dispatch")
@method_decorator(
    manager_can_enter(perm="recruitment.change_candidate"), name="dispatch"
)
class CandidateUpdateStatusFormView(HorillaFormView):
    """
    "Update Status" - combines what used to be two separate pipeline
    actions (Move to Pipeline, Add to Rejected) into one: dispositioning a
    candidate (advance them, reject them, or both at once - e.g. rejecting
    them from one job while leaving them moved into a different one they're
    a better fit for) is a single decision in practice, not two unrelated
    ones. The reject fields stay hidden client-side until "Add to Reject"
    is checked (see candidate/update_status_form.html); this view re-uses
    the exact same move-candidate logic as CandidateMovePipelineFormView so
    both actions keep behaving identically where they overlap.
    """

    model = models.Candidate
    form_class = forms.CandidateUpdateStatusForm
    new_display_title = _("Update Status")
    template_name = "candidate/update_status_form.html"

    def init_form(self, *args, data={}, files={}, instance=None, **kwargs):
        # HorillaFormView builds the form itself (see get_form() above) -
        # this is its actual extension point, not Django's usual
        # get_form_kwargs(). `instance` is already the Candidate resolved
        # from the URL's pk by the time this runs.
        existing_rejection = None
        reject_eligible = bool(instance and instance.pk and _candidate_reject_eligible(instance))
        if reject_eligible:
            existing_rejection = models.RejectedCandidate.objects.filter(
                candidate_id=instance
            ).first()
        return self.form_class(
            data,
            files,
            instance=instance,
            initial=self.get_initial(),
            existing_rejection=existing_rejection,
            reject_eligible=reject_eligible,
        )

    def form_valid(self, form: "forms.CandidateUpdateStatusForm") -> HttpResponse:
        if not form.is_valid():
            return super().form_valid(form)

        candidate = form.instance
        target_recruitment = form.cleaned_data["recruitment_id"]
        target_stage = form.cleaned_data["stage_id"]
        reject_eligible = _candidate_reject_eligible(candidate)
        adding_to_reject = form.cleaned_data.get("add_to_reject") and reject_eligible

        if adding_to_reject:
            # Capture the candidate's real (pre-submission) stage type -
            # not candidate.stage_id here, since Django's own ModelForm
            # _post_clean() already rewrote form.instance.stage_id to the
            # new target stage during form.is_valid() above, before this
            # method even started. A fresh DB read is the only way left
            # to see what stage they were actually in. This has to happen
            # before _apply_candidate_move() saves the move below - the
            # rejection mail automation fires on a background thread off
            # RejectedCandidate.save() and re-reads stage_id.stage_type
            # fresh from the DB at that point, so without this snapshot
            # it would always see the post-move (Rejected) stage, never
            # the one they were actually rejected from. See
            # Candidate.stage_type_at_rejection.
            previous_stage = (
                models.Candidate.objects.filter(pk=candidate.pk)
                .values_list("stage_id__stage_type", flat=True)
                .first()
            )
            candidate.stage_type_at_rejection = previous_stage

        if not _apply_candidate_move(self.request, candidate, target_recruitment, target_stage):
            return self.HttpResponse()

        rejected = False
        unrejected = False
        if adding_to_reject:
            rejected_candidate = _save_rejection(
                candidate,
                form.cleaned_data.get("description", ""),
                form.cleaned_data["reject_reason_id"],
            )
            rejected = True
        elif reject_eligible:
            # "Add to Reject" started checked (see init_form's
            # existing_rejection prefill) and was unchecked on submit -
            # that's the user reverting the rejection, not just leaving an
            # unrelated field alone, so the RejectedCandidate row has to
            # actually go, not just stay ignored.
            deleted_count, _deleted_per_model = models.RejectedCandidate.objects.filter(
                candidate_id=candidate
            ).delete()
            if deleted_count:
                candidate.stage_type_at_rejection = None
                candidate.save(update_fields=["stage_type_at_rejection"])
                unrejected = True

        if rejected:
            messages.success(
                self.request,
                _("Candidate moved to %(job)s and marked as rejected.")
                % {"job": target_recruitment},
            )
        elif unrejected:
            messages.success(
                self.request,
                _("Candidate moved to %(job)s and removed from rejected candidates.")
                % {"job": target_recruitment},
            )
        else:
            messages.success(
                self.request,
                _("Candidate moved to %(job)s.") % {"job": target_recruitment},
            )
        return self.HttpResponse()


def _save_rejection(candidate, description, reasons):
    """
    Create or update the candidate's rejection record and its reasons.

    Not ``update_or_create``: that locks the row (SELECT ... FOR UPDATE), and while a company
    is selected in the company switcher the company-scoped manager adds DISTINCT to every
    query - PostgreSQL refuses the two together ("FOR UPDATE is not allowed with DISTINCT
    clause"), which made "Add to Reject" fail with a 500. The unscoped manager is used here:
    the candidate is already known, and one rejection exists per candidate.
    """
    rejected_candidate, created = models.RejectedCandidate.objects.entire().get_or_create(
        candidate_id=candidate, defaults={"description": description}
    )
    if not created:
        rejected_candidate.description = description
        rejected_candidate.save()
    rejected_candidate.reject_reason_id.set(reasons)
    return rejected_candidate


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
    filter_icon_only = True
    show_clear_filter = True
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
