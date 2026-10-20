"""
offboarding/exit_survey_settings.py

Settings > Offboarding > Exit Survey: the list of questions every leaver is
asked, whether the survey goes out automatically when an exit starts, and a
button to copy questions from a survey template already built in Recruitment.

Same scope rule as the rest of Exit Settings: with a company selected you edit
that company's questions (organisation-wide ones show read-only); in "All
Company" mode you edit the organisation-wide ones.

Like Exit Settings, every action works from the page (htmx: a window to add or
edit, a confirmation pop-up, the list refreshes) and as a plain form post that
returns to the page.
"""

from django import forms
from django.apps import apps
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla.decorators import login_required, permission_required
from offboarding import exit_survey
from offboarding.exit_service import ExitError
from offboarding.exit_settings import PERM, _errors, _scope
from offboarding.exit_settings import _answer as _answer_on_settings_page
from offboarding.models import ExitSurveyQuestion, OffboardingGeneralSetting
from django.db.models import Q


def _answer(request, **kwargs):
    """Answer an action on this page (plain posts come back here)."""
    return _answer_on_settings_page(request, back="exit-survey-settings", **kwargs)


class QuestionForm(forms.ModelForm):
    class Meta:
        model = ExitSurveyQuestion
        fields = ["question", "type", "options", "is_mandatory", "is_active", "sequence"]

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            cleaned["question"], cleaned["options"] = exit_survey.clean_question(
                cleaned.get("question"), cleaned.get("type"), cleaned.get("options")
            )
        except ExitError as problem:
            raise forms.ValidationError(str(problem))
        return cleaned


def _visible(scope):
    rows = ExitSurveyQuestion.objects.entire()
    if scope:
        return rows.filter(Q(company_id=scope) | Q(company_id__isnull=True))
    return rows.filter(company_id__isnull=True)


def _recruitment_templates():
    if not apps.is_installed("recruitment"):
        return []
    from recruitment.models import SurveyTemplate

    return list(SurveyTemplate.objects.entire().order_by("title"))


def _questions_context(request):
    scope = _scope(request)
    questions = list(_visible(scope).order_by("sequence", "pk"))
    for question in questions:
        question.editable = question.company_id_id == scope
        question.choices = exit_survey.choices_of(question)
    return {
        "questions": questions,
        "scope_company": scope,
        "templates": _recruitment_templates(),
    }


@login_required
@permission_required(PERM)
def exit_survey_settings(request):
    context = _questions_context(request)
    return render(
        request,
        "offboarding/settings/exit_survey.html",
        {
            **context,
            "auto_send": OffboardingGeneralSetting.for_editing(request).exit_survey_auto_send,
            "send_before": OffboardingGeneralSetting.for_editing(request).exit_survey_send_before_days,
            "send_times": OffboardingGeneralSetting.SURVEY_SEND_TIMES,
        },
    )


@login_required
@permission_required(PERM)
def exit_survey_questions_section(request):
    """Just the list of questions, so it can refresh after a change."""
    return render(
        request, "offboarding/settings/_xq_questions.html", _questions_context(request)
    )


@login_required
@permission_required(PERM)
def exit_survey_question_form(request, pk=None):
    """The window to add a question, or edit one."""
    scope = _scope(request)
    instance = None
    if pk is not None:
        instance = get_object_or_404(ExitSurveyQuestion.objects.entire(), pk=pk)
        if instance.company_id_id != scope:
            raise Http404
    rows = _visible(scope)
    return render(
        request,
        "offboarding/settings/modals/question_form.html",
        {
            "question": instance,
            "types": ExitSurveyQuestion.TYPES,
            "next_sequence": (rows.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1,
        },
    )


@login_required
@permission_required(PERM)
def exit_survey_import_form(request):
    """The window to copy questions from a Recruitment survey."""
    return render(
        request,
        "offboarding/settings/modals/survey_import.html",
        {"templates": _recruitment_templates()},
    )


@login_required
@permission_required(PERM)
@require_POST
def exit_survey_auto_send(request):
    row = OffboardingGeneralSetting.for_editing(request)
    row.exit_survey_auto_send = request.POST.get("auto_send") == "on"
    when = request.POST.get("send_before") or ""
    allowed = {str(days) for days, _label in OffboardingGeneralSetting.SURVEY_SEND_TIMES}
    row.exit_survey_send_before_days = int(when) if when in allowed else None
    row.company_id_id = _scope(request)
    row.save()
    if not row.exit_survey_auto_send:
        message = _("The survey will only be sent when HR sends it.")
    elif row.exit_survey_send_before_days is None:
        message = _("The survey will be sent automatically when an exit starts.")
    else:
        label = dict(OffboardingGeneralSetting.SURVEY_SEND_TIMES)[row.exit_survey_send_before_days]
        message = _("The survey will be sent automatically %(when)s.") % {"when": str(label).lower()}
    return _answer(request, message=message, close=False)


@login_required
@permission_required(PERM)
@require_POST
def exit_survey_question_save(request, pk=None):
    scope = _scope(request)
    instance = None
    if pk is not None:
        instance = get_object_or_404(ExitSurveyQuestion.objects.entire(), pk=pk)
        if instance.company_id_id != scope:
            return _answer(
                request,
                error=_("Organisation-wide questions can only be changed in All Company mode."),
                close=False,
            )
    data = request.POST.copy()
    data["is_active"] = data.get("is_active", "on") if instance is None else data.get("is_active", "")
    form = QuestionForm(data, instance=instance)
    if not form.is_valid():
        return _answer(request, error=_errors(form), close=False)
    question = form.save(commit=False)
    if instance is None:
        question.company_id_id = scope
    question.save()
    return _answer(request, message=_("The question has been saved."))


@login_required
@permission_required(PERM)
@require_POST
def exit_survey_question_delete(request, pk):
    question = get_object_or_404(ExitSurveyQuestion.objects.entire(), pk=pk)
    if question.company_id_id != _scope(request):
        return _answer(
            request,
            error=_("Organisation-wide questions can only be changed in All Company mode."),
            close=False,
        )
    question.delete()
    return _answer(request, message=_("The question was deleted. Surveys already sent keep it."))


@login_required
@permission_required(PERM)
@require_POST
def exit_survey_import(request):
    template_id = request.POST.get("template")
    template = next((t for t in _recruitment_templates() if str(t.pk) == str(template_id)), None)
    if template is None:
        return _answer(request, error=_("Please choose a survey to copy from."), close=False)
    copied, skipped = exit_survey.import_from_recruitment(template, _scope(request))
    return _answer(
        request,
        message=_("%(copied)s question(s) copied, %(skipped)s skipped (already there or not supported).")
        % {"copied": copied, "skipped": skipped},
    )
