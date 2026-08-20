"""
surveys.py

This module is used to write views related to the survey features
"""

import json
import os
import re
from datetime import datetime
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from base.methods import closest_numbers, get_pagination
from horilla.decorators import (
    hx_request_required,
    is_recruitment_manager,
    login_required,
    permission_required,
)
from horilla.http import HorillaRedirect
from recruitment.filters import SurveyFilter
from recruitment.forms import (
    AddQuestionForm,
    ApplicationForm,
    CandidateEducationFormSet,
    CandidateExperienceFormSet,
    QuestionForm,
    SurveyForm,
    SurveyPreviewForm,
    TemplateForm,
)
from recruitment.models import (
    Candidate,
    JobPosition,
    Platform,
    Recruitment,
    RecruitmentSurvey,
    RecruitmentSurveyAnswer,
    Resume,
    Skill,
    Stage,
    SurveyTemplate,
)
from recruitment.pipeline_grouper import group_by_queryset
from recruitment.views.paginator_qry import paginator_qry


@login_required
@is_recruitment_manager(perm="recruitment.add_recruitmentsurvey")
def survey_form(request):
    """
    This method is used to render survey wform
    """
    recruitment_id = request.GET.get("recId")
    recruitment = Recruitment.find(recruitment_id)
    if not recruitment_id or not recruitment:
        message = (
            _("Missing Recruitment ID")
            if not recruitment_id
            else _("No Recruitment found matching the query.")
        )
        return HorillaRedirect(request, message=message)

    form = SurveyForm(recruitment=recruitment).form
    return render(request, "survey/form.html", {"form": form})


@login_required
@is_recruitment_manager(perm="recruitment.add_recruitmentsurvey")
def survey_preview(request, pk=None):
    """
    Used to render survey form to the candidate
    """
    title = request.GET.get("title")
    template = SurveyTemplate.objects.filter(title=title).first()
    if not title or not template:
        message = (
            _("Missing Survey Template Title")
            if not title
            else _("No Survey Template found matching the query.")
        )
        return HorillaRedirect(request, message=message)

    form = SurveyPreviewForm(template=template).form
    preview_template = "survey/survey_preview.html"
    if request.META.get("HTTP_HX_REQUEST") == "true":
        preview_template = "survey/survey_preview_container.html"
    return render(
        request,
        preview_template,
        {"form": form, "template": template},
    )


@login_required
def question_order_update(request):
    if request.method == "POST":
        # Extract data from the request
        question_id = request.POST.get("question_id")
        new_position = int(request.POST.get("new_position"))
        qs = RecruitmentSurvey.objects.get(id=question_id)

        if qs.sequence > new_position:
            new_position = new_position
        if qs.sequence <= new_position:
            new_position = new_position - 1

        old_qs = RecruitmentSurvey.objects.filter(sequence=new_position)
        for i in old_qs:

            i.sequence = new_position + 1
            i.save()
        qs.sequence = int(new_position)
        qs.save()
        return JsonResponse(
            {"success": True, "message": "Question order updated successfully"}
        )

    return JsonResponse({"error": "Invalid request method"}, status=405)


def candidate_survey(request):
    """
    Used to render survey form to the candidate
    """
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB in bytes
    if not request.session.get("candidate"):
        return HorillaRedirect(
            request, message=_("No candidate found matching the query.")
        )
    candidate_json = request.session["candidate"]
    candidate_dict = json.loads(candidate_json)
    rec_id = candidate_dict[0]["fields"]["recruitment_id"]
    job_id = candidate_dict[0]["fields"]["job_position_id"]
    job = JobPosition.objects.get(id=job_id)
    recruitment = Recruitment.objects.get(id=rec_id)
    stage_id = candidate_dict[0]["fields"]["stage_id"]
    created_by = candidate_dict[0]["fields"].get("created_by")
    modified_by = candidate_dict[0]["fields"].get("modified_by")
    candidate_dict[0]["fields"]["recruitment_id"] = recruitment
    candidate_dict[0]["fields"]["job_position_id"] = job
    candidate_dict[0]["fields"]["stage_id"] = Stage.objects.get(id=stage_id)
    platform_id = candidate_dict[0]["fields"].get("platform")
    if platform_id:
        candidate_dict[0]["fields"]["platform"] = Platform.objects.filter(
            id=platform_id
        ).first()
    UserModel = get_user_model()
    if created_by:
        candidate_dict[0]["fields"]["created_by"] = UserModel(id=created_by)
    if modified_by:
        candidate_dict[0]["fields"]["modified_by"] = UserModel(id=modified_by)
    # skills is a many-to-many field - the serialized session data has it as
    # a raw list of Skill pks, which (unlike platform's FK pk) can't be
    # passed to the model constructor at all, not even after resolving it
    # to instances - Django rejects any direct assignment to an m2m field
    # outside of .set(), which in turn only works on a saved instance.
    skill_ids = candidate_dict[0]["fields"].pop("skills", None)
    candidate = Candidate(**candidate_dict[0]["fields"])
    form = SurveyForm(recruitment=recruitment).form
    if request.method == "POST":
        if not Candidate.objects.filter(
            email=candidate.email, recruitment_id=candidate.recruitment_id
        ).exists():
            candidate.save()
            if skill_ids:
                candidate.skills.set(skill_ids)
        else:
            candidate = Candidate.objects.filter(
                email=candidate.email, recruitment_id=candidate.recruitment_id
            ).first()
        answer = (
            RecruitmentSurveyAnswer()
            if candidate.recruitmentsurveyanswer_set.first() is None
            else candidate.recruitmentsurveyanswer_set.first()
        )
        answer.candidate_id = candidate

        # Process the POST data to properly handle multiple values
        answer_data = {}
        for key, value in request.POST.items():
            if key.startswith("multiple_choices_"):
                parts = key.split("_", 2)
                question_text = parts[2]
                selected_choices = request.POST.getlist(key)
                answer_data[question_text] = selected_choices
            elif key.startswith("date_"):
                parts = key.split("_", 1)
                question_text = parts[1]
                selected_choices = request.POST.getlist(key)
                if selected_choices and selected_choices[0] != "":
                    formatted_dates = [
                        datetime.strptime(date, "%Y-%m-%d").strftime("%d-%m-%Y")
                        for date in selected_choices
                    ]
                    answer_data[question_text] = formatted_dates
            else:
                answer_data[key] = [value]
        for key, value in request.FILES.items():
            attachment = request.FILES[key]
            if attachment.size > MAX_FILE_SIZE:
                messages.error(
                    request, _("File size exceeds the limit. Maximum size is 5 MB")
                )
                return render(
                    request,
                    "survey/candidate_survey_form.html",
                    {"form": form, "candidate": candidate},
                )
            attachment_path = f"recruitment_attachment/{attachment.name}"
            attachment_dir = os.path.dirname(default_storage.path(attachment_path))
            if not os.path.exists(attachment_dir):
                os.makedirs(attachment_dir)
            with default_storage.open(attachment_path, "wb+") as destination:
                for chunk in attachment.chunks():
                    destination.write(chunk)
            answer.attachment = attachment_path
            answer_data[key] = [attachment_path]
        answer.answer_json = json.dumps(answer_data)
        answer.save()
        messages.success(request, _("Your answers are submitted."))
        return render(request, "candidate/success.html")
    return render(
        request,
        "survey/candidate_survey_form.html",
        {"form": form, "candidate": candidate},
    )


@login_required
@is_recruitment_manager(perm="recruitment.view_recruitmentsurvey")
def view_question_template(request):
    """
    This method is used to view the question template
    """
    recs = Recruitment.objects.all()
    ids = []
    for i in recs:
        for manager in i.recruitment_managers.all():
            if request.user.employee_get == manager:
                ids.append(i.id)
    if request.user.has_perm("recruitment.view_recruitmentsurvey"):
        questions = RecruitmentSurvey.objects.all()
    else:
        questions = RecruitmentSurvey.objects.filter(recruitment_ids__in=ids)
    templates = group_by_queryset(
        questions.filter(template_id__isnull=False).distinct(),
        "template_id__title",
        page=request.GET.get("template_page"),
        page_name="template_page",
        records_per_page=get_pagination(),
    )
    all_template_object_list = []
    for template in templates:
        all_template_object_list.append(template)

    survey_templates = SurveyTemplate.objects.all()
    all_templates = survey_templates.values_list("title", flat=True)
    used_templates = questions.values_list("template_id__title", flat=True)

    unused_templates = list(set(all_templates) - set(used_templates))
    unused_groups = []
    for template_name in unused_templates:
        unused_groups.append(
            {
                "grouper": template_name,
                "list": [],
                "dynamic_name": "",
            }
        )
    all_template_object_list = all_template_object_list + unused_groups

    templates = paginator_qry(
        all_template_object_list, request.GET.get("template_page")
    )
    survey_templates = paginator_qry(
        survey_templates, request.GET.get("survey_template_page")
    )
    filter_obj = SurveyFilter()
    requests_ids = json.dumps(
        [
            instance.id
            for instance in paginator_qry(
                questions, request.GET.get("page")
            ).object_list
        ]
    )
    return render(
        request,
        "survey/view_question_templates.html",
        {
            "questions": paginator_qry(questions, request.GET.get("page")),
            "templates": templates,
            "survey_templates": survey_templates,
            "f": filter_obj,
            "requests_ids": requests_ids,
        },
    )


@login_required
@hx_request_required
@permission_required(perm="recruitment.change_recruitmentsurvey")
def update_question_template(request, survey_id):
    """
    This view method is used to update question template
    """
    instance = RecruitmentSurvey.objects.get(id=survey_id)
    form = QuestionForm(
        instance=instance,
    )
    if request.method == "POST":
        form = QuestionForm(request.POST, instance=instance)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save()
            instance.template_id.set(form.cleaned_data["template_id"])
            instance.recruitment_ids.set(form.recruitment)
            # instance.job_position_ids.set(form.job_positions)
            messages.success(request, _("New survey question updated."))
            return HorillaRedirect(request)
    return render(request, "survey/template_update_form.html", {"form": form})


@login_required
@hx_request_required
@permission_required(perm="recruitment.add_recruitmentsurvey")
def create_question_template(request):
    """
    This view method is used to create question template
    """
    form = QuestionForm()
    if request.method == "POST":
        form = QuestionForm(request.POST)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save()
            instance.recruitment_ids.set(form.recruitment)
            instance.template_id.set(form.cleaned_data["template_id"])
            # instance.job_position_ids.set(form.job_positions)
            messages.success(request, _("New survey question created."))
            return HorillaRedirect(request)
    return render(request, "survey/template_form.html", {"form": form})


@login_required
@permission_required(perm="recruitment.delete_recruitmentsurvey")
def delete_survey_question(request, survey_id):
    """
    This method is used to delete the survey instance
    """
    try:
        RecruitmentSurvey.objects.get(id=survey_id).delete()
        messages.success(request, _("Question was deleted successfully"))
    except RecruitmentSurvey.DoesNotExist:
        messages.error(request, _("Question not found."))
    except ProtectedError:
        messages.error(request, _("You cannot delete this question"))
    if request.META.get("HTTP_HX_REQUEST") == "true":
        from recruitment.views.search import filter_survey

        return filter_survey(request)
    return redirect(view_question_template)


# Fields whose errors render inline in application_form.html (next to the
# input, via {{ form.<field>.errors }}) - see the loop in application_form()
# below that uses this to avoid also toasting the same error as a message.
FIELDS_WITH_INLINE_ERRORS = {
    "resume",
    "profile",
    "name",
    "email",
    "mobile",
    "dob",
    "gender",
    "address",
    "country",
    "state",
    "zip",
}


def _formset_rows_for_prefill(formset, required_field_names):
    """
    Turns a bound (POST) education/experience formset into plain dicts the
    template can hand to window.formsetAddRow[prefix]() (see
    application_form.html) to rebuild the same rows client-side after a
    validation error elsewhere on the form - the education/experience
    formsets are pure client-side JS row builders (extra=0, no server-
    rendered rows), so without this every entry a candidate typed would be
    silently lost just because e.g. their phone number failed a check.

    A row is only kept if at least one of `required_field_names` is
    filled, so a formset's own empty extra slot doesn't come back as a
    blank row on redisplay.
    """
    rows = []
    for row_form in formset.forms:
        row = {}
        for field_name in row_form.fields:
            value = row_form[field_name].value()
            row[field_name] = bool(value) if field_name == "is_current_job" else (value or "")
        if any(str(row.get(name) or "").strip() for name in required_field_names):
            rows.append(row)
    return rows


def _submitted_skill_names(request):
    """
    request.POST's "skills_input" values are a mix of existing Skill pks
    (numeric, chosen from the dropdown) and free-typed new names
    (non-numeric) - see the skills-resolution loop further down in this
    view. window.addSkillChip() (application_form.html) re-adds a skill by
    its display name regardless of which of those it was, so pks are
    resolved to titles here rather than passing raw values through.
    """
    raw_values = request.POST.getlist("skills_input")[:25]
    ids = [v for v in raw_values if v.isdigit()]
    titles_by_id = dict(
        Skill.objects.filter(id__in=ids).values_list("id", "title")
    )
    names = []
    for value in raw_values:
        if value.isdigit():
            title = titles_by_id.get(int(value))
            if title:
                names.append(title)
        elif value.strip():
            names.append(value.strip())
    return names


def application_form(request):
    """
    This method renders candidate form to create candidate
    """
    recruitment = None
    recruitment_id = request.GET.get("recruitmentId")
    resume_id = request.GET.get("resumeId")
    resume_obj = Resume.objects.filter(id=resume_id).first()

    if request.method == "GET" and not recruitment_id:
        messages.error(request, _("Recruitment ID is missing"))
        return redirect("open-recruitments")

    try:
        recruitment = Recruitment.objects.filter(
            id=recruitment_id, is_published=True
        ).first()  # Only create applications for published recruitments.
        if not recruitment:
            messages.error(request, _("Recruitment not found"))
            return redirect("open-recruitments")
    except (ValueError, OverflowError):
        messages.error(request, _("Invalid Recruitment ID"))
        return redirect("open-recruitments")

    if request.POST:
        if "resume" not in request.FILES and resume_id:
            if resume_obj and resume_obj.file:
                file_content = resume_obj.file.read()
                pdf_file = SimpleUploadedFile(
                    resume_obj.file.name, file_content, content_type="application/pdf"
                )
                request.FILES["resume"] = pdf_file

        form = ApplicationForm(request.POST, request.FILES)
        education_formset = CandidateEducationFormSet(request.POST, prefix="education")
        experience_formset = CandidateExperienceFormSet(
            request.POST, prefix="experience"
        )
        if (
            form.is_valid()
            and education_formset.is_valid()
            and experience_formset.is_valid()
        ):
            candidate_obj = form.save(commit=False)
            utm_source = (
                request.POST.get("utm_source") or request.GET.get("utm_source")
            )
            if utm_source:
                utm_source = utm_source.strip()
            if utm_source:
                platform_obj, _created = Platform.objects.get_or_create(
                    title__iexact=utm_source, defaults={"title": utm_source}
                )
                candidate_obj.platform = platform_obj
            recruitment_obj = candidate_obj.recruitment_id
            stages = recruitment_obj.stage_set.all()
            if stages.filter(stage_type="applied").exists():
                candidate_obj.stage_id = stages.filter(stage_type="applied").first()
            else:
                candidate_obj.stage_id = stages.order_by("sequence").first()
            messages.success(request, _("Application saved."))
            candidate_obj.save()  # 945

            education_formset.instance = candidate_obj
            education_formset.save()
            experience_formset.instance = candidate_obj
            experience_formset.save()

            # Select2 in tags:true mode submits a mix of existing Skill pks
            # (numeric, chosen from the dropdown) and brand-new free-typed
            # names (non-numeric, added client-side with no server round
            # trip) under the same "skills_input" key - each value here is
            # resolved to a real Skill row either way. The client already
            # blocks nonsense entries (e.g. "[]", "&") and caps the count,
            # but that's only a UX nicety - this mirrors both checks
            # server-side since the request can be forged.
            skill_name_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+#/&-]{0,49}$")
            skill_objs = []
            for raw_value in request.POST.getlist("skills_input")[:25]:
                value = raw_value.strip()
                if not value:
                    continue
                skill_obj = None
                if value.isdigit():
                    skill_obj = Skill.objects.filter(id=value).first()
                if skill_obj is None:
                    if not skill_name_pattern.match(value):
                        continue
                    skill_obj, _created = Skill.objects.get_or_create(
                        title__iexact=value, defaults={"title": value}
                    )
                skill_objs.append(skill_obj)
            if skill_objs:
                candidate_obj.skills.set(skill_objs)

            request.session["candidate"] = serializers.serialize(
                "json", [candidate_obj]
            )
            has_direct_survey = RecruitmentSurvey.objects.filter(
                recruitment_ids=recruitment_id
            ).exists()
            has_template_survey = RecruitmentSurvey.objects.filter(
                template_id__in=recruitment.survey_templates.all()
            ).exists()
            if has_direct_survey or has_template_survey:
                return redirect(candidate_survey)
            candidate_obj.save()

            if resume_obj:
                resume_obj.is_candidate = True
                resume_obj.save()

            return render(
                request, "candidate/success.html", {"recruitment": recruitment}
            )
        for field_name, field_errors in form.errors.items():
            if field_name == "__all__":
                for error in field_errors:
                    messages.error(request, error)
            elif field_name not in FIELDS_WITH_INLINE_ERRORS:
                # Fields in FIELDS_WITH_INLINE_ERRORS already render their
                # errors next to the input itself (see the
                # {{ form.<field>.errors }} tags in application_form.html)
                # - messaging them too would show the same error twice on
                # the page (once as a toast, once inline). Anything not in
                # that set has no inline error slot in the template, so the
                # toast is the only place it would otherwise be visible.
                field_label = (
                    form.fields.get(field_name).label
                    if form.fields.get(field_name)
                    else field_name
                )
                for error in field_errors:
                    messages.error(request, f"{field_label}: {error}")
        for formset in (
            education_formset,
            experience_formset,
        ):
            for row_errors in formset.errors:
                for field_name, field_errors in row_errors.items():
                    for error in field_errors:
                        messages.error(request, error)
        recruitment_for_job_position = form.data.get("recruitment_id") or recruitment_id
        if recruitment_for_job_position:
            recruitment_for_job_position = Recruitment.objects.filter(
                id=recruitment_for_job_position
            ).first()
            if recruitment_for_job_position:
                form.fields["job_position_id"].queryset = (
                    recruitment_for_job_position.open_positions.all()
                )
        # Everything below re-fills what the JS-built parts of the form
        # (education/experience rows, skills chips) can't get back just by
        # redisplaying the bound Django form - see _formset_rows_for_prefill
        # and _submitted_skill_names above.
        education_initial = _formset_rows_for_prefill(education_formset, ["degree"])
        experience_initial = _formset_rows_for_prefill(
            experience_formset, ["designation", "company_name"]
        )
        submitted_skill_names = _submitted_skill_names(request)
    else:
        # 811
        initial_data = {"resume": resume_obj.file.url} if resume_obj else {}
        form = ApplicationForm(initial=initial_data)
        education_formset = CandidateEducationFormSet(prefix="education")
        experience_formset = CandidateExperienceFormSet(prefix="experience")
        education_initial = []
        experience_initial = []
        submitted_skill_names = []

    from horilla_theme.models import CompanyTheme

    return render(
        request,
        "candidate/application_form.html",
        {
            "form": form,
            "recruitment": recruitment,
            "resume": resume_obj,
            "platforms": Platform.objects.all(),
            "education_formset": education_formset,
            "experience_formset": experience_formset,
            "education_initial": education_initial,
            "experience_initial": experience_initial,
            "submitted_skill_names": submitted_skill_names,
            "all_skills": Skill.objects.all(),
            "currency_symbol": _get_currency_symbol(),
            # The recruitment's own company's theme - not
            # {% get_company %}'s request.session["selected_company"],
            # which is only ever set for a logged-in staff member's own
            # admin-panel session and is meaningless (usually unset) for
            # an anonymous candidate, silently falling back to the
            # site-wide default color instead of this employer's brand.
            "theme_obj": CompanyTheme.get_theme_for_company(recruitment.company_id),
        },
    )


def _get_currency_symbol():
    """
    Same currency configured for payroll amounts elsewhere in the app
    (base.templatetags.horillafilters.currency_symbol_position), read
    directly here since that filter formats a whole "amount + symbol"
    string rather than exposing the symbol alone. Falls back to "$" (the
    PayrollSettings model's own default) if payroll isn't installed or
    hasn't been configured yet.
    """
    from django.apps import apps

    if not apps.is_installed("payroll"):
        return "$"
    from payroll.models.tax_models import PayrollSettings

    settings_obj = PayrollSettings.objects.first()
    return settings_obj.currency_symbol if settings_obj else "$"


@login_required
@hx_request_required
@is_recruitment_manager(perm="recruitment.view_recruitmentsurvey")
def single_survey(request, survey_id):
    """
    This view method is used to single view of question template
    """
    question = RecruitmentSurvey.objects.get(id=survey_id)
    requests_ids_json = request.GET.get("instances_ids")
    context = {"question": question}
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, survey_id)
        context["previous"] = previous_id
        context["next"] = next_id
        context["requests_ids"] = requests_ids_json
    return render(request, "survey/view_single_template.html", context)


@login_required
@hx_request_required
def create_template(request):
    """
    Create question template views
    """
    # Check if the user has any of the two permissions
    if not (
        request.user.has_perm("recruitment.add_surveytemplate")
        or request.user.has_perm("recruitment.change_surveytemplate")
    ):
        messages.info(request, "You dont have permission.")
        return HorillaRedirect(request)

    title = request.GET.get("title")
    instance = None
    if title:
        instance = SurveyTemplate.objects.filter(title=title).first()
    form = TemplateForm(instance=instance)
    if request.method == "POST":
        form = TemplateForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Template saved")
            return HorillaRedirect(request)
    return render(request, "survey/main_form.html", {"form": form})


@login_required
@permission_required("recruitment.delete_surveytemplate")
def delete_template(request):
    """
    This method is used to delete the survey template group
    """
    title = request.GET.get("title")
    SurveyTemplate.objects.filter(title=str(title)).delete()
    if title == "None":
        messages.info(request, "This template group cannot be deleted")
    else:
        messages.success(request, "Template group deleted")

    if request.META.get("HTTP_HX_REQUEST") == "true":
        return HttpResponse("<script>$('#filterSubmit').click();</script>")
    return HorillaRedirect(request)


@login_required
@hx_request_required
@permission_required("recruitment.change_surveytemplate")
def question_add(request):
    """
    This method is used to add survey question to the templates
    """
    template = None
    title = request.GET.get("title")
    if title:
        template = SurveyTemplate.objects.filter(title=title).first

    form = AddQuestionForm(initial={"template_ids": template})
    if request.method == "POST":
        form = AddQuestionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Question added")
            if request.META.get("HTTP_HX_REQUEST") == "true":
                return HttpResponse(
                    "<script>"
                    "$('#templateModal').removeClass('oh-modal--show');"
                    "$('#genericModal').removeClass('oh-modal--show');"
                    "$('#filterSubmit').click();"
                    "</script>"
                )
            return HorillaRedirect(request)
    return render(request, "survey/add_form.html", {"form": form})
