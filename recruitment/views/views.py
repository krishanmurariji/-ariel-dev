"""
views.py

This module contains the view functions for handling HTTP requests and rendering
responses in your application.

Each view function corresponds to a specific URL route and performs the necessary
actions to handle the request, process data, and generate a response.

This module is part of the recruitment project and is intended to
provide the main entry points for interacting with the application's functionality.
"""

import ast
import contextlib
import io
import json
import logging
import os
import random
import re
from datetime import date, datetime, timedelta
from itertools import chain
from urllib.parse import parse_qs

import fitz  # type: ignore
from django import template
from django.conf import settings
from django.contrib import messages
from django.core import serializers
from django.core.cache import cache as CACHE
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, ProtectedError, Q, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, strip_tags
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from base.backends import ConfiguredEmailBackend
from base.context_processors import check_candidate_self_tracking
from base.countries import country_arr, s_a, states
from base.forms import MailTemplateForm
from base.mail_letterhead import (
    attach_letterhead_images,
    build_letterhead_email,
    render_letterhead_content,
)
from base.methods import (
    eval_validate,
    export_data,
    generate_pdf,
    get_key_instances,
    sortby,
)
from base.models import EmailLog, HorillaMailTemplate, JobPosition, clear_messages
from employee.models import Employee, EmployeeWorkInformation
from employee.views import get_content_type
from horilla import settings
from horilla.decorators import (
    any_permission_required,
    hx_request_required,
    logger,
    login_required,
    permission_required,
)
from horilla.group_by import group_by_queryset
from horilla.http import HorillaRedirect
from horilla_auth.models import HorillaUser
from horilla_documents.models import Document
from notifications.signals import notify
from recruitment.ai_description import (
    DescriptionGenerationError,
    generate_job_description,
    generate_job_skills,
    generate_skills_from_description,
)
from recruitment.auth import CandidateAuthenticationBackend
from recruitment.decorators import (
    all_manager_can_enter,
    candidate_login_required,
    manager_can_enter,
    recruitment_manager_can_enter,
)
from recruitment.filters import (
    CandidateFilter,
    CandidateReGroup,
    InterviewFilter,
    RecruitmentFilter,
    SkillZoneCandFilter,
    SkillZoneFilter,
    StageFilter,
)
from recruitment.forms import (
    AddCandidateForm,
    CandidateCreationForm,
    CandidateDocumentForm,
    CandidateDocumentRejectForm,
    CandidateDocumentRequestForm,
    CandidateDocumentUpdateForm,
    CandidateExportForm,
    RecruitmentCreationForm,
    RejectReasonForm,
    ResumeForm,
    ScheduleInterviewForm,
    SkillsForm,
    SkillZoneCandidateForm,
    SkillZoneCreateForm,
    StageCreationForm,
    StageNoteForm,
    StageNoteUpdateForm,
    ToSkillZoneForm,
)
from onboarding.models import CandidateChecklistTask
from recruitment.methods import recruitment_manages
from recruitment.realtime import broadcast_new_message
from recruitment.models import (
    Candidate,
    CandidateDocument,
    CandidateRating,
    InterviewSchedule,
    LinkedInAccount,
    Platform,
    Recruitment,
    RecruitmentGeneralSetting,
    RecruitmentSurvey,
    RecruitmentSurveyAnswer,
    RejectedCandidate,
    RejectReason,
    Resume,
    Skill,
    SkillZone,
    SkillZoneCandidate,
    Stage,
    StageFiles,
    StageNote,
)
from recruitment.views.linkedin import delete_post, post_recruitment_in_linkedin
from recruitment.views.paginator_qry import paginator_qry

logger = logging.getLogger(__name__)


def is_stagemanager(request, stage_id=False):
    """
    This method is used to identify the employee is a stage manager or
    not, if stage_id is passed through args, method will
    check the employee is manager to the corresponding stage, return
    tuple with boolean and all stages that employee is manager.
    if called this method without stage_id args it will return boolean
     with all the stage that the employee is stage manager
    Args:
        request : django http request
        stage_id : stage instance id
    """
    user = request.user
    employee = user.employee_get
    if not stage_id:
        return (
            employee.stage_set.exists() or user.is_superuser,
            employee.stage_set.all(),
        )
    stage_obj = Stage.objects.get(id=stage_id)
    return (
        employee in stage_obj.stage_managers.all()
        or user.is_superuser
        or is_recruitmentmanager(request, rec_id=stage_obj.recruitment_id.id)[0],
        employee.stage_set.all(),
    )


def is_recruitmentmanager(request, rec_id=False):
    """
    This method is used to identify the employee is a recruitment
    manager or not, if rec_id is passed through args, method will
    check the employee is manager to the corresponding recruitment,
    return tuple with boolean and all recruitment that employee is manager.
    if called this method without recruitment args it will return
    boolean with all the recruitment that the employee is recruitment manager
    Args:
        request : django http request
        rec_id : recruitment instance id
    """
    user = request.user
    employee = user.employee_get
    if not rec_id:
        return (
            employee.recruitment_set.exists() or user.is_superuser,
            employee.recruitment_set.all(),
        )
    recruitment_obj = Recruitment.objects.get(id=rec_id)
    return (
        employee in recruitment_obj.recruitment_managers.all() or user.is_superuser,
        employee.recruitment_set.all(),
    )


def pipeline_grouper(request, recruitments):
    groups = []
    for rec in recruitments:
        stages = StageFilter(request.GET, queryset=rec.stage_set.all()).qs.order_by(
            "sequence"
        )
        all_stages_grouper = []
        data = {"recruitment": rec, "stages": []}
        for stage in stages.order_by("sequence"):
            all_stages_grouper.append({"grouper": stage, "list": []})
            stage_candidates = CandidateFilter(
                request.GET,
                stage.candidate_set.filter(
                    is_active=True,
                ),
            ).qs.order_by("sequence")

            page_name = "page" + stage.stage + str(rec.id)
            grouper = group_by_queryset(
                stage_candidates,
                "stage_id",
                request.GET.get(page_name),
                page_name,
            ).object_list
            data["stages"] = data["stages"] + grouper

        ordered_data = []

        # combining un used groups in to the grouper
        groupers = data["stages"]
        for stage in stages:
            found = False
            for grouper in groupers:
                if grouper["grouper"] == stage:
                    ordered_data.append(grouper)
                    found = True
                    break
            if not found:
                ordered_data.append({"grouper": stage})
        data = {
            "recruitment": rec,
            "stages": ordered_data,
        }
        groups.append(data)
    return groups


@login_required
@hx_request_required
@permission_required(perm="recruitment.add_recruitment")
def recruitment(request):
    """
    This method is used to create recruitment, when create recruitment this method
    add  recruitment view,create candidate, change stage sequence and so on, some of
    the permission is checking manually instead of using django permission permission
    to the  recruitment managers
    """
    form = RecruitmentCreationForm()
    if request.GET:
        form = RecruitmentCreationForm(initial=request.GET.dict())
    dynamic = (
        request.GET.get("dynamic") if request.GET.get("dynamic") != "None" else None
    )
    if request.method == "POST":
        form = RecruitmentCreationForm(request.POST)
        if form.is_valid():
            recruitment_obj = form.save()
            recruitment_obj.recruitment_managers.set(
                Employee.objects.filter(
                    id__in=form.data.getlist("recruitment_managers")
                )
            )
            recruitment_obj.open_positions.set(
                JobPosition.objects.filter(id__in=form.data.getlist("open_positions"))
            )
            if recruitment_obj.publish_in_linkedin:
                for linkedin_account in recruitment_obj.linkedin_accounts.all():
                    post_recruitment_in_linkedin(
                        request, recruitment_obj, linkedin_account
                    )
            for survey in form.cleaned_data["survey_templates"]:
                for sur in survey.recruitmentsurvey_set.all():
                    sur.recruitment_ids.add(recruitment_obj)
            messages.success(request, _("Recruitment added."))
            with contextlib.suppress(Exception):
                managers = recruitment_obj.recruitment_managers.select_related(
                    "employee_user_id"
                )
                users = [employee.employee_user_id for employee in managers]
                notify.send(
                    request.user.employee_get,
                    recipient=users,
                    verb="You are chosen as one of recruitment manager",
                    verb_ar="تم اختيارك كأحد مديري التوظيف",
                    verb_de="Sie wurden als einer der Personalvermittler ausgewählt",
                    verb_es="Has sido elegido/a como uno de los gerentes de contratación",
                    verb_fr="Vous êtes choisi(e) comme l'un des responsables du recrutement",
                    icon="people-circle",
                    redirect=reverse("pipeline"),
                )
            return HorillaRedirect(request)
    return render(
        request, "recruitment/recruitment_form.html", {"form": form, "dynamic": dynamic}
    )


@login_required
@permission_required(perm="recruitment.add_recruitment")
@require_http_methods(["POST"])
def generate_recruitment_description(request):
    """
    AJAX endpoint used by the recruitment create form: given a job title,
    returns an AI-drafted job description (HTML) plus a short list of
    relevant skills, for the recruiter to review before saving. Skill
    names are matched against existing Skill rows case-insensitively -
    only genuinely new names get a new row - so re-generating for similar
    titles doesn't pile up near-duplicate skills.
    """
    title = request.POST.get("title", "").strip()
    if not title:
        return JsonResponse({"error": _("Enter a job title first.")}, status=400)
    try:
        description = generate_job_description(title)
    except DescriptionGenerationError:
        return JsonResponse(
            {
                "error": _(
                    "Couldn't generate a description right now. Please "
                    "try again or write it manually."
                )
            },
            status=502,
        )

    skills_payload = []
    try:
        skill_names = generate_job_skills(title)
    except DescriptionGenerationError:
        # The description is the main deliverable and already succeeded -
        # don't fail the whole request just because the skills half didn't.
        skill_names = []
    for name in skill_names:
        skill, _created = Skill.objects.get_or_create(
            title__iexact=name, defaults={"title": name}
        )
        skills_payload.append({"id": skill.id, "title": skill.title})

    return JsonResponse({"description": description, "skills": skills_payload})


@login_required
@require_http_methods(["POST"])
def sync_recruitment_skills(request):
    """
    AJAX endpoint backing the "Sync Skills" button next to the Skills
    field on the recruitment create/edit form: given the description
    currently in the editor (not necessarily saved yet), returns the
    skills the AI reads out of it, resolved/created the same way
    generate_recruitment_description does. Available on both create and
    edit - unlike "Generate with AI" for the description itself, syncing
    skills from whatever description already exists is meaningful either
    way.
    """
    if not (
        request.user.has_perm("recruitment.add_recruitment")
        or request.user.has_perm("recruitment.change_recruitment")
    ):
        return JsonResponse({"error": _("Permission denied.")}, status=403)

    description = strip_tags(request.POST.get("description", "")).strip()
    if not description:
        return JsonResponse(
            {"error": _("Write a job description first, then sync skills.")},
            status=400,
        )

    try:
        skill_names = generate_skills_from_description(description)
    except DescriptionGenerationError:
        return JsonResponse(
            {
                "error": _(
                    "Couldn't analyze the description right now. Please try again."
                )
            },
            status=502,
        )

    skills_payload = []
    for name in skill_names:
        skill, _created = Skill.objects.get_or_create(
            title__iexact=name, defaults={"title": name}
        )
        skills_payload.append({"id": skill.id, "title": skill.title})

    return JsonResponse({"skills": skills_payload})


@login_required
@permission_required(perm="recruitment.view_recruitment")
def recruitment_view(request):
    """
    This method is used to  render all recruitment to view
    """
    if not request.GET:
        request.GET.copy().update({"is_active": "on"})
    queryset = Recruitment.objects.filter(is_active=True)
    if Recruitment.objects.all():
        template = "recruitment/recruitment_view.html"
    else:
        template = "recruitment/recruitment_empty.html"
    initial_tag = {}
    if request.GET.get("closed") == "false":
        queryset = queryset.filter(closed=True)
        initial_tag["closed"] = ["true"]
    else:
        queryset = queryset.filter(closed=False)
        initial_tag["closed"] = ["false"]

    filter_obj = RecruitmentFilter(request.GET, queryset)
    filter_dict = request.GET.copy()
    for key, value in initial_tag.items():
        filter_dict[key] = value

    return render(
        request,
        template,
        {
            "data": paginator_qry(filter_obj.qs, request.GET.get("page")),
            "f": filter_obj,
            "filter_dict": filter_dict,
            "pd": request.GET.urlencode() + "&closed=false",
        },
    )


@login_required
@permission_required(perm="recruitment.change_recruitment")
@hx_request_required
def recruitment_update(request, rec_id):
    """
    This method is used to update the recruitment, when updating the recruitment,
    any changes in manager is exists then permissions also assigned to the manager
    Args:
        id : recruitment_id
    """
    recruitment_obj = Recruitment.find(rec_id)
    if not recruitment_obj:
        messages.error(
            request, _("The recruitment entry you are trying to edit does not exist.")
        )
        return HorillaRedirect(request)
    survey_template_list = []
    survey_templates = RecruitmentSurvey.objects.filter(
        recruitment_ids=rec_id
    ).distinct()
    for survey in survey_templates:
        survey_template_list.append(survey.template_id.all())
    form = RecruitmentCreationForm(instance=recruitment_obj)
    if request.GET:
        form = RecruitmentCreationForm(request.GET)
    dynamic = (
        request.GET.get("dynamic") if request.GET.get("dynamic") != "None" else None
    )
    if request.method == "POST":
        form = RecruitmentCreationForm(request.POST, instance=recruitment_obj)
        if form.is_valid():
            recruitment_obj = form.save()
            for survey in form.cleaned_data["survey_templates"]:
                for sur in survey.recruitmentsurvey_set.all():
                    sur.recruitment_ids.add(recruitment_obj)
            recruitment_obj.save()
            if len(form.changed_data) > 0:
                if recruitment_obj.publish_in_linkedin:
                    for linkedin_account in recruitment_obj.linkedin_accounts.all():
                        delete_post(recruitment_obj, linkedin_account)
                        post_recruitment_in_linkedin(
                            request, recruitment_obj, linkedin_account
                        )
            messages.success(request, _("Recruitment Updated."))
            response = render(
                request, "recruitment/recruitment_form.html", {"form": form}
            )
            with contextlib.suppress(Exception):
                managers = recruitment_obj.recruitment_managers.select_related(
                    "employee_user_id"
                )
                users = [employee.employee_user_id for employee in managers]
                notify.send(
                    request.user.employee_get,
                    recipient=users,
                    verb=f"{recruitment_obj} is updated, You are chosen as one of the managers",
                    verb_ar=f"{recruitment_obj} تم تحديثه، تم اختيارك كأحد المديرين",
                    verb_de=f"{recruitment_obj} wurde aktualisiert. Sie wurden als\
                            einer der Manager ausgewählt",
                    verb_es=f"{recruitment_obj} ha sido actualizado/a. Has sido elegido\
                            a como uno de los gerentes",
                    verb_fr=f"{recruitment_obj} a été mis(e) à jour. Vous êtes choisi(e) comme\
                            l'un des responsables",
                    icon="people-circle",
                    redirect=reverse("pipeline"),
                )

            return HttpResponse(
                response.content.decode("utf-8") + "<script>location.reload();</script>"
            )
    return render(
        request,
        "recruitment/recruitment_update_form.html",
        {"form": form, "dynamic": dynamic},
    )


def paginator_qry_recruitment_limited(qryset, page_number):
    """
    This method is used to generate common paginator limit.
    """
    paginator = Paginator(qryset, 4)
    qryset = paginator.get_page(page_number)
    return qryset


user_recruitments = {}


@login_required
@manager_can_enter(perm="recruitment.view_recruitment")
def recruitment_pipeline(request):
    """
    This method is used to filter out candidate through pipeline structure
    """
    filter_obj = RecruitmentFilter(
        request.GET,
    )
    if filter_obj.qs.exists():
        template = "pipeline/pipeline.html"
    else:
        template = "pipeline/pipeline_empty.html"
    stage_filter = StageFilter(request.GET)
    candidate_filter = CandidateFilter(request.GET)
    recruitments = paginator_qry_recruitment_limited(
        filter_obj.qs, request.GET.get("page")
    )

    now = timezone.now()

    return render(
        request,
        template,
        {
            "rec_filter_obj": filter_obj,
            "recruitment": recruitments,
            "stage_filter_obj": stage_filter,
            "candidate_filter_obj": candidate_filter,
            "now": now,
        },
    )


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.view_recruitment")
def filter_pipeline(request):
    """
    This method is used to search/filter from pipeline
    """
    filter_obj = RecruitmentFilter(request.GET)
    stage_filter = StageFilter(request.GET)
    candidate_filter = CandidateFilter(request.GET)
    view = request.GET.get("view")
    recruitments = filter_obj.qs.filter(is_active=True)
    if not request.user.has_perm("recruitment.view_recruitment"):
        recruitments = recruitments.filter(
            Q(recruitment_managers=request.user.employee_get)
        )
        stage_recruitment_ids = (
            stage_filter.qs.filter(stage_managers=request.user.employee_get)
            .values_list("recruitment_id", flat=True)
            .distinct()
        )
        recruitments = recruitments | filter_obj.qs.filter(id__in=stage_recruitment_ids)
        recruitments = recruitments.filter(is_active=True).distinct()

    closed = request.GET.get("closed")
    filter_dict = parse_qs(request.GET.urlencode())
    filter_dict = get_key_instances(Recruitment, filter_dict)

    CACHE.set(
        request.session.session_key + "pipeline",
        {
            "candidates": candidate_filter.qs.filter(is_active=True).order_by(
                "sequence"
            ),
            "stages": stage_filter.qs.order_by("sequence"),
            "recruitments": recruitments,
            "filter_dict": filter_dict,
            "filter_query": request.GET,
        },
    )

    previous_data = request.GET.urlencode()
    paginator = Paginator(recruitments, 4)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    template = "pipeline/components/pipeline_search_components.html"
    if request.GET.get("view") == "card":
        template = "pipeline/kanban_components/kanban.html"
    return render(
        request,
        template,
        {
            "recruitment": page_obj,
            "stage_filter_obj": stage_filter,
            "candidate_filter_obj": candidate_filter,
            "filter_dict": filter_dict,
            "status": closed,
            "view": view,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("recruitment.view_recruitment")
def get_stage_badge_count(request):
    """
    Method to update stage badge count
    """
    stage_id = request.GET.get("stage_id")
    stage = Stage.find(stage_id)
    count = stage.candidate_set.filter(is_active=True).count() if stage else 0
    return HttpResponse(count)


@login_required
@manager_can_enter(perm="recruitment.view_recruitment")
def stage_component(request, view: str = "list"):
    """
    This method will stage tab contents
    """
    recruitment_id = request.GET.get("rec_id")
    if not recruitment_id or not (recruitment := Recruitment.find(recruitment_id)):
        return HorillaRedirect(
            request,
            message=(
                _("Recruitment ID missing.")
                if not recruitment_id
                else _("No Recruitment found matching the query.")
            ),
        )

    ordered_stages = CACHE.get(request.session.session_key + "pipeline")[
        "stages"
    ].filter(recruitment_id__id=recruitment_id)
    template = "pipeline/components/stages_tab_content.html"
    if view == "card":
        template = "pipeline/kanban_components/kanban_stage_components.html"
    return render(
        request,
        template,
        {
            "rec": recruitment,
            "ordered_stages": ordered_stages,
            "filter_dict": CACHE.get(request.session.session_key + "pipeline")[
                "filter_dict"
            ],
        },
    )


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def update_candidate_stage_and_sequence(request):
    """Update candidate sequence"""

    order_list = request.GET.getlist("order")
    stage_id = request.GET.get("stage_id")

    pipeline_cache = CACHE.get(request.session.session_key + "pipeline")
    if not pipeline_cache:
        return JsonResponse({"message": _("Pipeline cache expired.")})

    stage = pipeline_cache["stages"].filter(id=stage_id).first()
    if not stage:
        return JsonResponse({"message": _("Stage not found.")})

    context = {}

    for index, cand_id in enumerate(order_list):
        pipeline_cache["candidates"].filter(id=cand_id).update(
            sequence=index, stage_id=stage
        )

    if stage.stage_type == "hired" and stage.recruitment_id.is_vacancy_filled():
        context["message"] = _("Vacancy is filled")
        context["vacancy"] = stage.recruitment_id.vacancy

    return JsonResponse(context)


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def update_candidate_sequence(request):
    """Update candidate sequence"""

    order_list = request.GET.getlist("order")
    stage_id = request.GET.get("stage_id")

    pipeline_cache = CACHE.get(request.session.session_key + "pipeline")
    if not pipeline_cache:
        return JsonResponse({"message": _("Pipeline cache expired.")})

    stage = pipeline_cache["stages"].filter(id=stage_id).first()
    if not stage:
        return JsonResponse({"message": _("Stage not found.")})

    for index, cand_id in enumerate(order_list):
        pipeline_cache["candidates"].filter(id=cand_id).update(
            sequence=index,
            stage_id=stage,
            hired=(stage.stage_type == "hired"),
        )

    return JsonResponse({})


def limited_paginator_qry(queryset, page):
    """
    Limited pagination
    """
    paginator = Paginator(queryset, 10)
    queryset = paginator.get_page(page)
    return queryset


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.view_recruitment")
def candidate_component(request):
    """
    Candidate component
    """
    stage_id = request.GET.get("stage_id")
    stage = (
        CACHE.get(request.session.session_key + "pipeline")["stages"]
        .filter(id=stage_id)
        .first()
    )
    candidates = CACHE.get(request.session.session_key + "pipeline")[
        "candidates"
    ].filter(stage_id=stage)

    template = "pipeline/components/candidate_stage_component.html"
    if request.GET.get("view") == "card":
        template = "pipeline/kanban_components/candidate_kanban_components.html"

    now = timezone.now()
    return render(
        request,
        template,
        {
            "candidates": limited_paginator_qry(
                candidates, request.GET.get("candidate_page")
            ),
            "stage": stage,
            "rec": getattr(candidates.first(), "recruitment_id", {}),
            "now": now,
        },
    )


@login_required
@manager_can_enter("recruitment.change_candidate")
def change_candidate_stage(request):
    """
    This method is used to update candidates stage
    """
    if request.method == "POST":
        canIds = request.POST["canIds"]
        stage_id = request.POST["stageId"]
        context = {}
        if request.GET.get("bulk") == "True":
            canIds = json.loads(canIds)
            for cand_id in canIds:
                try:
                    candidate = Candidate.objects.get(id=cand_id)
                    stage = Stage.objects.filter(
                        recruitment_id=candidate.recruitment_id, id=stage_id
                    ).first()
                    if stage:
                        candidate.stage_id = stage
                        candidate.save()
                        if stage.stage_type == "hired":
                            if stage.recruitment_id.is_vacancy_filled():
                                context["message"] = _("Vacancy is filled")
                                context["vacancy"] = stage.recruitment_id.vacancy
                        messages.success(request, _("Candidate stage updated"))
                except Candidate.DoesNotExist:
                    messages.error(request, _("Candidate not found."))
        else:
            try:
                candidate = Candidate.objects.get(id=canIds)
                stage = Stage.objects.filter(
                    recruitment_id=candidate.recruitment_id, id=stage_id
                ).first()
                if stage:
                    candidate.stage_id = stage
                    candidate.save()
                    if stage.stage_type == "hired":
                        if stage.recruitment_id.is_vacancy_filled():
                            context["message"] = _("Vacancy is filled")
                            context["vacancy"] = stage.recruitment_id.vacancy
                    candidate.stage_id = stage
                    candidate.save()
                    messages.success(request, _("Candidate stage updated"))
            except Candidate.DoesNotExist:
                messages.error(request, _("Candidate not found."))
        return JsonResponse(context)
    stage_id = request.GET.get("stage_id")
    candidate_id = request.GET.get("candidate_id")
    candidate = Candidate.find(candidate_id)
    if not candidate:
        return HorillaRedirect(
            request, message=_("No Candidate found matching the query.")
        )

    stage = Stage.objects.filter(
        recruitment_id=candidate.recruitment_id, id=stage_id
    ).first()
    if stage:
        candidate.stage_id = stage
        candidate.save()
        messages.success(request, _("Candidate stage updated"))
    return stage_component(request)


@login_required
@permission_required(perm="recruitment.view_recruitment")
def recruitment_pipeline_card(request):
    """
    This method is used to render pipeline card structure.
    """
    search = request.GET.get("search")
    search = search if search is not None else ""
    recruitment_obj = Recruitment.objects.all()
    candidates = Candidate.objects.filter(name__icontains=search, is_active=True)
    stages = Stage.objects.all()
    return render(
        request,
        "pipeline/pipeline_components/pipeline_card_view.html",
        {"recruitment": recruitment_obj, "candidates": candidates, "stages": stages},
    )


@login_required
@permission_required(perm="recruitment.delete_recruitment")
def recruitment_archive(request, rec_id):
    """
    This method is used to archive and unarchive the recruitment
    args:
        rec_id: The id of the Recruitment
    """
    try:
        recruitment = Recruitment.objects.get(id=rec_id)
        if recruitment.is_active:
            recruitment.is_active = False
            messages.success(request, _("Recruitment archived successfully."))
        else:
            recruitment.is_active = True
            messages.success(request, _("Recruitment un-archived successfully."))
        recruitment.save()
    except (Recruitment.DoesNotExist, OverflowError):
        messages.error(request, _("Recruitment Does not exists.."))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        return HttpResponse(
            "<script>"
            "$('#applyFilter').click();"
            "$('#reloadMessagesButton').click();"
            "</script>"
        )
    return HorillaRedirect(request)


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_stage")
def stage_update_pipeline(request, stage_id):
    """
    This method is used to update stage from pipeline view
    """
    stage_obj = Stage.objects.get(id=stage_id)
    form = StageCreationForm(instance=stage_obj)
    if request.POST:
        form = StageCreationForm(request.POST, instance=stage_obj)
        if form.is_valid():
            stage_obj = form.save()
            messages.success(request, _("Stage updated."))
            with contextlib.suppress(Exception):
                managers = stage_obj.stage_managers.select_related("employee_user_id")
                users = [employee.employee_user_id for employee in managers]
                notify.send(
                    request.user.employee_get,
                    recipient=users,
                    verb=f"{stage_obj.stage} stage in recruitment {stage_obj.recruitment_id}\
                            is updated, You are chosen as one of the managers",
                    verb_ar=f"تم تحديث مرحلة {stage_obj.stage} في التوظيف {stage_obj.recruitment_id}\
                            ، تم اختيارك كأحد المديرين",
                    verb_de=f"Die Stufe {stage_obj.stage} in der Rekrutierung {stage_obj.recruitment_id}\
                            wurde aktualisiert. Sie wurden als einer der Manager ausgewählt",
                    verb_es=f"Se ha actualizado la etapa {stage_obj.stage} en la contratación\
                          {stage_obj.recruitment_id}.Has sido elegido/a como uno de los gerentes",
                    verb_fr=f"L'étape {stage_obj.stage} dans le recrutement {stage_obj.recruitment_id}\
                          a été mise à jour.Vous avez été choisi(e) comme l'un des responsables",
                    icon="people-circle",
                    redirect=reverse("pipeline"),
                )

            return HorillaRedirect(request)

    return render(request, "pipeline/form/stage_update.html", {"form": form})


@login_required
@hx_request_required
@recruitment_manager_can_enter(perm="recruitment.change_recruitment")
def recruitment_update_pipeline(request, rec_id):
    """
    This method is used to update recruitment from pipeline view
    """
    recruitment_obj = Recruitment.objects.get(id=rec_id)
    form = RecruitmentCreationForm(instance=recruitment_obj)
    if request.POST:
        form = RecruitmentCreationForm(request.POST, instance=recruitment_obj)
        if form.is_valid():
            recruitment_obj = form.save()
            messages.success(request, _("Recruitment updated."))
            with contextlib.suppress(Exception):
                managers = recruitment_obj.recruitment_managers.select_related(
                    "employee_user_id"
                )
                users = [employee.employee_user_id for employee in managers]
                notify.send(
                    request.user.employee_get,
                    recipient=users,
                    verb=f"{recruitment_obj} is updated, You are chosen as one of the managers",
                    verb_ar=f"تم تحديث {recruitment_obj}، تم اختيارك كأحد المديرين",
                    verb_de=f"{recruitment_obj} wurde aktualisiert.\
                          Sie wurden als einer der Manager ausgewählt",
                    verb_es=f"{recruitment_obj} ha sido actualizado/a. Has sido elegido\
                            a como uno de los gerentes",
                    verb_fr=f"{recruitment_obj} a été mis(e) à jour. Vous avez été\
                            choisi(e) comme l'un des responsables",
                    icon="people-circle",
                    redirect=reverse("pipeline"),
                )

            return HorillaRedirect(request)
    return render(request, "pipeline/form/recruitment_update.html", {"form": form})


@login_required
@recruitment_manager_can_enter(perm="recruitment.change_recruitment")
def recruitment_close_pipeline(request, rec_id):
    """
    This method is used to close recruitment from pipeline view
    """
    try:
        recruitment_obj = Recruitment.objects.get(id=rec_id)
        recruitment_obj.closed = True
        recruitment_obj.save()
        messages.success(request, "Recruitment closed successfully")
    except (Recruitment.DoesNotExist, OverflowError):
        messages.error(request, _("Recruitment Does not exists.."))
    return HorillaRedirect(request)


@login_required
@recruitment_manager_can_enter(perm="recruitment.change_recruitment")
def recruitment_reopen_pipeline(request, rec_id):
    """
    This method is used to reopen recruitment from pipeline view
    """
    recruitment_obj = Recruitment.find(rec_id)
    if not recruitment_obj:
        return HorillaRedirect(
            request, message=_("No Recruitment found matching the query.")
        )

    recruitment_obj.closed = False
    recruitment_obj.save()
    messages.success(request, "Recruitment reopend successfully")
    return HorillaRedirect(request)


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def candidate_stage_update(request, cand_id):
    """
    This method is a ajax method used to update candidate stage when drag and drop
    the candidate from one stage to another on the pipeline template
    Args:
        id : candidate_id
    """
    stage_id = request.POST.get("stageId")
    candidate_obj = Candidate.find(cand_id)
    if not candidate_obj:
        return JsonResponse(
            {"type": "error", "message": _("No Candidate found matching the query.")}
        )

    history_queryset = candidate_obj.history_set.all().first()
    stage_obj = Stage.objects.get(id=stage_id)
    if candidate_obj.stage_id == stage_obj:
        return JsonResponse({"type": "noChange", "message": _("No change detected.")})
    # Here set the last updated schedule date on this stage if schedule exists in history
    history_queryset = candidate_obj.history_set.filter(stage_id=stage_obj)
    schedule_date = None
    if history_queryset.exists():
        # this condition is executed when a candidate dropped back to any previous
        # stage, if there any scheduled date then set it back
        schedule_date = history_queryset.first().schedule_date
    stage_manager_on_this_recruitment = (
        is_stagemanager(request)[1]
        .filter(recruitment_id=stage_obj.recruitment_id)
        .exists()
    )
    if (
        stage_manager_on_this_recruitment
        or request.user.is_superuser
        or is_recruitmentmanager(rec_id=stage_obj.recruitment_id.id)[0]
    ):
        candidate_obj.stage_id = stage_obj
        candidate_obj.hired = stage_obj.stage_type == "hired"
        candidate_obj.canceled = stage_obj.stage_type == "cancelled"
        candidate_obj.schedule_date = schedule_date
        candidate_obj.start_onboard = False
        candidate_obj.save()
        with contextlib.suppress(Exception):
            managers = stage_obj.stage_managers.select_related("employee_user_id")
            users = [employee.employee_user_id for employee in managers]
            notify.send(
                request.user.employee_get,
                recipient=users,
                verb=f"New candidate arrived on stage {stage_obj.stage}",
                verb_ar=f"وصل مرشح جديد إلى المرحلة {stage_obj.stage}",
                verb_de=f"Neuer Kandidat ist auf der Stufe {stage_obj.stage} angekommen",
                verb_es=f"Nuevo candidato llegó a la etapa {stage_obj.stage}",
                verb_fr=f"Nouveau candidat arrivé à l'étape {stage_obj.stage}",
                icon="person-add",
                redirect=reverse("pipeline"),
            )

        return JsonResponse(
            {"type": "success", "message": _("Candidate stage updated")}
        )
    return JsonResponse(
        {"type": "danger", "message": _("Something went wrong, Try agian.")}
    )


@login_required
@hx_request_required
@all_manager_can_enter(perm="recruitment.view_stagenote")
def view_note(request, cand_id):
    """
    This method renders a template components to view candidate remark or note
    Args:
        id : candidate instance id
    """
    candidate_obj = Candidate.objects.get(id=cand_id)
    notes = candidate_obj.stagenote_set.all().order_by("id")
    return render(
        request,
        "pipeline/pipeline_components/view_note.html",
        {"cand": candidate_obj, "notes": notes},
    )


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.add_stagenote")
def add_note(request, pk=None):
    """
    This method renders template component to add candidate remark
    """
    form = StageNoteForm(initial={"candidate_id": pk})
    if request.method == "POST":
        form = StageNoteForm(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            note, attachment_ids = form.save(commit=False)
            candidate = Candidate.objects.get(id=pk)
            note.candidate_id = candidate
            note.stage_id = candidate.stage_id
            note.updated_by = request.user.employee_get
            # No more per-message visibility toggle - every message is a
            # normal two-way chat message, visible to the candidate as soon
            # as it's sent (matches candidate_add_notes, which already
            # always sets this for candidate-authored messages).
            note.candidate_can_view = True
            note.seen_by_candidate = note.updated_by is None
            note.save()
            broadcast_new_message(candidate.id)
            # No success toast here - the sent message appearing in the
            # chat itself is the confirmation, like a normal chat app.
    candidate_obj = Candidate.objects.get(id=pk)
    return render(
        request,
        "cbv/candidates/profile_notes_tab.html",
        {
            "candidate": candidate_obj,
            "note_form": form,
        },
    )


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.add_stagenote")
def create_note(request, cand_id=None):
    """
    This method renders template component to add candidate remark
    """
    form = StageNoteForm(initial={"candidate_id": cand_id})
    if request.method == "POST":
        form = StageNoteForm(request.POST, request.FILES)
        if form.is_valid():
            note, attachment_ids = form.save(commit=False)
            candidate = Candidate.objects.get(id=cand_id)
            note.candidate_id = candidate
            note.stage_id = candidate.stage_id
            note.updated_by = request.user.employee_get
            # No more per-message visibility toggle - see add_note's
            # matching comment above.
            note.candidate_can_view = True
            note.seen_by_candidate = note.updated_by is None
            note.save()
            broadcast_new_message(candidate.id)
            # No success toast here - see add_note's matching comment above.
            return redirect("view-note", cand_id=cand_id)
    candidate_obj = Candidate.objects.get(id=cand_id)
    notes = candidate_obj.stagenote_set.all().order_by("id")
    return render(
        request,
        "pipeline/pipeline_components/view_note.html",
        {
            "note_form": form,
            "cand": candidate_obj,
            "notes": notes,
        },
    )


@login_required
@manager_can_enter(perm="recruitment.change_stagenote")
def note_update(request, note_id):
    """
    This method is used to update the stage not
    Args:
        id : stage note instance id
    """
    note = StageNote.find(note_id)
    if not note:
        return HorillaRedirect(
            request, message=_("No Stage Note found matching the query.")
        )

    form = StageNoteUpdateForm(instance=note)
    if request.POST:
        form = StageNoteUpdateForm(request.POST, request.FILES, instance=note)
        if form.is_valid():
            form.save()
            messages.success(request, _("Note updated successfully..."))
            cand_id = note.candidate_id.id
            return redirect("view-note", cand_id=cand_id)

    return render(
        request, "pipeline/pipeline_components/update_note.html", {"form": form}
    )


@login_required
@manager_can_enter(perm="recruitment.change_stagenote")
def note_update_individual(request, note_id):
    """
    This method is used to update the stage not
    Args:
        id : stage note instance id
    """
    note = StageNote.find(note_id)
    if not note:
        return HorillaRedirect(
            request, message=_("No Stage Note found matching the query.")
        )

    form = StageNoteForm(instance=note)
    if request.POST:
        form = StageNoteForm(request.POST, request.FILES, instance=note)
        if form.is_valid():
            form.save()
            messages.success(request, _("Note updated successfully..."))
            return HorillaRedirect(request)
    return render(
        request,
        "pipeline/pipeline_components/update_note_individual.html",
        {
            "form": form,
        },
    )


@login_required
@hx_request_required
def add_more_files(request, id):
    """
    This method is used to Add more files to the stage candidate note.
    Args:
        id : stage note instance id
    """
    note = StageNote.objects.get(id=id)
    if request.method == "POST":
        files = request.FILES.getlist("files")
        files_ids = []
        for file in files:
            instance = StageFiles.objects.create(files=file)
            files_ids.append(instance.id)

            note.stage_files.add(instance.id)
    return redirect("view-note", cand_id=note.candidate_id.id)


@login_required
@hx_request_required
def add_more_individual_files(request, id):
    """
    This method is used to Add more files to the stage candidate note.
    Args:
        id : stage note instance id
    """
    note = StageNote.objects.get(id=id)
    if request.method == "POST":
        files = request.FILES.getlist("files")
        files_ids = []
        for file in files:
            instance = StageFiles.objects.create(files=file)
            files_ids.append(instance.id)
            note.stage_files.add(instance.id)
        messages.success(request, _("Files uploaded successfully"))
    return redirect(f"/recruitment/add-note/{note.candidate_id.id}/")


@login_required
@hx_request_required
def delete_stage_note_file(request, id):
    """
    This method is used to delete the stage note file
    Args:
        id : stage file instance id
    """
    if stage_file := StageFiles.find(id):
        stage_file.delete()
        messages.success(request, _("File deleted successfully"))
    else:
        messages.error(request, _("No Stage Files found matching the query."))

    return HttpResponse("")


@login_required
@hx_request_required
def delete_individual_note_file(request, id):
    """
    This method is used to delete the stage note file
    Args:
        id : stage file instance id
    """
    script = ""
    file = StageFiles.objects.get(id=id)
    cand_id = file.stagenote_set.all().first().candidate_id.id
    file.delete()
    messages.success(request, _("File deleted successfully"))
    return HttpResponse(script)


@candidate_login_required
@hx_request_required
def candidate_notes_mark_read(request, cand_id):
    """
    Marks every chat message currently visible to this candidate as seen -
    called when the candidate opens the Chat tab, so the mobile bottom
    navbar's badge reflects real unread count instead of total messages.
    """
    candidate_session_id = request.session.get("candidate_id")
    is_this_candidate = candidate_session_id == cand_id
    is_authorized_employee = False
    if request.user.is_authenticated:
        employee = Employee.objects.filter(employee_user_id=request.user).first()
        is_authorized_employee = request.user.has_perm(
            "recruitment.view_candidate"
        ) or (
            employee
            and (
                employee.recruitment_set.filter(candidate__id=cand_id).exists()
                or employee.stage_set.filter(candidate=cand_id).exists()
            )
        )
    if not (is_this_candidate or is_authorized_employee):
        return HttpResponse("")
    StageNote.objects.filter(candidate_id=cand_id, seen_by_candidate=False).update(
        seen_by_candidate=True
    )
    # Drain (without displaying) any toast still sitting in the session
    # from an older request - Django's messages framework holds onto a
    # queued message until some template actually renders {% if messages
    # %}, which on this page can otherwise be an unrelated later action
    # (e.g. a task update) that has nothing to do with chat. Opening the
    # Chat tab - the one server round trip a mobile menu switch makes - is
    # a good choke point to make sure nothing chat-related lingers that
    # long.
    list(messages.get_messages(request))
    return HttpResponse("")


@login_required
@permission_required(perm="recruitment.change_candidate")
def candidate_schedule_date_update(request):
    """
    This is a an ajax method to update schedule date for a candidate
    """
    candidate_id = request.POST.get("candidateId")
    schedule_date = request.POST.get("date")
    candidate_obj = Candidate.find(candidate_id)
    message = "Error"
    if candidate_obj:
        candidate_obj.schedule_date = schedule_date
        candidate_obj.save()
        message = "Congratulations"
    return JsonResponse({"message": message})


@login_required
@manager_can_enter(perm="recruitment.add_stage")
def stage(request):
    """
    This method is used to create stages, also several permission assigned to the stage managers
    """
    form = StageCreationForm(
        initial={"recruitment_id": request.GET.get("recruitment_id")}
    )
    if request.method == "POST":
        form = StageCreationForm(request.POST)
        if form.is_valid():
            stage_obj = form.save()
            stage_obj.stage_managers.set(
                Employee.objects.filter(id__in=form.data.getlist("stage_managers"))
            )
            stage_obj.save()
            recruitment_obj = stage_obj.recruitment_id
            rec_stages = (
                Stage.objects.filter(recruitment_id=recruitment_obj, is_active=True)
                .order_by("sequence")
                .last()
            )
            if rec_stages.sequence is None:
                stage_obj.sequence = 1
            else:
                stage_obj.sequence = rec_stages.sequence + 1
            stage_obj.save()
            messages.success(request, _("Stage added."))
            with contextlib.suppress(Exception):
                managers = stage_obj.stage_managers.select_related("employee_user_id")
                users = [employee.employee_user_id for employee in managers]
                notify.send(
                    request.user.employee_get,
                    recipient=users,
                    verb=f"Stage {stage_obj} is updated on recruitment {stage_obj.recruitment_id},\
                          You are chosen as one of the managers",
                    verb_ar=f"تم تحديث المرحلة {stage_obj} في التوظيف\
                          {stage_obj.recruitment_id}، تم اختيارك كأحد المديرين",
                    verb_de=f"Stufe {stage_obj} wurde in der Rekrutierung {stage_obj.recruitment_id}\
                          aktualisiert. Sie wurden als einer der Manager ausgewählt",
                    verb_es=f"La etapa {stage_obj} ha sido actualizada en la contratación\
                          {stage_obj.recruitment_id}. Has sido elegido/a como uno de los gerentes",
                    verb_fr=f"L'étape {stage_obj} a été mise à jour dans le recrutement\
                          {stage_obj.recruitment_id}. Vous avez été choisi(e) comme l'un des responsables",
                    icon="people-circle",
                    redirect=reverse("pipeline"),
                )

            return HorillaRedirect(request)
    return render(request, "stage/stage_form.html", {"form": form})


@login_required
@permission_required(perm="recruitment.view_stage")
def stage_view(request):
    """
    This method is used to render all stages to a template
    """
    stages = Stage.objects.all()
    stages = stages.filter(recruitment_id__is_active=True)
    recruitments = group_by_queryset(
        stages,
        "recruitment_id",
        request.GET.get("rpage"),
    )
    filter_obj = StageFilter()
    form = StageCreationForm()
    if stages.exists():
        template = "stage/stage_view.html"
    else:
        template = "stage/stage_empty.html"
    return render(
        request,
        template,
        {
            "data": paginator_qry(stages, request.GET.get("page")),
            "form": form,
            "f": filter_obj,
            "recruitments": recruitments,
        },
    )


@login_required
@hx_request_required
def stage_data(request, rec_id):
    stages = StageFilter(request.GET).qs.filter(recruitment_id__id=rec_id)
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(Stage, data_dict)

    return render(
        request,
        "stage/stage_component.html",
        {
            "data": paginator_qry(stages, request.GET.get("page")),
            "filter_dict": data_dict,
            "pd": request.GET.urlencode(),
            "hx_target": request.META.get("HTTP_HX_TARGET"),
        },
    )


@login_required
@manager_can_enter(perm="recruitment.change_stage")
@hx_request_required
def stage_update(request, stage_id):
    """
    This method is used to update stage, if the managers changed then\
    permission assigned to new managers also
    Args:
        id : stage_id

    """
    stages = Stage.objects.get(id=stage_id)
    form = StageCreationForm(instance=stages)
    if request.method == "POST":
        form = StageCreationForm(request.POST, instance=stages)
        if form.is_valid():
            form.save()
            messages.success(request, _("Stage updated."))
            response = render(
                request, "recruitment/recruitment_form.html", {"form": form}
            )
            return HttpResponse(
                response.content.decode("utf-8") + "<script>location.reload();</script>"
            )
    return render(request, "stage/stage_update_form.html", {"form": form})


@login_required
@recruitment_manager_can_enter("recruitment.change_stage")
def update_stage_order(request, pk):
    """
    This method is used to update the stage sequence of the onboarding
    """
    recruitment = Recruitment.find(pk)
    if not recruitment:
        return HorillaRedirect(
            request, message=_("No Recruitment found matching the query.")
        )

    if request.method == "POST":
        try:
            order = json.loads(request.POST.get("order", "[]"))
            for index, stage_id in enumerate(order):
                stage = recruitment.stage_set.get(id=stage_id)
                stage.sequence = index + 1
                stage.save()
            messages.success(request, "Sequence Updated Successfully")
            return JsonResponse({"status": "success"})
        except Exception as e:
            messages.error(request, "Error Updating Sequence..")
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    stages = recruitment.stage_set.order_by("sequence")

    return render(
        request,
        "cbv/pipeline/stage_order.html",
        {
            "stages": stages,
            "recruitment": recruitment,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("recruitment.add_candidate")
def add_candidate(request):
    """
    This method is used to add candidate directly to the stage
    """
    form = AddCandidateForm(initial={"stage_id": request.GET.get("stage_id")})
    if request.POST:
        form = AddCandidateForm(
            request.POST,
            request.FILES,
            initial={"stage_id": request.GET.get("stage_id")},
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Candidate Added")
            return HorillaRedirect(request)
    return render(request, "pipeline/form/candidate_form.html", {"form": form})


@login_required
@require_http_methods(["POST"])
@hx_request_required
def stage_title_update(request, stage_id):
    """
    This method is used to update the name of recruitment stage
    """
    stage_obj = Stage.objects.get(id=stage_id)
    stage_obj.stage = request.POST["stage"]
    stage_obj.save()
    message = _("The stage title has been updated successfully")
    return HttpResponse(
        f'<div class="oh-alert-container"><div class="oh-alert oh-alert--animated oh-alert--success">{message}</div></div>'
    )


@login_required
@any_permission_required(
    perms=["recruitment.add_candidate", "onboarding.add_onboardingcandidate"]
)
def candidate(request):
    """
    This method used to create candidate
    """
    form = CandidateCreationForm()
    open_recruitment = Recruitment.objects.filter(closed=False, is_active=True)
    path = "/recruitment/candidate-view"
    template_name = "candidate/candidate_create_form.html"
    if request.META.get("HTTP_HX_REQUEST") == "true":
        template_name = "candidate/candidate_create_modal_form.html"
    if request.method == "POST":
        form = CandidateCreationForm(request.POST, request.FILES)
        if form.is_valid():
            candidate_obj = form.save(commit=False)
            candidate_obj.start_onboard = False
            if candidate_obj.stage_id is None:
                candidate_obj.stage_id = Stage.objects.filter(
                    recruitment_id=candidate_obj.recruitment_id, stage_type="initial"
                ).first()
            # when creating new candidate from onboarding view
            if request.GET.get("onboarding") == "True":
                candidate_obj.hired = True
                path = "/onboarding/candidates-view"
            if form.data.get("job_position_id"):
                candidate_obj.save()
                messages.success(request, _("Candidate added."))
            else:
                messages.error(request, "Job position field is required")
                return render(
                    request,
                    template_name,
                    {"form": form, "open_recruitment": open_recruitment},
                )
            if request.META.get("HTTP_HX_REQUEST") == "true":
                close_modal_script = ""
                if request.GET.get("container") != "true":
                    close_modal_script = (
                        "$('#objectCreateModal').removeClass('oh-modal--show');"
                    )
                container_reload_script = (
                    "$('#candidateMainContainer').html("
                    '\'<div hx-get="/recruitment/nav-candidate/?" hx-trigger="load"></div>\' + '
                    '\'<div class="oh-checkpoint-badge mb-2" id="selectedInstances" data-ids="[]" data-clicked="" style="display: none"></div>\' + '
                    '\'<div class="oh-wrapper" id="listContainer"><div class="animated-background"></div></div>\''
                    ");"
                    "htmx.process($('#candidateMainContainer')[0]);"
                )
                if request.GET.get("container") != "true":
                    container_reload_script = "$('#applyFilter').click();"
                return HttpResponse(
                    "<script>"
                    + close_modal_script
                    + container_reload_script
                    + "$('#reloadMessagesButton').click();"
                    + "</script>"
                )
            return redirect(path)

    return render(
        request,
        template_name,
        {"form": form, "open_recruitment": open_recruitment},
    )


@login_required
@permission_required(perm="recruitment.add_candidate")
def recruitment_stage_get(request, rec_id):
    """
    This method returns all stages as json
    """
    recruitment_obj = Recruitment.find(rec_id)

    if not recruitment_obj:
        return JsonResponse(
            {"error": _("No Recruitment found matching the query.")},
            status=404,
        )

    all_stages = recruitment_obj.stage_set.all()
    all_stage_json = serializers.serialize("json", all_stages)

    return JsonResponse({"stages": all_stage_json})


@login_required
@permission_required(perm="recruitment.view_candidate")
def candidate_view(request):
    """
    This method render all candidate to the template
    """
    view_type = request.GET.get("view")
    previous_data = request.GET.urlencode()
    candidates = Candidate.objects.filter(is_active=True)
    recruitments = Recruitment.objects.filter(closed=False, is_active=True)

    mails = list(Candidate.objects.values_list("email", flat=True))
    # Query the HorillaUser model to check if any email is present
    existing_emails = list(
        HorillaUser.objects.filter(username__in=mails).values_list("email", flat=True)
    )

    filter_obj = CandidateFilter(request.GET, queryset=candidates)
    if Candidate.objects.exists():
        template = "candidate/candidate_view.html"
    else:
        template = "candidate/candidate_empty.html"
    data_dict = parse_qs(previous_data)
    get_key_instances(Candidate, data_dict)

    # Store the candidates in the session
    request.session["filtered_candidates"] = [candidate.id for candidate in candidates]

    return render(
        request,
        template,
        {
            "data": paginator_qry(filter_obj.qs, request.GET.get("page")),
            "pd": previous_data,
            "f": filter_obj,
            "view_type": view_type,
            "filter_dict": data_dict,
            "gp_fields": CandidateReGroup.fields,
            "emp_list": existing_emails,
            "recruitments": recruitments,
        },
    )


@login_required
@hx_request_required
def interview_filter_view(request):
    """
    This method is used to filter Disciplinary Action.
    """

    previous_data = request.GET.urlencode()

    if request.user.has_perm("recruitment.view_interviewschedule"):
        interviews = InterviewSchedule.objects.all().order_by("-interview_date")
    else:
        interviews = InterviewSchedule.objects.filter(
            employee_id=request.user.employee_get.id
        ).order_by("-interview_date")

    if request.GET.get("sortby"):
        interviews = sortby(request, interviews, "sortby")

    dis_filter = InterviewFilter(request.GET, queryset=interviews).qs

    page_number = request.GET.get("page")
    page_obj = paginator_qry(dis_filter, page_number)
    data_dict = parse_qs(previous_data)
    get_key_instances(InterviewSchedule, data_dict)
    now = timezone.now()
    return render(
        request,
        "candidate/interview_list.html",
        {
            "data": page_obj,
            "pd": previous_data,
            "filter_dict": data_dict,
            "now": now,
        },
    )


@login_required
def interview_view(request):
    """
    This method render all interviews to the template
    """
    previous_data = request.GET.urlencode()

    if request.user.has_perm("recruitment.view_interviewschedule"):
        interviews = InterviewSchedule.objects.all().order_by("-interview_date")
    else:
        interviews = InterviewSchedule.objects.filter(
            employee_id=request.user.employee_get.id
        ).order_by("-interview_date")

    form = InterviewFilter(request.GET, queryset=interviews)
    page_number = request.GET.get("page")
    page_obj = paginator_qry(form.qs, page_number)
    previous_data = request.GET.urlencode()
    template = "candidate/interview_view.html"
    now = timezone.now()

    return render(
        request,
        template,
        {
            "data": page_obj,
            "pd": previous_data,
            "f": form,
            "now": now,
        },
    )


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_interviewschedule")
def interview_employee_remove(request, interview_id, employee_id):
    """
    This view is used to remove the employees from the meeting ,
    Args:
        interview_id(int) : primarykey of the interview.
        employee_id(int) : primarykey of the employee
    """
    interview = InterviewSchedule.find(interview_id)
    if not interview:
        return HorillaRedirect(
            request, message=_("No Meeting found matching the query")
        )

    interview.employee_id.remove(employee_id)
    messages.success(request, "Interviewer removed succesfully.")
    interview.save()
    # return redirect(interview_filter_view)
    return HttpResponse("<script> $('#applyFilter').click();</script>")


@login_required
def candidate_export(request):
    """
    This method is used to Export candidate data
    """
    if request.META.get("HTTP_HX_REQUEST"):
        export_column = CandidateExportForm()
        export_filter = CandidateFilter()
        content = {
            "export_filter": export_filter,
            "export_column": export_column,
        }
        return render(request, "candidate/export_filter.html", context=content)
    return export_data(
        request=request,
        model=Candidate,
        filter_class=CandidateFilter,
        form_class=CandidateExportForm,
        file_name="Candidate_export",
    )


@login_required
@permission_required(perm="recruitment.view_candidate")
def candidate_view_list(request):
    """
    This method renders all candidate on candidate_list.html template
    """
    previous_data = request.GET.urlencode()
    candidates = Candidate.objects.all()
    if request.GET.get("is_active") is None:
        candidates = candidates.filter(is_active=True)
    candidates = CandidateFilter(request.GET, queryset=candidates).qs
    return render(
        request,
        "candidate/candidate_list.html",
        {
            "data": paginator_qry(candidates, request.GET.get("page")),
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
@permission_required(perm="recruitment.view_candidate")
def candidate_view_card(request):
    """
    This method renders all candidate on candidate_card.html template
    """
    previous_data = request.GET.urlencode()
    candidates = Candidate.objects.all()
    if request.GET.get("is_active") is None:
        candidates = candidates.filter(is_active=True)
    candidates = CandidateFilter(request.GET, queryset=candidates).qs
    return render(
        request,
        "candidate/candidate_card.html",
        {
            "data": paginator_qry(candidates, request.GET.get("page")),
            "pd": previous_data,
        },
    )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_about_tab(request, pk, **kwargs):
    """
    method for rendering about tab
    """

    candidate_obj = Candidate.find(pk)
    if not candidate_obj:
        messages.error(request, _("Candidate not found"))
        return HorillaRedirect(request)
    from horilla_theme.methods import get_active_company_theme

    return render(
        request,
        "cbv/candidates/profile_about_tab.html",
        {
            "candidate": candidate_obj,
            "theme_obj": get_active_company_theme(request),
        },
    )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_resume_tab(request, pk, **kwargs):
    """
    method for rendering resume tab
    """

    candidate_obj = Candidate.find(pk)
    return render(
        request,
        "cbv/candidates/profile_resume_tab.html",
        {
            "candidate": candidate_obj,
        },
    )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_survey_tab(request, pk, **kwargs):
    """
    method for rendering survey tab
    """

    candidate_obj = Candidate.find(pk)
    survey = RecruitmentSurveyAnswer.objects.filter(candidate_id=pk).first()
    return render(
        request,
        "cbv/candidates/profile_survey_tab.html",
        {
            "candidate": candidate_obj,
            "survey": survey,
        },
    )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_document_request_tab(request, pk, **kwargs):
    """
    method for rendering survey tab
    """

    candidate_obj = Candidate.find(pk)
    documents = candidate_obj.candidatedocument_set.all()
    return render(
        request,
        "candidate/document.html",
        {
            "candidate": candidate_obj,
            "documents": documents,
        },
    )


# @login_required
# @manager_can_enter(perm="recruitment.view_candidate")
# def candidate_notes_tab(request, pk, **kwargs):
#     """
#     method for rendering notes tab
#     """

#     candidate_obj = Candidate.find(pk)
#     return render(
#         request,
#         "candidate/individual_view_note.html",
#         {
#             "candidate": candidate_obj,
#         },
#     )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_history_tab(request, pk, **kwargs):
    """
    method for rendering history tab
    """

    candidate_obj = Candidate.find(pk)
    return render(
        request,
        "candidate/history.html",
        {
            "candidate": candidate_obj,
        },
    )


@login_required
@all_manager_can_enter(perm="recruitment.view_candidate")
def candidate_onboarding_tab(request, pk, **kwargs):
    """
    method for rendering onboarding tab
    """

    candidate_obj = Candidate.find(pk)
    return render(
        request,
        "cbv/candidates/profile_onboarding_tab.html",
        {
            "candidate": candidate_obj,
        },
    )


@login_required
@all_manager_can_enter(perm="recruitment.view_candidate")
def candidate_rating_tab(request, pk, **kwargs):
    """
    method for rendering rating tab
    """

    candidate_obj = Candidate.find(pk)
    return render(
        request,
        "candidate/rating_tab.html",
        {
            "candidate": candidate_obj,
        },
    )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_interview_tab(request, pk, **kwargs):
    """
    method for rendering interview tab
    """

    candidate_obj = Candidate.find(pk)
    return render(
        request,
        "cbv/candidates/profile_interview_tab.html",
        {
            "candidate": candidate_obj,
        },
    )


def scheduled_interview_tab(request, pk, **kwargs):
    """
    method for rendering interview tab
    """
    employee = Employee.objects.get(id=pk)
    interviews = InterviewSchedule.objects.filter(employee_id=employee).order_by(
        "-interview_date"
    )
    return render(
        request,
        "tabs/scheduled_interview.html",
        {"interviews": interviews, "employee": employee},
    )


@login_required
@all_manager_can_enter(perm="recruitment.view_candidate")
def candidate_view_individual(request, cand_id, **kwargs):
    """
    This method is used to view profile of candidate.
    """
    candidate_obj = get_object_or_404(Candidate, pk=cand_id)

    # Candidate.find(cand_id) (the previous version of this call) isn't a
    # real method on Candidate - get_object_or_404 is the equivalent
    # lookup already used elsewhere in this file.
    mails = list(Candidate.objects.values_list("email", flat=True))
    # Query the HorillaUser model to check if any email is present
    existing_emails = list(
        HorillaUser.objects.filter(username__in=mails).values_list("email", flat=True)
    )
    documents = CandidateDocument.objects.filter(candidate_id=cand_id)

    # Retrieve the filtered candidate list from the session (set by the
    # candidate list view) so the Previous/Next Candidate arrows on this
    # page can step through the same filtered set.
    filtered_candidate_ids = request.session.get("filtered_candidates", [])

    # Convert the string to an actual list of integers
    requests_ids = (
        ast.literal_eval(filtered_candidate_ids)
        if isinstance(filtered_candidate_ids, str)
        else filtered_candidate_ids
    )

    next_id = None
    previous_id = None

    for index, req_id in enumerate(requests_ids):
        if req_id == cand_id:
            if index != len(requests_ids) - 1:
                next_id = requests_ids[index + 1]
            if index != 0:
                previous_id = requests_ids[index - 1]
            break

    return render(
        request,
        "candidate/individual.html",
        {
            "candidate": candidate_obj,
            "previous": previous_id,
            "next": next_id,
            "requests_ids": requests_ids,
            "emp_list": existing_emails,
            "documents": documents,
        },
    )


def _query_param_truthy(get_dict, key):
    val = get_dict.get(key)
    if val is None:
        return False
    return str(val).lower() in ("true", "1", "yes")


def _onboarding_container_request(request):
    return request.GET.get("container") == "true" and _query_param_truthy(
        request.GET, "onboarding"
    )


@login_required
@manager_can_enter(
    perms=["recruitment.change_candidate", "onboarding.change_onboardingcandidate"]
)
def candidate_update(request, cand_id, **kwargs):
    """
    Used to update or change the candidate
    Args:
        id : candidate_id
    """
    try:
        candidate_obj = Candidate.objects.get(id=cand_id)
        form = CandidateCreationForm(instance=candidate_obj)
        path = "/recruitment/candidate-view"
        template_name = "candidate/candidate_create_form.html"
        if request.META.get("HTTP_HX_REQUEST") == "true":
            template_name = "candidate/candidate_create_modal_form.html"
        if request.method == "POST":
            form = CandidateCreationForm(
                request.POST, request.FILES, instance=candidate_obj
            )
            if form.is_valid():
                candidate_obj = form.save()
                if candidate_obj.stage_id is None:
                    candidate_obj.stage_id = Stage.objects.filter(
                        recruitment_id=candidate_obj.recruitment_id,
                        stage_type="initial",
                    ).first()
                if candidate_obj.stage_id is not None:
                    if (
                        candidate_obj.stage_id.recruitment_id
                        != candidate_obj.recruitment_id
                    ):
                        candidate_obj.stage_id = (
                            candidate_obj.recruitment_id.stage_set.filter(
                                stage_type="initial"
                            ).first()
                        )
                if _query_param_truthy(request.GET, "onboarding"):
                    candidate_obj.hired = True
                    path = "/onboarding/candidates-view"
                candidate_obj.save()
                messages.success(request, _("Candidate Updated Successfully."))
                if request.META.get("HTTP_HX_REQUEST") == "true":
                    close_modal_script = ""
                    if request.GET.get("container") != "true":
                        close_modal_script = (
                            "$('#objectCreateModal').removeClass('oh-modal--show');"
                        )
                    if request.GET.get("container") == "true":
                        if _query_param_truthy(request.GET, "onboarding"):
                            _onboarding_candidates_url = reverse("candidates-view")
                            container_reload_script = (
                                "if (window.history && history.pushState) { "
                                f"history.pushState({{}}, '', '{_onboarding_candidates_url}'); "
                                "}"
                                "document.body.classList.remove('onboarding-list-hide-toolbar');"
                                "var nav = document.getElementById('onboardingCandidatesNavBar');"
                                "if (nav) { nav.style.display = ''; }"
                                "var tagRow = nav && nav.querySelector('#filterTagContainerSectionNav');"
                                "if (tagRow) { tagRow.innerHTML = ''; }"
                                "var list = document.getElementById('listContainer');"
                                "if (list) { list.innerHTML = '<div class=\"animated-background\"></div>'; }"
                                "setTimeout(function () { "
                                "var b = document.getElementById('applyFilter'); "
                                "if (b) { b.click(); } }, 100);"
                            )
                        else:
                            container_reload_script = (
                                "$('#candidateMainContainer').html("
                                '\'<div hx-get="/recruitment/nav-candidate/?" hx-trigger="load"></div>\' + '
                                '\'<div class="oh-checkpoint-badge mb-2" id="selectedInstances" data-ids="[]" data-clicked="" style="display: none"></div>\' + '
                                '\'<div class="oh-wrapper" id="listContainer"><div class="animated-background"></div></div>\''
                                ");"
                                "htmx.process($('#candidateMainContainer')[0]);"
                            )
                    else:
                        container_reload_script = "$('#applyFilter').click();"
                    return HttpResponse(
                        "<script>"
                        + close_modal_script
                        + container_reload_script
                        + "$('#reloadMessagesButton').click();"
                        + "</script>"
                    )
                return redirect(path)
        onboarding_container_mode = _onboarding_container_request(request)
        return render(
            request,
            template_name,
            {
                "form": form,
                "onboarding_container_mode": onboarding_container_mode,
            },
        )
    except (Candidate.DoesNotExist, OverflowError):
        messages.error(request, _("Candidate Does not exists.."))
    return HorillaRedirect(request)


@transaction.atomic
@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def candidate_conversion(request, cand_id, **kwargs):
    container_request = request.GET.get("container") == "true"
    candidate_obj = Candidate.find(cand_id)

    if not candidate_obj:
        messages.error(request, ("Candidate not found"))
        if container_request:
            return JsonResponse({"message": "Candidate not found"}, status=404)
        return HorillaRedirect(request)

    if candidate_obj.converted_employee_id:
        messages.info(request, "This candidate is already converted to an employee.")
        if container_request:
            return JsonResponse({"message": "Already converted"}, status=200)
        return HorillaRedirect(request)

    user_exists = HorillaUser.objects.filter(username=candidate_obj.email).exists()
    employee_exists = Employee.objects.filter(
        employee_user_id__username=candidate_obj.email
    ).exists()

    if user_exists:
        messages.error(request, ("User instance with this mail already exists"))
    elif not employee_exists:
        try:
            new_employee = Employee(
                employee_first_name=candidate_obj.name,
                email=candidate_obj.email,
                phone=candidate_obj.mobile,
                gender=candidate_obj.gender,
                is_directly_converted=True,
            )
            new_employee.save()

            work_info = new_employee.employee_work_info
            work_info.job_position_id = candidate_obj.job_position_id
            work_info.department_id = candidate_obj.job_position_id.department_id
            work_info.company_id = candidate_obj.recruitment_id.company_id
            work_info.save()

            Document.objects.bulk_create(
                [
                    Document(
                        title=doc.title,
                        employee_id=new_employee,
                        document=doc.document,
                        status=doc.status,
                        reject_reason=doc.reject_reason,
                    )
                    for doc in candidate_obj.candidatedocument_set.all()
                ]
            )

            candidate_obj.converted_employee_id = new_employee
            candidate_obj.save()
            messages.success(
                request,
                _("Candidate has been successfully converted into an employee."),
            )
        except IntegrityError:
            messages.warning(request, "An error occurred while creating employee data.")

    else:
        messages.info(request, "An employee with this email already exists")

    if container_request:
        return JsonResponse({"message": "Success"}, status=200)

    if "HTTP_HX_REQUEST" in request.META:
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})

    return HorillaRedirect(request)


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def delete_profile_image(request, obj_id):
    """
    This method is used to delete the profile image of the candidate
    Args:
        obj_id : candidate instance id
    """
    candidate_obj = Candidate.find(obj_id)
    if not candidate_obj:
        return HorillaRedirect(
            request, message=_("No Candidate found matching the query.")
        )

    try:
        if candidate_obj.profile:
            file_path = candidate_obj.profile.path
            absolute_path = os.path.join(settings.MEDIA_ROOT, file_path)
            os.remove(absolute_path)
            candidate_obj.profile = None
            candidate_obj.save()
            messages.success(request, _("Profile image removed."))
    except Exception as e:
        pass
    return redirect("rec-candidate-update", cand_id=obj_id)


@login_required
@permission_required(perm="recruitment.view_history")
def candidate_history(request, cand_id):
    """
    This method is used to view candidate stage changes
    Args:
        id : candidate_id
    """
    candidate_obj = Candidate.find(cand_id)
    if not candidate_obj:
        return HorillaRedirect(
            request, message=_("No Candidate found matching the query.")
        )

    candidate_history_queryset = candidate_obj.history.all()
    return render(
        request,
        "candidate/candidate_history.html",
        {"history": candidate_history_queryset},
    )


CANDIDATE_PORTAL_LOGIN_MAIL_BODY = """<div style="font-family:'Segoe UI',Arial,sans-serif;background-color:#f4f6f9;padding:40px 20px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,0.08);border:1px solid #eaeaea;">
    <div style="background-color:#e54f38;padding:24px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#ffffff;opacity:0.85;">Recruitment</p>
      <h1 style="margin:0;font-size:21px;line-height:1.3;color:#ffffff;font-weight:600;">Candidate Portal Login Details</h1>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>{{instance.get_full_name}}</strong>,</p>
      <p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">You can track your application status from the candidate portal using the credentials below.</p>
      <div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
        <p style="margin:0 0 10px;font-size:13px;color:#555555;"><strong>Portal Link:</strong> <a href="{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/" target="_blank" style="color:#e54f38;text-decoration:none;">{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/</a></p>
        <p style="margin:0 0 8px;font-size:13px;color:#555555;"><strong>Username (Email):</strong> {{instance.email}}</p>
        <p style="margin:0;font-size:13px;color:#555555;"><strong>Password (Mobile Number):</strong> {{instance.mobile}}</p>
      </div>
      <p style="margin:0;font-size:13px;color:#888888;">For security, please keep these credentials private.</p>
    </div>
    <div style="padding:20px 32px;border-top:1px solid #eeeeee;background:#fafafa;">
      <p style="margin:0 0 4px;font-size:13px;color:#777777;text-align:center;">Regards,<br><strong style="color:#555555;">{{self.get_full_name}}</strong> | {{self.get_department}} | {{self.get_company}}</p>
      <p style="margin:0;font-size:11px;color:#aaaaaa;text-align:center;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
  </div>
</div>"""


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_candidate")
def form_send_mail(request, cand_id=None):
    """
    This method is used to render the bootstrap modal content body form
    """
    candidate_obj = None
    stage_id = None
    if request.GET.get("stage_id"):
        stage_id = eval_validate(request.GET.get("stage_id"))
    if cand_id:
        candidate_obj = Candidate.objects.get(id=cand_id)
    candidates = Candidate.objects.all()
    if stage_id and isinstance(stage_id, int):
        candidates = candidates.filter(stage_id__id=stage_id)
    else:
        stage_id = None

    HorillaMailTemplate.objects.get_or_create(
        title="Candidate Portal Login",
        # Body mirrors the shared branded layout used by the other recruitment
        # lifecycle mail templates (Application Received, Job Opening Closing
        # Reminder, etc. - see recruitment/migrations/0029 and 0038) so every
        # recruitment email looks like it comes from one consistent system.
        defaults={"body": CANDIDATE_PORTAL_LOGIN_MAIL_BODY},
    )
    templates = HorillaMailTemplate.objects.all()
    return render(
        request,
        "pipeline/pipeline_components/send_mail.html",
        {
            "cand": candidate_obj,
            "templates": templates,
            "candidates": candidates,
            "stage_id": stage_id,
            "searchWords": MailTemplateForm().get_template_language(),
        },
    )


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.add_interviewschedule")
def interview_schedule(request, cand_id):
    """
    This method is used to Schedule interview to candidate
    Args:
        cand_id : candidate instance id
    """
    candidate = Candidate.objects.get(id=cand_id)
    candidates = Candidate.objects.filter(id=cand_id)
    template = "pipeline/pipeline_components/schedule_interview.html"
    form = ScheduleInterviewForm(initial={"candidate_id": candidate})
    form.fields["candidate_id"].queryset = candidates
    if request.method == "POST":
        form = ScheduleInterviewForm(request.POST)
        if form.is_valid():
            form.save()
            emp_ids = form.cleaned_data["employee_id"]
            cand_id = form.cleaned_data["candidate_id"]
            interview_date = form.cleaned_data["interview_date"]
            interview_time = form.cleaned_data["interview_time"]
            users = [employee.employee_user_id for employee in emp_ids]
            notify.send(
                request.user.employee_get,
                recipient=users,
                verb=f"You are scheduled as an interviewer for an interview with {cand_id.name} on {interview_date} at {interview_time}.",
                verb_ar=f"أنت مجدول كمقابلة مع {cand_id.name} يوم {interview_date} في توقيت {interview_time}.",
                verb_de=f"Sie sind als Interviewer für ein Interview mit {cand_id.name} am {interview_date} um {interview_time} eingeplant.",
                verb_es=f"Estás programado como entrevistador para una entrevista con {cand_id.name} el {interview_date} a las {interview_time}.",
                verb_fr=f"Vous êtes programmé en tant qu'intervieweur pour un entretien avec {cand_id.name} le {interview_date} à {interview_time}.",
                icon="people-circle",
                redirect=reverse("interview-view"),
            )

            messages.success(request, "Interview Scheduled successfully.")
            return HorillaRedirect(request)
    return render(request, template, {"form": form, "cand_id": cand_id})


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.add_interviewschedule")
def create_interview_schedule(request):
    """
    This method is used to Schedule interview to candidate
    Args:
        cand_id : candidate instance id
    """
    candidates = Candidate.objects.all()
    template = "candidate/interview_form.html"
    form = ScheduleInterviewForm()
    form.fields["candidate_id"].queryset = candidates
    if request.method == "POST":
        form = ScheduleInterviewForm(request.POST)
        if form.is_valid():
            form.save()
            emp_ids = form.cleaned_data["employee_id"]
            cand_id = form.cleaned_data["candidate_id"]
            interview_date = form.cleaned_data["interview_date"]
            interview_time = form.cleaned_data["interview_time"]
            users = [employee.employee_user_id for employee in emp_ids]
            notify.send(
                request.user.employee_get,
                recipient=users,
                verb=f"You are scheduled as an interviewer for an interview with {cand_id.name} on {interview_date} at {interview_time}.",
                verb_ar=f"أنت مجدول كمقابلة مع {cand_id.name} يوم {interview_date} في توقيت {interview_time}.",
                verb_de=f"Sie sind als Interviewer für ein Interview mit {cand_id.name} am {interview_date} um {interview_time} eingeplant.",
                verb_es=f"Estás programado como entrevistador para una entrevista con {cand_id.name} el {interview_date} a las {interview_time}.",
                verb_fr=f"Vous êtes programmé en tant qu'intervieweur pour un entretien avec {cand_id.name} le {interview_date} à {interview_time}.",
                icon="people-circle",
                redirect=reverse("interview-view"),
            )

            messages.success(request, "Interview Scheduled successfully.")
    return render(request, template, {"form": form})


def _cancel_interview_meeting(interview, request):
    """
    Best-effort: cancels the interview's Teams/Google Meet event, if one
    exists, so Outlook/Google send attendees their own "Canceled:" notice -
    mirrors generate_interview_meeting's provider dispatch, but a failure
    here must never block deleting the local InterviewSchedule.

    Pushes one last update with a "cancelled" body right before deleting -
    without it, the delete-triggered notice reuses whatever body was last
    saved ("Scheduled"/"Re-scheduled"), which reads wrong for a
    cancellation. This means attendees get two emails (an update, then the
    provider's own cancellation notice), which is the accepted trade-off
    for the cancellation notice actually saying "Cancelled".
    """
    from django.apps import apps as django_apps

    if django_apps.is_installed("outlook_auth"):
        from outlook_auth.methods import (
            delete_teams_calendar_event,
            get_azure_api,
            update_teams_calendar_event,
        )
        from outlook_auth.models import InterviewOutlookMeetingLink

        link = InterviewOutlookMeetingLink.objects.filter(interview=interview).first()
        if link:
            api = get_azure_api(request)
            if api and api.token:
                meeting = link.meeting
                try:
                    update_teams_calendar_event(
                        api,
                        {
                            "title": meeting.title,
                            "description": _interview_meeting_description(
                                interview, status="cancelled", duration_minutes=meeting.duration
                            ),
                            "start_time": meeting.start_time,
                            "duration": meeting.duration,
                            "attendees": meeting.attendees,
                        },
                        meeting.event_id,
                    )
                    delete_teams_calendar_event(api, meeting.event_id)
                except Exception as e:
                    logger.error(f"Error cancelling Teams Meeting: {e}")

    if django_apps.is_installed("horilla_meet"):
        from horilla_meet.methods import delete_calendar_event, update_calendar_event
        from horilla_meet.models import GoogleCredential, InterviewMeetingLink

        link = InterviewMeetingLink.objects.filter(interview=interview).first()
        if link and GoogleCredential.objects.filter(
            employee_id=request.user.employee_get
        ).exists():
            meeting = link.meeting
            try:
                update_calendar_event(
                    request,
                    {
                        "title": meeting.title,
                        "description": _interview_meeting_description(
                            interview, status="cancelled", duration_minutes=meeting.duration
                        ),
                        "start_time": meeting.start_time,
                        "duration": meeting.duration,
                    },
                    meeting.event_id,
                )
                delete_calendar_event(request, meeting.event_id)
            except Exception as e:
                logger.error(f"Error cancelling Google Meeting: {e}")


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.delete_interviewschedule")
def interview_delete(request, interview_id):
    """
    Deletes an interview schedule. Cancels its meeting first (notifying the
    candidate and panel) unless the interview is already completed or its
    date has passed - a completed/expired interview already happened (or
    was never going to), so there's nothing to notify anyone about.
    Args:
        interview_id: InterviewSchedule instance ID
    """
    try:
        interview = InterviewSchedule.objects.get(id=interview_id)
        is_expired = interview.interview_date < timezone.localdate()
        if not interview.completed and not is_expired:
            _cancel_interview_meeting(interview, request)
        interview.delete()
        messages.success(request, _("Interview deleted successfully."))
    except InterviewSchedule.DoesNotExist:
        messages.error(request, _("Scheduled Interview not found"))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        return HttpResponse(
            "<script>"
            "$('#applyFilter').click();"
            "$('#reloadMessagesButton').click();"
            "</script>"
        )
    return HorillaRedirect(request)


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_interviewschedule")
def interview_edit(request, interview_id):
    """
    This method is used to Edit Schedule interview
    Args:
        interview_id : interview schedule instance id
    """
    interview = InterviewSchedule.objects.get(id=interview_id)
    view = request.GET["view"]
    if view == "true":
        candidates = Candidate.objects.all()
        view = "true"
    else:
        candidates = Candidate.objects.filter(id=interview.candidate_id.id)
        view = "false"
    template = "pipeline/pipeline_components/schedule_interview_update.html"
    form = ScheduleInterviewForm(instance=interview)
    form.fields["candidate_id"].queryset = candidates
    if request.method == "POST":
        form = ScheduleInterviewForm(request.POST, instance=interview)
        if form.is_valid():
            emp_ids = form.cleaned_data["employee_id"]
            cand_id = form.cleaned_data["candidate_id"]
            interview_date = form.cleaned_data["interview_date"]
            interview_time = form.cleaned_data["interview_time"]
            form.save()
            users = [employee.employee_user_id for employee in emp_ids]
            notify.send(
                request.user.employee_get,
                recipient=users,
                verb=f"You are scheduled as an interviewer for an interview with {cand_id.name} on {interview_date} at {interview_time}.",
                verb_ar=f"أنت مجدول كمقابلة مع {cand_id.name} يوم {interview_date} في توقيت {interview_time}.",
                verb_de=f"Sie sind als Interviewer für ein Interview mit {cand_id.name} am {interview_date} um {interview_time} eingeplant.",
                verb_es=f"Estás programado como entrevistador para una entrevista con {cand_id.name} el {interview_date} a las {interview_time}.",
                verb_fr=f"Vous êtes programmé en tant qu'intervieweur pour un entretien avec {cand_id.name} le {interview_date} à {interview_time}.",
                icon="people-circle",
                redirect=reverse("interview-view"),
            )
            messages.success(request, "Interview updated successfully.")
            return HorillaRedirect(request)
    return render(
        request,
        template,
        {
            "form": form,
            "interview_id": interview_id,
            "view": view,
        },
    )


@login_required
def get_interview_managers(request):
    cand_id = request.GET.get("candidate_id")
    form = ScheduleInterviewForm()
    candidate_obj = Candidate.find(cand_id) if cand_id else None
    if candidate_obj:
        recruitment_managers = candidate_obj.recruitment_id.recruitment_managers.all()
        stage_managers = (
            candidate_obj.stage_id.stage_managers.all()
            if candidate_obj.stage_id
            else recruitment_managers.none()
        )
        managers = (recruitment_managers | stage_managers).distinct()
        form.fields["employee_id"].queryset = managers
    else:
        form.fields["employee_id"].queryset = form.fields["employee_id"].queryset.none()
    pk = request.GET.get("pk")
    if pk:
        try:
            interviewer = InterviewSchedule.objects.get(id=pk)
            form.fields["employee_id"].initial = interviewer.employee_id.all()
        except InterviewSchedule.DoesNotExist:
            pass
    return render(
        request,
        "candidate/interview_form.html",
        {"form": form, "CBV": True},
    )


@login_required
def get_managers(request):
    cand_id = request.GET.get("cand_id")
    candidate_obj = Candidate.find(cand_id)
    if not candidate_obj:
        return JsonResponse({"employees": {}})

    stage_obj = Stage.objects.filter(recruitment_id=candidate_obj.recruitment_id.id)

    # Combine the querysets into a single iterable
    all_managers = chain(
        candidate_obj.recruitment_id.recruitment_managers.all(),
        *[stage.stage_managers.all() for stage in stage_obj],
    )

    # Extract unique managers from the combined iterable
    unique_managers = list(set(all_managers))

    # Assuming you have a list of employee objects called 'unique_managers'
    employees_dict = {
        employee.id: employee.get_full_name() for employee in unique_managers
    }
    return JsonResponse({"employees": employees_dict})


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def send_acknowledgement(request):
    """
    This method is used to send acknowledgement mail to the candidate
    """
    candidate_id = request.POST.get("id")
    subject = request.POST.get("subject")
    bdy = request.POST.get("body")
    candidate_ids = request.POST.getlist("candidates")
    candidates = Candidate.objects.filter(id__in=candidate_ids)

    # Recipients who aren't a candidate yet (e.g. an external interviewer) -
    # CC'd on every mail sent below since they have no Candidate record to
    # personalize the template body against.
    extra_emails = []
    for raw_email in request.POST.get("extra_emails", "").split(","):
        raw_email = raw_email.strip()
        if not raw_email:
            continue
        try:
            validate_email(raw_email)
            extra_emails.append(raw_email)
        except DjangoValidationError:
            messages.warning(request, f"Skipped invalid email address: {raw_email}")

    other_attachments = request.FILES.getlist("other_attachments")

    if candidate_id:
        candidate_obj = Candidate.objects.filter(id=candidate_id)
    else:
        candidate_obj = Candidate.objects.none()
    candidates = (candidates | candidate_obj).distinct()

    template_attachment_ids = request.POST.getlist("template_attachments")
    for candidate in candidates:
        attachments = [
            (file.name, file.read(), file.content_type) for file in other_attachments
        ]
        bodys = list(
            HorillaMailTemplate.objects.filter(
                id__in=template_attachment_ids
            ).values_list("body", flat=True)
        )
        for html in bodys:
            # due to not having solid template we first need to pass the context
            template_bdy = template.Template(html)
            context = template.Context(
                {
                    "instance": candidate,
                    "self": request.user.employee_get,
                    "request": request,
                }
            )
            render_bdy = template_bdy.render(context)
            attachments.append(
                (
                    "Document",
                    generate_pdf(render_bdy, {}, path=False, title="Document").content,
                    "application/pdf",
                )
            )

        template_bdy = template.Template(bdy)
        context = template.Context(
            {
                "instance": candidate,
                "self": request.user.employee_get,
                "request": request,
            }
        )
        render_bdy = template_bdy.render(context)
        letterhead_bdy, company, _has_logo = render_letterhead_content(
            render_bdy, tag_label=_("Recruitment"), team_label=_("Talent Acquisition Team"),
            company=getattr(candidate.recruitment_id, "company_id", None),
        )
        to = candidate.email
        email = EmailMultiAlternatives(
            subject=subject,
            body=letterhead_bdy,
            to=[to],
            cc=extra_emails,
        )
        email.attach_alternative(letterhead_bdy, "text/html")

        email.attachments = attachments
        attach_letterhead_images(email, company)
        try:
            email.send()
            messages.success(request, "Mail sent to candidate")
        except Exception as e:
            logger.exception(e)
            messages.error(request, "Something went wrong")
    return HorillaRedirect(request)


@login_required
@manager_can_enter(perm="recruitment.change_candidate")
def candidate_sequence_update(request):
    """
    This method is used to update the sequence of candidate
    """
    sequence_data = json.loads(request.POST.get("sequenceData", "{}"))
    for cand_id, seq in sequence_data.items():
        cand = Candidate.objects.get(id=cand_id)
        cand.sequence = seq
        cand.save()

    return JsonResponse({"message": "Sequence updated", "type": "info"})


@login_required
@recruitment_manager_can_enter(perm="recruitment.change_stage")
def stage_sequence_update(request):
    """
    This method is used to update the sequence of the stages
    """
    sequence_data = json.loads(request.POST.get("sequence", "{}"))
    if not sequence_data:
        return JsonResponse({"type": "error", "message": _("Missing Sequence")})

    for stage_id, seq in sequence_data.items():
        stage = Stage.objects.get(id=stage_id)
        stage.sequence = seq
        stage.save()
    return JsonResponse({"type": "success", "message": "Stage sequence updated"})


@login_required
def candidate_select(request):
    """
    This method is used for select all in candidate
    """
    page_number = request.GET.get("page")

    if page_number == "all":
        employees = Candidate.objects.filter(is_active=True)
    else:
        employees = Candidate.objects.all()

    employee_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def candidate_select_filter(request):
    """
    This method is used to select all filtered candidates
    """
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}
    context = {}

    if page_number == "all":
        candidate_filter = CandidateFilter(filters, queryset=Candidate.objects.all())

        # Get the filtered queryset
        filtered_candidates = candidate_filter.qs

        candidate_ids = [str(cand.id) for cand in filtered_candidates]
        total_count = filtered_candidates.count()

        context = {"employee_ids": candidate_ids, "total_count": total_count}

    return JsonResponse(context)


@login_required
def create_candidate_rating(request, cand_id):
    """
    This method is used to create rating for the candidate
    Args:
        cand_id : candidate instance id
    """
    candidate = Candidate.find(cand_id)
    if candidate:
        employee_id = request.user.employee_get
        rating = request.POST.get("rating")
        CandidateRating.objects.create(
            candidate_id=candidate, rating=rating, employee_id=employee_id
        )
    else:
        messages.error(request, _("No Candidate found matching the query"))

    return redirect(recruitment_pipeline)


# ///////////////////////////////////////////////
# skill zone
# ///////////////////////////////////////////////


@login_required
@manager_can_enter(perm="recruitment.view_skillzone")
def skill_zone_view(request):
    """
    This method is used to show Skill zone view
    """
    candidates = SkillZoneCandFilter(request.GET).qs.filter(is_active=True)
    skill_groups = group_by_queryset(
        candidates,
        "skill_zone_id",
        request.GET.get("page"),
        "page",
    )

    all_zones = []
    for zone in skill_groups:
        all_zones.append(zone["grouper"])

    skill_zone_filtered = SkillZoneFilter(request.GET).qs.filter(is_active=True)
    all_zone_objects = list(skill_zone_filtered)
    unused_skill_zones = list(set(all_zone_objects) - set(all_zones))

    unused_zones = []
    for zone in unused_skill_zones:
        unused_zones.append(
            {
                "grouper": zone,
                "list": [],
                "dynamic_name": "",
            }
        )
    skill_groups = skill_groups.object_list + unused_zones
    skill_groups = paginator_qry(skill_groups, request.GET.get("page"))
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(SkillZone, data_dict)
    if skill_groups.object_list:
        template = "skill_zone/skill_zone_view.html"
    else:
        template = "skill_zone/empty_skill_zone.html"

    context = {
        "pd": previous_data,
        "filter_dict": data_dict,
        "model": SkillZone(),
        "f": SkillZoneCandFilter(),
        "skill_zones": skill_groups,
        "page": request.GET.get("page"),
    }
    return render(request, template, context=context)


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.add_skillzone")
def skill_zone_create(request):
    """
    This method is used to create Skill zone.
    """
    form = SkillZoneCreateForm()
    if request.method == "POST":
        form = SkillZoneCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Skill Zone created successfully."))
            form = SkillZoneCreateForm()

    return render(
        request,
        "skill_zone/skill_zone_form.html",
        {"form": form},
    )


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_skillzone")
def skill_zone_update(request, sz_id):
    """
    This method is used to update Skill zone.
    """
    skill_zone = SkillZone.objects.get(id=sz_id)
    form = SkillZoneCreateForm(instance=skill_zone)
    if request.method == "POST":
        form = SkillZoneCreateForm(request.POST, instance=skill_zone)
        if form.is_valid():
            form.save()
            messages.success(request, _("Skill Zone updated successfully."))
    return render(
        request,
        "skill_zone/skill_zone_form.html",
        {"form": form, "sz_id": sz_id},
    )


@login_required
@manager_can_enter(perm="recruitment.delete_skillzone")
def skill_zone_delete(request, sz_id):
    """
    function used to delete Skill zone.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_id : Skill zone id

    Returns:
    GET : return Skill zone view template
    """
    try:
        skill_zone = SkillZone.find(sz_id)
        if skill_zone:
            skill_zone.delete()
            messages.success(request, _("Skill zone deleted successfully.."))
        else:
            messages.error(request, _("Skill zone not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        response = HttpResponse(status=204)
        response["HX-Trigger"] = "skillZoneContainerReload"
        return response
    return HttpResponse(
        "<script>$('.filterButton')[0].click();reloadMessage();</script>"
    )


@login_required
@manager_can_enter(perm="recruitment.change_skillzone")
def skill_zone_archive(request, sz_id):
    """
    function used to archive or un-archive Skill zone.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_id : Skill zone id

    Returns:
    GET : return Skill zone view template
    """
    skill_zone = SkillZone.find(sz_id)
    if skill_zone:
        is_active = skill_zone.is_active
        if is_active == True:
            skill_zone.is_active = False
            skill_zone_candidates = SkillZoneCandidate.objects.filter(
                skill_zone_id=sz_id
            )
            for i in skill_zone_candidates:
                i.is_active = False
                i.save()
            messages.success(request, _("Skill zone archived successfully.."))
        else:
            skill_zone.is_active = True
            skill_zone_candidates = SkillZoneCandidate.objects.filter(
                skill_zone_id=sz_id
            )
            for i in skill_zone_candidates:
                i.is_active = True
                i.save()
            messages.success(request, _("Skill zone unarchived successfully.."))
        skill_zone.save()
    else:
        messages.error(request, _("Skill zone not found."))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        response = HttpResponse(status=204)
        response["HX-Trigger"] = "skillZoneContainerReload"
        return response
    return redirect(skill_zone_view)


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.view_skillzone")
def skill_zone_filter(request):
    """
    This method is used to filter and show Skill zone view.
    """
    template = "skill_zone/skill_zone_list.html"
    if request.GET.get("view") == "card":
        template = "skill_zone/skill_zone_card.html"

    candidates = SkillZoneCandFilter(request.GET).qs
    skill_zone_filtered = SkillZoneFilter(request.GET).qs
    if request.GET.get("is_active") == "false":
        skill_zone_filtered = SkillZoneFilter(request.GET).qs.filter(is_active=False)
        candidates = SkillZoneCandFilter(request.GET).qs.filter(is_active=False)

    else:
        skill_zone_filtered = SkillZoneFilter(request.GET).qs.filter(is_active=True)
        candidates = SkillZoneCandFilter(request.GET).qs.filter(is_active=True)
    skill_groups = group_by_queryset(
        candidates,
        "skill_zone_id",
        request.GET.get("page"),
        "page",
    )
    all_zones = []
    for zone in skill_groups:
        all_zones.append(zone["grouper"])

    all_zone_objects = list(skill_zone_filtered)
    unused_skill_zones = list(set(all_zone_objects) - set(all_zones))

    unused_zones = []
    for zone in unused_skill_zones:
        unused_zones.append(
            {
                "grouper": zone,
                "list": [],
                "dynamic_name": "",
            }
        )
    skill_groups = skill_groups.object_list + unused_zones
    skill_groups = paginator_qry(skill_groups, request.GET.get("page"))
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(SkillZone, data_dict)
    context = {
        "skill_zones": skill_groups,
        "pd": previous_data,
        "filter_dict": data_dict,
    }
    return render(
        request,
        template,
        context,
    )


@login_required
@manager_can_enter(perm="recruitment.view_skillzonecandidate")
def skill_zone_cand_card_view(request, sz_id):
    """
    This method is used to show Skill zone candidates.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_cand_id : Skill zone id

    Returns:
    GET : return Skill zone candidate view template
    """
    skill_zone = SkillZone.objects.get(id=sz_id)
    template = "skill_zone_cand/skill_zone_cand_view.html"
    sz_candidates = SkillZoneCandidate.objects.filter(
        skill_zone_id=skill_zone, is_active=True
    )
    context = {
        "sz_candidates": paginator_qry(sz_candidates, request.GET.get("page")),
        "pd": request.GET.urlencode(),
        "sz_id": sz_id,
    }
    return render(request, template, context)


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.add_skillzonecandidate")
def skill_zone_candidate_create(request, sz_id):
    """
    This method is used to add candidates to a Skill zone.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_cand_id : Skill zone id

    Returns:
    GET : return Skill zone candidate create template
    """
    skill_zone = SkillZone.objects.get(id=sz_id)
    template = "skill_zone_cand/skill_zone_cand_form.html"
    form = SkillZoneCandidateForm(initial={"skill_zone_id": skill_zone})
    if request.method == "POST":
        form = SkillZoneCandidateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Candidate added successfully."))
            return HorillaRedirect(request)

    return render(request, template, {"form": form, "sz_id": sz_id})


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.change_skillzonecandidate")
def skill_zone_cand_edit(request, sz_cand_id):
    """
    This method is used to edit candidates in a Skill zone.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_cand_id : Skill zone candidate id

    Returns:
    GET : return Skill zone candidate edit template
    """
    skill_zone_cand = SkillZoneCandidate.objects.filter(id=sz_cand_id).first()
    template = "skill_zone_cand/skill_zone_cand_form.html"
    form = SkillZoneCandidateForm(instance=skill_zone_cand)
    if request.method == "POST":
        form = SkillZoneCandidateForm(request.POST, instance=skill_zone_cand)
        if form.is_valid():
            form.save()
            messages.success(request, _("Candidate edited successfully."))
            return HorillaRedirect(request)
    return render(request, template, {"form": form, "sz_cand_id": sz_cand_id})


@login_required
@manager_can_enter(perm="recruitment.delete_skillzonecandidate")
def skill_zone_cand_delete(request, sz_cand_id):
    """
    function used to delete Skill zone candidate.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_cand_id : Skill zone candidate id

    Returns:
    GET : return Skill zone view template
    """

    try:
        SkillZoneCandidate.objects.get(id=sz_cand_id).delete()
        messages.success(request, _("Skill zone deleted successfully.."))
    except SkillZoneCandidate.DoesNotExist:
        messages.error(request, _("Skill zone not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        response = HttpResponse(status=204)
        response["HX-Trigger"] = "skillZoneContainerReload"
        return response
    return redirect(skill_zone_view)


@login_required
@hx_request_required
@manager_can_enter(perm="recruitment.view_skillzonecandidate")
def skill_zone_cand_filter(request):
    """
    This method is used to filter the skill zone candidates
    """
    template = "skill_zone_cand/skill_zone_cand_card.html"
    if request.GET.get("view") == "list":
        template = "skill_zone_cand/skill_zone_cand_list.html"

    candidates = SkillZoneCandidate.objects.all()
    candidates_filter = SkillZoneCandFilter(request.GET, queryset=candidates).qs
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(SkillZoneCandidate, data_dict)
    context = {
        "candidates": paginator_qry(candidates_filter, request.GET.get("page")),
        "pd": previous_data,
        "filter_dict": data_dict,
        "f": SkillZoneCandFilter(),
    }
    return render(
        request,
        template,
        context,
    )


@login_required
@manager_can_enter(perm="recruitment.delete_skillzonecandidate")
def skill_zone_cand_archive(request, sz_cand_id):
    """
    function used to archive or un-archive Skill zone candidate.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_cand_id : Skill zone candidate id

    Returns:
    GET : return Skill zone candidate view template
    """
    try:
        skill_zone_cand = SkillZoneCandidate.objects.get(id=sz_cand_id)
        is_active = skill_zone_cand.is_active
        if is_active == True:
            skill_zone_cand.is_active = False
            messages.success(request, _("Candidate archived successfully.."))

        else:
            skill_zone_cand.is_active = True
            messages.success(request, _("Candidate unarchived successfully.."))

        skill_zone_cand.save()
    except SkillZoneCandidate.DoesNotExist:
        messages.error(request, _("No Candidate found matching the query."))
    return redirect(skill_zone_view)


@login_required
@manager_can_enter(perm="recruitment.delete_skillzonecandidate")
def skill_zone_cand_delete(request, sz_cand_id):
    """
    function used to delete Skill zone candidate.

    Parameters:
    request (HttpRequest): The HTTP request object.
    sz_cand_id : Skill zone candidate id

    Returns:
    GET : return Skill zone view template
    """
    try:
        SkillZoneCandidate.objects.get(id=sz_cand_id).delete()
        messages.success(request, _("Candidate deleted successfully.."))
    except SkillZoneCandidate.DoesNotExist:
        messages.error(request, _("Candidate not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        response = HttpResponse(status=204)
        response["HX-Trigger"] = "skillZoneContainerReload"
        return response
    return redirect(skill_zone_view)


@login_required
@hx_request_required
def to_skill_zone(request, cand_id):
    """
    This method is used to Add candidate into skill zone
    Args:
        cand_id : candidate instance id
    """
    if not (
        request.user.has_perm("recruitment.change_candidate")
        or request.user.has_perm("recruitment.add_skillzonecandidate")
    ):
        messages.info(request, "You dont have permission.")
        return HorillaRedirect(request)

    candidate = Candidate.objects.get(id=cand_id)
    template = "skill_zone_cand/to_skill_zone_form.html"
    form = ToSkillZoneForm(
        initial={
            "candidate_id": candidate,
            "skill_zone_ids": SkillZoneCandidate.objects.filter(
                candidate_id=candidate
            ).values_list("skill_zone_id", flat=True),
        }
    )
    if request.method == "POST":
        form = ToSkillZoneForm(request.POST)
        if form.is_valid():
            skill_zones = form.cleaned_data["skill_zone_ids"]
            for zone in skill_zones:
                if not SkillZoneCandidate.objects.filter(
                    candidate_id=candidate, skill_zone_id=zone
                ).exists():
                    zone_candidate = SkillZoneCandidate()
                    zone_candidate.candidate_id = candidate
                    zone_candidate.skill_zone_id = zone
                    zone_candidate.reason = form.cleaned_data["reason"]
                    zone_candidate.save()
            messages.success(request, "Candidate Added to skill zone successfully")
            return HorillaRedirect(request)
    return render(request, template, {"form": form, "cand_id": cand_id})


@login_required
def update_candidate_rating(request, cand_id):
    """
    This method is used to update the candidate rating
    Args:
        id : candidate rating instance id
    """
    candidate = Candidate.find(cand_id)
    if candidate:
        employee_id = request.user.employee_get
        rating = request.POST.get("rating")
        rate = CandidateRating.objects.get(
            candidate_id=candidate, employee_id=employee_id
        )
        rate.rating = int(rating)
        rate.save()
    else:
        messages.error(request, _("No Candidate found matching the query"))

    return redirect(recruitment_pipeline)


def open_recruitments(request):
    """
    This method is used to render the open recruitment page
    """
    recruitments = Recruitment.default.filter(
        closed=False, is_published=True, is_active=True
    )
    context = {
        "recruitments": recruitments,
    }
    response = render(request, "recruitment/open_recruitments.html", context)
    response["X-Frame-Options"] = "ALLOW-FROM *"

    return response


@login_required
@hx_request_required
def recruitment_details(request, id):
    """
    This method is used to render the recruitment details page
    """
    recruitment = Recruitment.default.get(id=id)
    context = {
        "recruitment": recruitment,
    }
    return render(request, "recruitment/recruitment_details.html", context)


@login_required
@manager_can_enter("recruitment.view_candidate")
def get_mail_log(request, pk):
    """
    This method is used to track mails sent along with the status
    """

    candidate_obj = Candidate.find(pk)
    tracked_mails = EmailLog.objects.filter(to__icontains=candidate_obj.email).order_by(
        "-created_at"
    )
    return render(
        request,
        "candidate/mail_log.html",
        {
            "candidate": candidate_obj,
            "tracked_mails": tracked_mails,
        },
    )


def _sync_candidate_portal_mail_automation(is_on):
    """
    Keeps the "Candidate Portal Login" MailAutomation (the one an admin
    manages through Settings > Recruitment > Mail > Mail Automation, with
    its own editable HorillaMailTemplate) in lockstep with the simple
    Settings > Recruitment > Candidate Self Tracking toggle.

    Looked up by mail_template title rather than a hardcoded pk, since
    that's the stable, admin-visible identifier - it survives the
    automation itself being renamed and doesn't assume any particular
    fixture was loaded.
    """
    from horilla_automations.models import MailAutomation

    automation = MailAutomation.objects.filter(
        model="recruitment.models.Candidate",
        mail_template__title="Candidate Portal Login",
    ).first()
    if automation and automation.is_active != is_on:
        automation.is_active = is_on
        automation.save()


@login_required
@hx_request_required
@permission_required("recruitment.add_recruitmentgeneralsetting")
def candidate_self_tracking(request):
    """
    This method is used to update the recruitment general setting
    """
    selected_company = request.session.get("selected_company")
    company_id = (
        None if not selected_company or selected_company == "all" else selected_company
    )
    settings, created = RecruitmentGeneralSetting.objects.get_or_create(
        company_id_id=company_id
    )
    if request.GET.get("candidate_self_tracking") == "true":
        settings.candidate_self_tracking = True
        message = _("Application Tracking is enabled ")
    else:
        settings.candidate_self_tracking = False
        # Application Tracking is a hard prerequisite for the portal mail
        # (its toggle isn't even shown without it) - turning tracking off
        # also turns the mail off rather than leaving it silently primed.
        settings.send_candidate_portal_mail = False
        message = _("Application Tracking is disabled ")
    settings.save()
    _sync_candidate_portal_mail_automation(
        settings.candidate_self_tracking and settings.send_candidate_portal_mail
    )
    messages.success(request, message)
    return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")


@login_required
@hx_request_required
@permission_required("recruitment.add_recruitmentgeneralsetting")
def candidate_self_tracking_rating_option(request):
    """
    This method is used to enable/disable the selt tracking rating field
    """
    selected_company = request.session.get("selected_company")
    company_id = (
        None if not selected_company or selected_company == "all" else selected_company
    )
    settings, created = RecruitmentGeneralSetting.objects.get_or_create(
        company_id_id=company_id
    )
    if request.GET.get("candidate_self_tracking") == "true":
        settings.show_overall_rating = True
        message = _("Rating visibility is enabled ")
    else:
        settings.show_overall_rating = False
        message = _("Rating visibility is disabled ")
    settings.save()
    messages.success(request, message)
    return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")


@login_required
@hx_request_required
@permission_required("recruitment.add_recruitmentgeneralsetting")
def candidate_portal_mail_setting(request):
    """
    This method is used to enable/disable the candidate portal login mail
    sent to applicants on application submit
    """
    selected_company = request.session.get("selected_company")
    company_id = (
        None if not selected_company or selected_company == "all" else selected_company
    )
    settings, created = RecruitmentGeneralSetting.objects.get_or_create(
        company_id_id=company_id
    )
    if request.GET.get("send_candidate_portal_mail") == "true":
        settings.send_candidate_portal_mail = True
        message = _("Candidate portal mail is enabled ")
    else:
        settings.send_candidate_portal_mail = False
        message = _("Candidate portal mail is disabled ")
    settings.save()
    _sync_candidate_portal_mail_automation(
        settings.candidate_self_tracking and settings.send_candidate_portal_mail
    )
    messages.success(request, message)
    return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")


@login_required
@hx_request_required
@permission_required("recruitment.change_recruitmentgeneralsetting")
def default_meeting_provider(request):
    """
    This method is used to set which platform (Google Meet / Teams) is
    used to create the meeting link when 'Send Meeting Link' is clicked
    on an interview.
    """
    selected_company = request.session.get("selected_company")
    company_id = (
        None if not selected_company or selected_company == "all" else selected_company
    )
    settings, created = RecruitmentGeneralSetting.objects.get_or_create(
        company_id_id=company_id
    )
    provider = request.GET.get("default_meeting_provider")
    if provider in dict(RecruitmentGeneralSetting.meeting_providers):
        settings.default_meeting_provider = provider
        settings.save()
        messages.success(request, _("Default meeting provider updated "))
    return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")


def _interview_meeting_title(interview):
    """
    "{Stage} - {Candidate} at {Company}" - e.g. "Interview - Avantika Thakur
    at Ariel Software Solutions Pvt Ltd".
    """
    candidate = interview.candidate_id
    stage_name = candidate.stage_id.stage if candidate.stage_id else _("Interview")
    company = (
        candidate.recruitment_id.company_id if candidate.recruitment_id else None
    )
    if company:
        return f"{stage_name} - {candidate.name} at {company}"
    return f"{stage_name} - {candidate.name}"


_INTERVIEW_MEETING_STATUS_LABELS = {
    "scheduled": _("Scheduled"),
    "rescheduled": _("Re-scheduled"),
    "cancelled": _("Cancelled"),
}

def _company_address_html(company):
    """
    Formats a Company's address/city/state/zip/country into a readable,
    multi-line block for the offline-interview invite's "Venue" section.
    Skips any part that isn't filled in on the Company record.
    """
    if not company:
        return ""
    address_line = escape(company.address) if company.address else ""
    city_state = ", ".join(
        escape(part) for part in (company.city, company.state) if part
    )
    if company.zip:
        city_state = f"{city_state} - {escape(company.zip)}" if city_state else escape(company.zip)
    country_line = escape(company.country) if company.country else ""
    lines = [line for line in (address_line, city_state, country_line) if line]
    return "<br>".join(lines)


# Shown in the online-interview invite, above the closing "Regards" line -
# not relevant for an offline (in-person) interview, so it's left out there.
_INTERVIEW_GUIDELINES = [
    _(
        "Please ensure you have a stable and reliable internet connection "
        "before joining the interview."
    ),
    _(
        "We recommend joining the interview 5–10 minutes before the "
        "scheduled time to allow time for any technical checks."
    ),
    _(
        "Please use a laptop or desktop computer with a working camera "
        "and microphone, if possible."
    ),
    _(
        "Kindly check that your camera and microphone are functioning "
        "properly before the interview begins."
    ),
    _(
        "Please choose a quiet, well-lit, and distraction-free "
        "environment to ensure a smooth conversation."
    ),
    _(
        "We request that you keep your camera switched on throughout the "
        "interview, unless otherwise instructed by the interviewer."
    ),
]


def _ordinal_suffix(day):
    if 11 <= day % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _format_time_12h(t):
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def _format_interview_when(interview_date, start_time, duration_minutes):
    """
    e.g. "Wednesday, November 15th, from 1:00 PM to 1:45 PM" - avoids
    strftime's %-I/%#I (no-leading-zero hour), which isn't portable
    between the Linux production server and Windows dev machines.
    """
    end_time = (
        datetime.combine(interview_date, start_time) + timedelta(minutes=duration_minutes)
    ).time()
    weekday_month = interview_date.strftime("%A, %B")
    day = interview_date.day
    return (
        f"{weekday_month} {day}{_ordinal_suffix(day)}, from "
        f"{_format_time_12h(start_time)} to {_format_time_12h(end_time)}"
    )


def _interview_meeting_description(interview, status, duration_minutes=60):
    """
    Builds the HTML shown in the Teams/Google Meet invite body - a table of
    the details a panel member (or the candidate, who is also an attendee)
    needs at a glance. `status` is one of "scheduled", "rescheduled" or
    "cancelled" and picks the heading - cancelled is used by
    _cancel_interview_meeting to overwrite the body right before deleting
    the event, so the "Canceled" notice Outlook/Google send on delete
    doesn't show whatever heading ("Scheduled"/"Re-scheduled") happened to
    be last saved.

    Online interviews get an "Interview Guidelines" section above the
    closing signature; offline interviews get a "Venue" row in the details
    table instead (the company's address), since the guidelines
    (camera/mic/internet checks) don't apply to an in-person interview.
    """
    candidate = interview.candidate_id
    is_online = interview.interview_mode != "offline"
    company_obj = candidate.recruitment_id.company_id if candidate.recruitment_id else None
    prefix = _("Online Interview") if is_online else _("Interview")
    heading = f"{prefix} {_INTERVIEW_MEETING_STATUS_LABELS[status]}"
    when = _format_interview_when(
        interview.interview_date, interview.interview_time, duration_minutes
    )
    # get_full_name(), not str(emp) - the latter appends the employee's
    # badge id in parentheses, which has no place in a candidate-facing
    # meeting invite.
    panel = escape(
        ", ".join(emp.get_full_name() for emp in interview.employee_id.all())
    ) or "-"
    rows = [
        (_("Candidate"), escape(candidate.name)),
        (
            _("Job"),
            escape(str(candidate.job_position_id)) if candidate.job_position_id else "-",
        ),
        (_("Interview Date & Time"), when),
        (_("Panel Members"), panel),
    ]
    if not is_online:
        address_html = _company_address_html(company_obj)
        if address_html:
            rows.append((_("Venue"), address_html))
    # Google Calendar/Gmail strip `style` attributes from an invite's
    # description, which is why an inline-CSS table renders as plain,
    # borderless text there (Outlook keeps the CSS fine either way) -
    # legacy HTML attributes (border/bgcolor/cellpadding/color, and the
    # deprecated <font color> tag) survive that sanitizing pass on both
    # providers, so those - not the letterhead's own CSS/CID-image
    # approach, which calendar invites can't carry at all - are what
    # bring in the same blue brand accent here.
    ACCENT = "#0b5fce"
    ACCENT_TINT = "#eef5fd"
    row_html = "".join(
        f'<tr><td bgcolor="{ACCENT_TINT}"><b>{label}</b></td><td>{value}</td></tr>'
        for label, value in rows
    )
    extra_note = f"<p>{escape(interview.description)}</p>" if interview.description else ""
    guidelines_html = ""
    if is_online:
        guidelines_items = "".join(f"<li>{item}</li>" for item in _INTERVIEW_GUIDELINES)
        guidelines_html = (
            f'<p><b><font color="{ACCENT}">{_("Interview Guidelines")}</font></b></p>'
            f"<ul>{guidelines_items}</ul>"
        )
    company = escape(str(company_obj) if company_obj else "")
    return (
        f"<div>"
        f'<h2><font color="{ACCENT}">{heading}</font></h2>'
        f"<p>{_('Greetings,')}</p>"
        f"<p>{_('Please check the interview details below:')}</p>"
        f'<table border="1" cellpadding="8" cellspacing="0" bordercolor="{ACCENT_TINT}">{row_html}</table>'
        f"{extra_note}"
        f"{guidelines_html}"
        f'<p>{_("Warm Regards,")}<br><b><font color="{ACCENT}">{_("Talent Acquisition Team")}</font></b><br>{company}</p>'
        f"</div>"
    )


def generate_interview_meeting(interview, request):
    """
    Creates/updates the interview's meeting link using whichever platform
    (Google Meet / Teams) is configured as the company's default meeting
    provider (RecruitmentGeneralSetting.default_meeting_provider),
    dispatching to the horilla_meet or outlook_auth adapter accordingly -
    mirrors the "app decides, adapter does the work" pattern already used
    for candidate email intake (see CandidateIntakeMailbox).

    Shared by the manual "Send Meeting Link" action (create_interview_meeting
    below) and automatic generation right after an interview is scheduled
    (recruitment.cbv.interview.InterviewForm.form_valid) - callers decide
    how to surface the result dict; this never raises, since a meeting-link
    failure must never be treated as an interview-scheduling failure.

    Always creates/updates the calendar event (so it's added to the
    interviewers'/candidate's calendar and they're notified by mail) even
    for interview.interview_mode == "offline" - only the video meeting
    link (Google Meet conferenceData / Teams onlineMeeting) is skipped for
    those.

    Returns a dict, one of:
      {"success": True, "created": bool}
      {"needs_auth": True, "message"/"redirect": ...}
      {"error": str, "status": int}
    """
    from django.apps import apps as django_apps

    from base.models import IntegrationApps

    selected_company = request.session.get("selected_company")
    company_id = (
        None if not selected_company or selected_company == "all" else selected_company
    )
    setting = RecruitmentGeneralSetting.objects.filter(company_id_id=company_id).first()
    provider = setting.default_meeting_provider if setting else "google_meet"

    title = _interview_meeting_title(interview)
    start_time = datetime.combine(interview.interview_date, interview.interview_time)
    attendees = [employee.get_mail() for employee in interview.employee_id.all()]
    attendees.append(interview.candidate_id.get_email())
    is_online = interview.interview_mode != "offline"

    if provider == "teams":
        if not django_apps.is_installed("outlook_auth"):
            return {
                "error": str(_("Teams integration is not installed.")),
                "status": 400,
            }
        from outlook_auth.methods import get_azure_api
        from outlook_auth.models import InterviewOutlookMeetingLink, OutlookMeeting

        api = get_azure_api(request)
        if not api or not api.token:
            return {
                "needs_auth": True,
                "message": str(
                    _(
                        "Outlook is not connected yet. Configure and "
                        "authenticate the Outlook Mail integration first."
                    )
                ),
            }

        link = InterviewOutlookMeetingLink.objects.filter(interview=interview).first()
        meeting_id = link.meeting.id if link else None
        description = _interview_meeting_description(
            interview,
            status="scheduled" if meeting_id is None else "rescheduled",
            duration_minutes=link.meeting.duration if link else 60,
        )
        try:
            meeting, created = OutlookMeeting.objects.update_or_create(
                id=meeting_id,
                defaults={
                    "employee_id": request.user.employee_get,
                    "title": title,
                    "start_time": start_time,
                    "description": description,
                    "attendees": attendees,
                    "is_online": is_online,
                },
            )
            meeting.refresh_from_db()
            if not link:
                InterviewOutlookMeetingLink.objects.create(
                    interview=interview, meeting=meeting
                )
            return {"success": True, "created": created, "is_online": is_online}
        except Exception as e:
            logger.error(f"Error creating/updating Teams Meeting: {e}")
            return {"error": str(e), "status": 500}

    # default: google_meet
    if not django_apps.is_installed("horilla_meet"):
        return {
            "error": str(_("Google Meet integration is not installed.")),
            "status": 400,
        }
    if not IntegrationApps.objects.filter(
        app_label="horilla_meet", is_enabled=True
    ).exists():
        return {"error": str(_("Google Meet integration is disabled.")), "status": 400}
    from horilla_meet.models import GoogleCredential, GoogleMeeting, InterviewMeetingLink

    if not GoogleCredential.objects.filter(
        employee_id=request.user.employee_get
    ).exists():
        return {"needs_auth": True, "redirect": reverse("authenticate-gmeet")}

    link = InterviewMeetingLink.objects.filter(interview=interview).first()
    meeting_id = link.meeting.id if link else None
    description = _interview_meeting_description(
        interview,
        status="scheduled" if meeting_id is None else "rescheduled",
        duration_minutes=link.meeting.duration if link else 60,
    )
    try:
        meeting, created = GoogleMeeting.objects.update_or_create(
            id=meeting_id,
            defaults={
                "title": title,
                "start_time": start_time,
                "description": description,
                "attendees": attendees,
                "is_online": is_online,
            },
        )
        meeting.refresh_from_db()
        if not link:
            InterviewMeetingLink.objects.create(interview=interview, meeting=meeting)
        return {"success": True, "created": created, "is_online": is_online}
    except Exception as e:
        logger.error(f"Error creating/updating Google Meeting: {e}")
        return {"error": str(e), "status": 500}


@login_required
@permission_required("recruitment.change_interviewschedule")
def create_interview_meeting(request):
    """
    HTTP entry point for the manual "Send Meeting Link" action - see
    generate_interview_meeting for the actual provider logic.
    """
    interview_id = request.GET.get("id")
    try:
        interview = get_object_or_404(InterviewSchedule, id=interview_id)
    except Exception as e:
        logger.error(f"Error fetching interview: {e}")
        return JsonResponse({"error": str(e)}, status=500)

    result = generate_interview_meeting(interview, request)
    if result.get("needs_auth"):
        return JsonResponse(result)
    if "error" in result:
        return JsonResponse({"error": result["error"]}, status=result.get("status", 500))

    messages.success(
        request,
        _("Meeting created successfully")
        if result.get("created")
        else _("Meeting updated successfully"),
    )
    return JsonResponse({"success": "true"})


@login_required
@hx_request_required
@permission_required("recruitment.add_recruitmentgeneralsetting")
def platform_conversion_widget_setting(request):
    """
    This method is used to enable/disable the 'Source Conversion Rate'
    widget on the Recruitment Dashboard
    """
    selected_company = request.session.get("selected_company")
    company_id = (
        None if not selected_company or selected_company == "all" else selected_company
    )
    settings, created = RecruitmentGeneralSetting.objects.get_or_create(
        company_id_id=company_id
    )
    if request.GET.get("show_platform_conversion") == "true":
        settings.show_platform_conversion = True
        message = _("Source Conversion widget is enabled ")
    else:
        settings.show_platform_conversion = False
        message = _("Source Conversion widget is disabled ")
    settings.save()
    messages.success(request, message)
    return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")


def candidate_login(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        mobile = request.POST.get("phone", "").strip()

        backend = CandidateAuthenticationBackend()
        candidate = backend.authenticate(request, username=email, password=mobile)

        if candidate is not None:
            if candidate.canceled:
                return render(
                    request,
                    "candidate/self_login.html",
                    {
                        "error": _(
                            "Your account has been disabled by the administrator."
                        )
                    },
                )
            request.session["candidate_id"] = candidate.id
            request.session["candidate_email"] = candidate.email
            return redirect("candidate-self-status-tracking")
        else:
            return render(
                request, "candidate/self_login.html", {"error": "Invalid credentials"}
            )

    return render(request, "candidate/self_login.html")


def candidate_logout(request):
    """Logs out the candidate by clearing session data."""

    request.session.pop("candidate_id", None)
    request.session.pop("candidate_email", None)
    messages.success(request, "You have been logged out.")
    return redirect("candidate_login")


def _candidate_all_tasks(candidate):
    """
    Onboarding checklist tasks for the "My Task" tab on the self-tracking
    page - only the Employee Checklist step's tasks, since those are the
    only ones the candidate does themself. pre_joining/post_joining tasks
    are done by someone else about the candidate, so they don't belong
    here - a candidate has no self-tracking-portal view of those at all;
    HR can still see them on the internal candidate profile's Task tab.
    """
    return candidate.checklist_tasks.filter(
        phase="employee_checklist"
    ).select_related("template_task_id__template_id").order_by("-id")


def _group_tasks_by_template(tasks):
    """
    Buckets tasks under the Task Group (TaskTemplate) they were assigned
    from, preserving first-seen order - lets the "My Task" tab show one tab
    per group instead of every task in one long scroll. A task whose
    template link was cleared (template_task_id SET_NULL on delete) falls
    into a shared "Other" bucket rather than being dropped.
    """
    groups = []
    groups_by_key = {}
    for task in tasks:
        template = task.template_task_id.template_id if task.template_task_id else None
        key = template.id if template else 0
        group = groups_by_key.get(key)
        if group is None:
            group = {"title": template.title if template else _("Other"), "tasks": []}
            groups_by_key[key] = group
            groups.append(group)
        group["tasks"].append(task)
    return groups


@candidate_login_required
def candidate_self_status_tracking(request):
    """
    This method is accessed by the candidates
    """
    candidate_id = request.session.get("candidate_id")

    if not candidate_id:
        return redirect("candidate-login/")

    candidate = Candidate.objects.get(pk=candidate_id)

    # check_candidate_self_tracking() resolves the setting via
    # request.session["selected_company"], which is only ever populated for
    # internal staff (the company switcher) - a candidate's own session never
    # has it, so that lookup falls through to the company_id__isnull=True row,
    # which doesn't exist here. Resolve by the candidate's own company instead.
    candidate_company = (
        candidate.recruitment_id.company_id if candidate.recruitment_id else None
    )
    setting = RecruitmentGeneralSetting.objects.filter(
        company_id=candidate_company
    ).first() or RecruitmentGeneralSetting.objects.filter(
        company_id__isnull=True
    ).first()
    self_tracking_feature = setting.candidate_self_tracking if setting else False

    if self_tracking_feature:
        interviews = candidate.candidate_interview.annotate(
            is_today=Case(
                When(interview_date=date.today(), then=0),
                default=1,
                output_field=IntegerField(),
            )
        ).order_by("is_today", "-interview_date", "interview_time")
        all_tasks = _candidate_all_tasks(candidate)
        return render(
            request,
            "candidate/candidate_self_tracking.html",
            {
                "candidate": candidate,
                "interviews": interviews,
                "all_tasks": all_tasks,
                "my_task_groups": _group_tasks_by_template(all_tasks),
                "status_choices": CandidateChecklistTask.STATUS_CHOICES,
                # The page pushes ?tab=<tab> into the address bar whenever
                # the candidate switches tabs (see activateMainTab() in the
                # template), purely so a real browser refresh lands back on
                # the tab they were on instead of always defaulting to
                # Scheduled Interviews.
                "default_task_tab": request.GET.get("tab", "schedule-interview"),
            },
        )
    return render(request, "404.html")


@candidate_login_required
def candidate_task_self_update(request, task_id):
    """
    Lets a candidate mark one of their own Employee Checklist tasks Done
    (or reopen it) from the "My Task" tab - only reachable for a task on
    the employee_checklist phase (the only phase with no employee
    assignee, so completing it is the candidate's own job) that actually
    belongs to the logged-in candidate.
    """
    from onboarding.stepper import _maybe_mark_onboarding_complete

    candidate_id = request.session.get("candidate_id")
    if not candidate_id:
        return redirect("candidate-login/")
    candidate = Candidate.objects.get(pk=candidate_id)

    task = CandidateChecklistTask.objects.filter(
        pk=task_id, candidate_id=candidate, phase="employee_checklist"
    ).first()
    if task and request.method == "POST":
        new_status = request.POST.get("status")
        if new_status in dict(CandidateChecklistTask.STATUS_CHOICES):
            if new_status != task.status:
                task.status_updated_at = timezone.now()
            task.status = new_status
            if new_status == "done":
                task.completed_at = timezone.now()
            elif task.completed_at:
                task.completed_at = None
            task.save()
            if new_status == "done":
                _maybe_mark_onboarding_complete(candidate)

    all_tasks = _candidate_all_tasks(candidate)
    return render(
        request,
        "candidate/candidate_self_tracking.html",
        {
            "candidate": candidate,
            "all_tasks": all_tasks,
            "my_task_groups": _group_tasks_by_template(all_tasks),
            "status_choices": CandidateChecklistTask.STATUS_CHOICES,
            "default_task_tab": "my-task",
        },
    )


@login_required
@manager_can_enter("recruitment.add_candidate")
def candidate_self_status_tracking_managers_view(request, cand_id):
    """
    This method is accessed by the candidates
    """
    self_tracking_feature = check_candidate_self_tracking(request)[
        "check_candidate_self_tracking"
    ]
    if self_tracking_feature:
        candidate_id = request.session.get("candidate_id")
        if (
            request.user.has_perm("recruitment.view_candidate")
            or request.user.employee_get.recruitment_set.filter(
                candidate__id=cand_id
            ).exists()
            or request.user.employee_get.stage_set.filter(candidate=cand_id).exists()
        ):
            request.session["candidate_id"] = cand_id
            candidate_id = cand_id

        if not candidate_id:
            return redirect("candidate-login/")

        candidate = Candidate.objects.get(pk=candidate_id)
        interviews = candidate.candidate_interview.annotate(
            is_today=Case(
                When(interview_date=date.today(), then=0),
                default=1,
                output_field=IntegerField(),
            )
        ).order_by("is_today", "-interview_date", "interview_time")

        all_tasks = _candidate_all_tasks(candidate)
        return render(
            request,
            "candidate/candidate_self_tracking.html",
            {
                "candidate": candidate,
                "interviews": interviews,
                "all_tasks": all_tasks,
                "my_task_groups": _group_tasks_by_template(all_tasks),
                "status_choices": CandidateChecklistTask.STATUS_CHOICES,
                # The page pushes ?tab=<tab> into the address bar whenever
                # the candidate switches tabs (see activateMainTab() in the
                # template), purely so a real browser refresh lands back on
                # the tab they were on instead of always defaulting to
                # Scheduled Interviews.
                "default_task_tab": request.GET.get("tab", "schedule-interview"),
            },
        )
    return render(request, "404.html")


@login_required
@hx_request_required
@permission_required("recruitment.add_rejectreason")
def create_reject_reason(request):
    """
    This method is used to create/update the reject reasons
    """
    instance_id = eval_validate(str(request.GET.get("instance_id")))
    instance = None
    if instance_id:
        instance = RejectReason.objects.get(id=instance_id)
    form = RejectReasonForm(instance=instance)
    if request.method == "POST":
        form = RejectReasonForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Reject reason saved")
            return HorillaRedirect(request)
    return render(request, "settings/reject_reason_form.html", {"form": form})


@login_required
@permission_required("recruitment.view_recruitment")
def self_tracking_feature(request):
    """
    Recruitment optional feature for candidate self tracking
    """
    return render(request, "recruitment/settings/settings.html")


@login_required
@hx_request_required
@permission_required("recruitment.delete_rejectreason")
def delete_reject_reason(request):
    """
    This method is used to delete the reject reasons
    """
    id = request.GET.get("id")
    if not (reason := RejectReason.find(id)):
        messages.error(request, _("No Reject Reason found matching the query."))
        return HttpResponse("<script>$('.reload-record').click();</script>")

    is_last = RejectReason.objects.count() == 1
    reason.delete()
    messages.success(request, _(f"{reason.title} is deleted."))
    script = (
        "$('.reload-record').click();"
        if is_last
        else "$('#reloadMessagesButton').click();"
    )
    return HttpResponse(f"<script>{script}</script>")


def extract_text_with_font_info(pdf):
    """
    This method is used to extract text from the pdf and create a list of dictionaries containing details about the extracted text.
    Args:
        pdf (): pdf file to extract text from
    """
    pdf_bytes = pdf.read()
    pdf_doc = io.BytesIO(pdf_bytes)
    doc = fitz.open("pdf", pdf_doc)
    text_info = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            try:
                for line in block["lines"]:
                    for span in line["spans"]:
                        text_info.append(
                            {
                                "text": span["text"],
                                "font_size": span["size"],
                                "capitalization": sum(
                                    1 for c in span["text"] if c.isupper()
                                )
                                / len(span["text"]),
                            }
                        )
            except:
                pass

    return text_info


def rank_text(text_info):
    """
    This method is used to rank the text

    Args:
        text_info: List of dictionary containing the details

    Returns:
        Returns a sorted list
    """
    ranked_text = sorted(
        text_info, key=lambda x: (x["font_size"], x["capitalization"]), reverse=True
    )
    return ranked_text


def dob_matching(dob):
    """
    This method is used to change the date format to YYYY-MM-DD

    Args:
        dob: Date

    Returns:
        Return date in YYYY-MM-DD
    """
    date_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y.%m.%d",
        "%d.%m.%Y",
    ]

    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(dob, fmt)
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return dob


def extract_info(pdf):
    """
    This method creates the contact information dictionary from the provided pdf file
    Args:
        pdf_file: pdf file
    """
    extracted_info = {
        "full_name": "",
        "address": "",
        "country": "",
        "state": "",
        "phone_number": "",
        "dob": "",
        "email_id": "",
        "zip": "",
    }
    if not pdf:
        return extracted_info

    text_info = extract_text_with_font_info(pdf)
    ranked_text = rank_text(text_info)

    phone_pattern = re.compile(r"\b\+?\d{1,2}\s?\d{9,10}\b")
    dob_pattern = re.compile(
        r"\b(?:\d{1,2}|\d{4})[-/.,]\d{1,2}[-/.,](?:\d{1,2}|\d{4})\b"
    )
    email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    zip_code_pattern = re.compile(r"\b\d{5,6}(?:-\d{4})?\b")

    name_candidates = [
        item["text"]
        for item in ranked_text
        if item["font_size"] == max(item["font_size"] for item in ranked_text)
    ]

    if name_candidates:
        extracted_info["full_name"] = " ".join(name_candidates)

    for item in ranked_text:
        text = item["text"]

        if not text:
            continue

        if not extracted_info["phone_number"]:
            phone_match = phone_pattern.search(text)
            if phone_match:
                extracted_info["phone_number"] = phone_match.group()

        if not extracted_info["dob"]:
            dob_match = dob_pattern.search(text)
            if dob_match:
                extracted_info["dob"] = dob_matching(dob_match.group())

        if not extracted_info["zip"]:
            zip_match = zip_code_pattern.search(text)
            if zip_match:
                extracted_info["zip"] = zip_match.group()

        if not extracted_info["email_id"]:
            email_match = email_pattern.search(text)
            if email_match:
                extracted_info["email_id"] = email_match.group()

        if "address" in text.lower() and not extracted_info["address"]:
            extracted_info["address"] = text.replace("Address:", "").strip()

        for item in text.split(" "):
            if item.capitalize() in country_arr:
                extracted_info["country"] = item

        for item in text.split(" "):
            if item.capitalize() in states:
                extracted_info["state"] = item

    return extracted_info


def resume_completion(request):
    """
    This function is returns the data for completing the candidate creation form
    """
    resume_file = request.FILES.get("resume")
    contact_info = extract_info(resume_file)

    return JsonResponse(contact_info)


def check_vaccancy(request):
    """
    check vaccancy of recruitment
    """
    stage_id = request.GET.get("stageId")
    stage = Stage.objects.get(id=stage_id)
    message = _("No message")
    if stage and stage.recruitment_id.is_vacancy_filled():
        message = _("Vacancy is filled")
    return JsonResponse({"message": message})


@login_required
def skills_view(request):
    """
    This function is used to view skills page in settings
    """
    skills = Skill.objects.all()
    return render(request, "settings/skills/skills_view.html", {"skills": skills})


@login_required
def platforms_view(request):
    """
    This function is used to view platforms page in settings
    """
    platforms = Platform.objects.all()
    return render(
        request, "settings/platforms/platforms_view.html", {"platforms": platforms}
    )


@login_required
@hx_request_required
def create_skills(request):
    """
    This method is used to create the skills
    """
    instance_id = eval_validate(str(request.GET.get("instance_id")))
    dynamic = request.GET.get("dynamic")
    hx_vals = request.GET.get("data")
    instance = None
    if instance_id:
        instance = Skill.objects.get(id=instance_id)
    form = SkillsForm(instance=instance)
    if request.method == "POST":
        form = SkillsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Skill created successfully")

            if request.GET.get("dynamic") == "True":

                url = reverse("recruitment-create")
                instance = Skill.objects.all().last()
                mutable_get = request.GET.copy()
                skills = mutable_get.getlist("skills")
                skills.remove("create")
                skills.append(str(instance.id))
                mutable_get["skills"] = skills[-1]
                skills.pop()
                data = mutable_get.urlencode()
                try:
                    for item in skills:
                        data += f"&skills={item}"
                except:
                    pass
                return redirect(f"{url}?{data}")

            return HttpResponse("<script>window.location.reload()</script>")

    context = {
        "form": form,
        "dynamic": dynamic,
        "hx_vals": hx_vals,
    }

    return render(request, "settings/skills/skills_form.html", context=context)


@login_required
@hx_request_required
@permission_required("recruitment.delete_rejectreason")
def delete_skills(request, id=None):
    """
    This method is used to delete the skills
    """
    ids = request.GET.getlist("ids")

    skills = Skill.objects.filter(id__in=ids)
    titles = list(skills.values_list("title", flat=True))

    count_before = skills.count()
    deleted_count, d = skills.delete()

    for title in titles:
        messages.success(request, f"{title} is deleted.")

    if count_before - deleted_count == 0:
        return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")

    return HttpResponse("<script>$('.reload-record').click()</script>")


@login_required
@hx_request_required
@permission_required("recruitment.delete_recruitment")
def delete_platforms(request, id=None):
    """
    This method is used to delete the platforms
    """
    ids = request.GET.getlist("ids")

    platforms = Platform.objects.filter(id__in=ids)
    titles = list(platforms.values_list("title", flat=True))

    count_before = platforms.count()
    deleted_count, d = platforms.delete()

    for title in titles:
        messages.success(request, f"{title} is deleted.")

    if count_before - deleted_count == 0:
        return HttpResponse("<script>$('#reloadMessagesButton').click()</script>")

    return HttpResponse("<script>$('.reload-record').click()</script>")


@login_required
@hx_request_required
@manager_can_enter("recruitment.add_candidate")
def view_bulk_resumes(request):
    """
    This function returns the bulk_resume.html page to the modal
    """
    rec_id = eval_validate(str(request.GET.get("rec_id")))
    resumes = Resume.objects.filter(recruitment_id=rec_id)

    return render(
        request, "pipeline/bulk_resume.html", {"resumes": resumes, "rec_id": rec_id}
    )


@login_required
@hx_request_required
@manager_can_enter("recruitment.add_candidate")
def add_bulk_resumes(request):
    """
    This function is used to create bulk resume
    """
    rec_id = eval_validate(str(request.GET.get("rec_id")))
    recruitment = Recruitment.objects.get(id=rec_id)
    if request.method == "POST":
        files = request.FILES.getlist("files")
        for file in files:
            Resume.objects.create(
                file=file,
                recruitment_id=recruitment,
            )

        url = reverse("view-bulk-resume")
        query_params = f"?rec_id={rec_id}"

        return redirect(f"{url}{query_params}")


@login_required
@hx_request_required
@manager_can_enter("recruitment.add_candidate")
def delete_resume_file(request):
    """
    Used to delete resume
    """
    ids = request.GET.getlist("ids")
    rec_id = request.GET.get("rec_id")
    Resume.objects.filter(id__in=ids).delete()

    url = reverse("view-bulk-resume")
    query_params = f"?rec_id={rec_id}"

    return redirect(f"{url}{query_params}")


def extract_words_from_pdf(pdf_file):
    """
    This method is used to extract the words from the pdf file into a list.
    Args:
        pdf_file: pdf file

    """
    pdf_document = fitz.open(pdf_file.path)

    words = []

    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        page_text = page.get_text()

        page_words = re.findall(r"\b\w+\b", page_text.lower())

        words.extend(page_words)

    pdf_document.close()

    return words


@login_required
@hx_request_required
@manager_can_enter("recruitment.add_candidate")
def matching_resumes(request, rec_id):
    """
    This function returns the matching resume table after sorting the resumes according to their scores

    Args:
        rec_id: Recruitment ID

    """
    recruitment = Recruitment.objects.filter(id=rec_id).first()
    skills = recruitment.skills.values_list("title", flat=True)
    resumes = recruitment.resume.all()
    is_candidate = resumes.filter(is_candidate=True)
    is_candidate_ids = set(is_candidate.values_list("id", flat=True))

    resume_ranks = []
    for resume in resumes:
        words = extract_words_from_pdf(resume.file)
        matching_skills_count = sum(skill.lower() in words for skill in skills)

        item = {"resume": resume, "matching_skills_count": matching_skills_count}
        if not len(words):
            item["image_pdf"] = True

        resume_ranks.append(item)

    candidate_resumes = [
        rank for rank in resume_ranks if rank["resume"].id in is_candidate_ids
    ]
    non_candidate_resumes = [
        rank for rank in resume_ranks if rank["resume"].id not in is_candidate_ids
    ]

    non_candidate_resumes = sorted(
        non_candidate_resumes, key=lambda x: x["matching_skills_count"], reverse=True
    )
    candidate_resumes = sorted(
        candidate_resumes, key=lambda x: x["matching_skills_count"], reverse=True
    )

    ranked_resumes = non_candidate_resumes + candidate_resumes

    return render(
        request,
        "pipeline/matching_resumes.html",
        {
            "matched_resumes": ranked_resumes,
            "rec_id": rec_id,
        },
    )


@login_required
@manager_can_enter("recruitment.add_candidate")
def matching_resume_completion(request):
    """
    This function is returns the data for completing the candidate creation form
    """
    resume_id = request.GET.get("resume_id")
    resume_obj = get_object_or_404(Resume, id=resume_id)
    resume_file = resume_obj.file
    contact_info = extract_info(resume_file)

    return JsonResponse(contact_info)


@login_required
@permission_required("recruitment.view_rejectreason")
def candidate_reject_reasons(request):
    """
    This method is used to view all the reject reasons
    """
    reject_reasons = RejectReason.objects.all()
    return render(
        request, "settings/reject_reasons.html", {"reject_reasons": reject_reasons}
    )


@login_required
def hired_candidate_chart(request):
    """
    function used to show hired candidates in all recruitments.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response labels, data, background_color, border_color.
    """
    labels = []
    data = []
    background_color = []
    border_color = []
    recruitments = Recruitment.objects.filter(closed=False, is_active=True)
    for recruitment in recruitments:
        red = random.randint(0, 255)
        green = random.randint(0, 255)
        blue = random.randint(0, 255)
        background_color.append(f"rgba({red}, {green}, {blue}, 0.2")
        border_color.append(f"rgb({red}, {green}, {blue})")
        labels.append(f"{recruitment}")
        data.append(recruitment.candidate.filter(hired=True).count())
    return JsonResponse(
        {
            "labels": labels,
            "data": data,
            "background_color": background_color,
            "border_color": border_color,
            "message": _("No records available at the moment."),
        },
        safe=False,
    )


@login_required
@hx_request_required
def candidate_document_request(request):
    """
    This function is used to create document requests of an employee in employee requests view.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns: return document_request_create_form template
    """
    candidate_id = (
        request.GET.get("candidate_id") if request.GET.get("candidate_id") else None
    )
    form = CandidateDocumentRequestForm(initial={"candidate_id": candidate_id})
    if request.method == "POST":
        form = CandidateDocumentRequestForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Document request created successfully"))
            return HttpResponse(
                "<script>"
                "$('.oh-modal--show').removeClass('oh-modal--show');"
                "$('#applyFilter').click();"
                "$('#reloadMessagesButton').click();"
                "</script>"
            )

    context = {
        "form": form,
    }
    return render(
        request, "documents/document_request_create_form.html", context=context
    )


@login_required
@hx_request_required
def document_create(request, id):
    """
    This function is used to create documents from employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee

    Returns: return document_tab template
    """
    candidate_id = Candidate.objects.get(id=id)
    form = CandidateDocumentForm(initial={"candidate_id": candidate_id})
    form.fields["candidate_id"].queryset = Candidate.objects.filter(id=id)
    if request.method == "POST":
        form = CandidateDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, _("Document created successfully."))
            return HorillaRedirect(request)

    context = {
        "form": form,
        "candidate_id": candidate_id,
    }
    return render(request, "candidate/document_create_form.html", context=context)


@login_required
def update_document_title(request, id):
    """
    This function is used to create documents from employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns: return document_tab template
    """
    document = get_object_or_404(CandidateDocument, id=id)
    name = request.POST.get("title")
    if request.method == "POST":
        document.title = name
        document.save()

        return JsonResponse(
            {"success": True, "message": "Document title updated successfully"}
        )
    else:
        return JsonResponse(
            {"success": False, "message": "Invalid request"}, status=400
        )


@login_required
@hx_request_required
@manager_can_enter("recruitment.delete_candidatedocument")
def document_delete(request, id):
    """
    Handle the deletion of a document, with permissions and error handling.

    This view function attempts to delete a document specified by its ID.
    If the user does not have the "delete_document" permission, it restricts
    deletion to documents owned by the user. It provides appropriate success
    or error messages based on the outcome. If the document is protected and
    cannot be deleted, it handles the exception and informs the user.
    """
    try:
        document = CandidateDocument.objects.filter(id=id)
        if document:
            document.delete()
            messages.success(
                request,
                _(
                    f"Document request {document.first()} for {document.first().employee_id} deleted successfully"
                ),
            )
        else:
            messages.error(request, _("Document not found"))

    except ProtectedError:
        messages.error(request, _("You cannot delete this document."))

    if "HTTP_HX_TARGET" in request.META and request.META.get(
        "HTTP_HX_TARGET"
    ).startswith("document"):
        clear_messages(request)
        return HttpResponse()
    else:
        return HorillaRedirect(request)


@candidate_login_required
@hx_request_required
def file_upload(request, id):
    """
    This function is used to upload documents of an employee in employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns: return document_form template
    """
    document_item = CandidateDocument.objects.get(id=id)
    form = CandidateDocumentUpdateForm(instance=document_item)
    if request.method == "POST":
        form = CandidateDocumentUpdateForm(
            request.POST, request.FILES, instance=document_item
        )
        if form.is_valid():
            form.save()
            messages.success(request, _("Document uploaded successfully"))
            return HorillaRedirect(request)

    context = {
        "form": form,
        "document": document_item,
    }
    return render(request, "candidate/document_form.html", context=context)


@candidate_login_required
@hx_request_required
def view_file(request, id):
    """
    This function used to view the uploaded document in the modal.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns: return view_file template
    """
    document_obj = CandidateDocument.objects.filter(id=id).first()
    context = {
        "document": document_obj,
    }
    if document_obj.document:
        file_path = document_obj.document.path
        file_extension = os.path.splitext(file_path)[1][1:].lower()

        content_type = get_content_type(file_extension)

        try:
            with open(file_path, "rb") as file:
                file_content = file.read()
        except:
            file_content = None

        context["file_content"] = file_content
        context["file_extension"] = file_extension
        context["content_type"] = content_type

    return render(request, "candidate/view_file.html", context)


@login_required
@hx_request_required
@manager_can_enter("recruitment.change_candidatedocument")
def document_approve(request, id):
    """
    This function used to view the approve uploaded document.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns:
    """
    document_obj = get_object_or_404(CandidateDocument, id=id)
    if document_obj.document:
        document_obj.status = "approved"
        document_obj.save()
        messages.success(request, _("Document request approved"))
    else:
        messages.error(request, _("No document uploaded"))

    return HorillaRedirect(request)


@login_required
@hx_request_required
@manager_can_enter("recruitment.change_candidatedocument")
def document_mark_under_verification(request, id):
    """
    Moves a submitted document into the "Under Verification" state, between
    submission and the final Verified/Rejected decision.
    """
    document_obj = get_object_or_404(CandidateDocument, id=id)
    if document_obj.document:
        document_obj.status = "under_verification"
        document_obj.save()
        messages.success(request, _("Document marked as under verification"))
    else:
        messages.error(request, _("No document uploaded"))

    return HorillaRedirect(request)


@login_required
@hx_request_required
@manager_can_enter("recruitment.change_candidatedocument")
def document_reject(request, id):
    """
    This function used to view the reject uploaded document.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns:
    """
    document_obj = get_object_or_404(CandidateDocument, id=id)
    form = CandidateDocumentRejectForm()
    if document_obj.document:
        if request.method == "POST":
            form = CandidateDocumentRejectForm(request.POST, instance=document_obj)
            if form.is_valid():
                instance = form.save(commit=False)
                document_obj.reject_reason = instance.reject_reason
                document_obj.status = "rejected"
                document_obj.save()
                messages.error(request, _("Document request rejected"))
                notify_candidate_of_document_rejection(document_obj)

                return HorillaRedirect(request)
    else:
        messages.error(request, _("No document uploaded"))
        return HorillaRedirect(request)

    return render(
        request,
        "candidate/reject_form.html",
        {"form": form, "document_obj": document_obj},
    )


DOCUMENT_REJECTION_MAIL_CONTENT = """<p style="margin:0 0 16px;font-size:15px;color:#2d2d2d;">Hi <strong>%(name)s</strong>,</p>
<p style="margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;">Your submitted document could not be accepted:</p>
<div style="margin:24px 0;padding:20px;background:#fff6f4;border-left:4px solid #e54f38;border-radius:8px;">
  <p style="margin:0 0 8px;font-size:14px;color:#2d2d2d;"><strong>%(title)s</strong></p>
  <p style="margin:0;font-size:13px;color:#555555;"><strong>Reason:</strong> %(reason)s</p>
</div>
<p style="margin:0;font-size:13px;color:#888888;">Please resubmit the document at your earliest convenience.</p>"""


def notify_candidate_of_document_rejection(document_obj):
    """
    Emails the candidate that one of their submitted documents was rejected,
    including the reason, so they know to resubmit without HR chasing them.
    """
    candidate = document_obj.candidate_id
    if not candidate or not candidate.email:
        return
    email_backend = ConfiguredEmailBackend()
    display_email_name = email_backend.dynamic_from_email_with_display_name
    try:
        message = build_letterhead_email(
            subject=_("Action needed: %(title)s was not accepted")
            % {"title": document_obj.title},
            content_html=DOCUMENT_REJECTION_MAIL_CONTENT
            % {
                "name": escape(candidate.name),
                "title": escape(document_obj.title),
                "reason": escape(document_obj.reject_reason or _("Not specified")),
            },
            to=candidate.email,
            tag_label=_("Onboarding"),
            team_label=_("Talent Acquisition Team"),
            company=getattr(candidate.recruitment_id, "company_id", None),
            from_email=display_email_name,
            reply_to=[display_email_name],
        )
        message.send()
    except Exception as e:
        logger.error(f"Document rejection mail not sent to {candidate.email}: {e}")


@candidate_login_required
def candidate_add_notes(request, cand_id):
    """
    This method renders template component to add candidate remark
    """

    candidate = Candidate.find(cand_id)
    if not candidate:
        return HorillaRedirect(
            request, message=_("No Candidate found matching the query.")
        )

    updated_by = request.user.employee_get if request.user.is_authenticated else None
    label = (
        request.user.employee_get.get_full_name()
        if request.user.is_authenticated
        else candidate.name
    )

    form = StageNoteForm(initial={"candidate_id": cand_id})
    if request.method == "POST":
        form = StageNoteForm(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            note, attachment_ids = form.save(commit=False)
            note.candidate_id = candidate
            note.stage_id = candidate.stage_id
            note.updated_by = updated_by
            note.candidate_can_view = True
            note.seen_by_candidate = updated_by is None
            note.save()
            broadcast_new_message(candidate.id)
            # No success toast here - see add_note's matching comment above.
            with contextlib.suppress(Exception):
                managers = candidate.recruitment_id.recruitment_managers.all()
                stage_managers = candidate.stage_id.stage_managers.all()

                all_managers = managers | stage_managers
                users = [
                    employee.employee_user_id for employee in all_managers.distinct()
                ]

                notify.send(
                    candidate,
                    label=label,
                    recipient=users,
                    verb=f"{label} has added a note on the candidate {candidate}",
                    verb_ar=f"أضاف {label} ملاحظة حول المرشح {candidate}",
                    verb_de=f"{label} hat dem {candidate} eine Notiz hinzugefügt.",
                    verb_es=f"{label} agregó una nota al {candidate}.",
                    verb_fr=f"{label} a ajouté une note à {candidate}.",
                    icon="people-circle",
                    redirect=reverse(
                        "candidate-view-individual", kwargs={"cand_id": cand_id}
                    ),
                )

    return render(
        request,
        "candidate/candidate_self_tracking.html",
        {
            "candidate": candidate,
            "note_form": form,
        },
    )


@login_required
@hx_request_required
def employee_profile_interview_tab(request):
    employee = request.user.employee_get

    interviews = employee.interviewschedule_set.annotate(
        is_today=Case(
            When(interview_date=date.today(), then=0),
            default=1,
            output_field=IntegerField(),
        )
    ).order_by("is_today", "-interview_date", "interview_time")

    return render(request, "tabs/scheduled_interview.html", {"interviews": interviews})


@login_required
@hx_request_required
@permission_required("recruitment.delete_rejectedcandidate")
def delete_candidate_rejection(request, rej_id):
    """
    This method is used to delete candidate rejection
    """
    try:
        instance = RejectedCandidate.objects.filter(id=rej_id).first()
        if instance:
            instance.delete()
            messages.success(request, "Candidate rejection deleted successfully")
        else:
            messages.error(request, "Candidate rejection not found")
    except Exception as e:
        messages.error(request, "Error occurred while deleting candidate rejection")
    return HorillaRedirect(request)
