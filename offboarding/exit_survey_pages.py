"""
offboarding/exit_survey_pages.py

The screens of the exit survey: the leaver's form, the Survey section of the
Manage window, the Survey responses tab and the Excel export.

Who sees what: the leaver fills in their own survey and nothing else. HR (the
offboarding view permission) reads the answers and exports them; a manager
looking at their report's Manage window sees only whether it was filled in,
never what was said. Changing anything (send, remind) needs the "manage
exits" permission.
"""

from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla.decorators import hx_request_required, login_required
from offboarding import exit_service, exit_survey
from offboarding.exit_pages import (
    PAGE_SIZE,
    _me,
    back_to_panel,
    _querystring,
    exit_access_required,
    is_exit_admin,
)
from offboarding.exit_service import ExitError
from offboarding.models import ExitSurvey

STATUS_CHOICES = [
    ("completed", _("Filled in")),
    ("sent", _("Waiting for the employee")),
    ("all", _("All")),
]


# --------------------------------------------------------------------------
# the leaver's own form
# --------------------------------------------------------------------------


def _items(survey, data=None, errors=None):
    errors = errors or {}
    items = []
    for q in survey.questions:
        key = f"q_{q['id']}"
        if data is not None:
            value = data.get(key, "")
            selected = data.getlist(key)
        else:
            value, selected = "", []
        items.append(
            {"q": q, "value": value, "selected": selected, "error": errors.get(str(q["id"]))}
        )
    return items


@login_required
def exit_survey_fill(request):
    me = _me(request)
    survey = exit_survey.pending_for(me) if me else None
    if survey is None:
        messages.info(request, _("You have no exit survey to fill in."))
        return redirect("/")
    errors, error = {}, None
    if request.method == "POST":
        answers, errors = exit_survey.clean_answers(survey, request.POST)
        if not errors:
            try:
                exit_survey.submit(survey, me, answers)
            except ExitError as problem:
                error = str(problem)
            else:
                messages.success(request, _("Thank you - your survey has been submitted."))
                return redirect("/")
        else:
            error = _("Please fix the answers marked below.")
    return render(
        request,
        "offboarding/exit_process/survey_fill.html",
        {
            "survey": survey,
            "items": _items(survey, request.POST if request.method == "POST" else None, errors),
            "error": error,
        },
    )


# --------------------------------------------------------------------------
# the Survey section of the Manage window
# --------------------------------------------------------------------------


def panel_context(request, case):
    survey = exit_survey.current_survey(case)
    return {
        "survey": survey,
        "survey_questions": list(exit_survey.questions_for(case.employee_id)) if survey is None else [],
    }


def _panel_survey(request, case, **kwargs):
    from offboarding.exit_panel import _panel

    return _panel(request, case, "survey", **kwargs)


def _case_for_change(request, pk):
    from offboarding.exit_panel import _changeable_case

    return _changeable_case(request, pk)


def _sent_notice(survey, what):
    if getattr(survey, "mailed", False):
        return _("%(what)s by notification and email.") % {"what": what}
    return _("%(what)s by notification. The email could not be sent.") % {"what": what}


@exit_access_required
@hx_request_required
@require_POST
def exit_survey_send(request, pk):
    case = _case_for_change(request, pk)
    if case is None:
        return render(request, "no_perm.html")
    try:
        survey = exit_survey.send_survey(case, _me(request))
    except ExitError as problem:
        return _panel_survey(request, case, error=str(problem))
    return _panel_survey(request, case, notice=_sent_notice(survey, _("The survey has been sent to the employee")))


@exit_access_required
@hx_request_required
@require_POST
def exit_survey_remind(request, pk):
    survey = get_object_or_404(ExitSurvey.objects.entire(), pk=pk)
    case = _case_for_change(request, survey.case_id_id)
    if case is None:
        return render(request, "no_perm.html")
    try:
        survey = exit_survey.remind(survey, _me(request))
    except ExitError as problem:
        return _panel_survey(request, case, error=str(problem))
    return _panel_survey(request, case, notice=_sent_notice(survey, _("Reminder sent")))


@exit_access_required
@hx_request_required
def exit_survey_view(request, pk):
    """HR reads one filled-in survey."""
    survey = get_object_or_404(ExitSurvey.objects, pk=pk)
    if not is_exit_admin(request.user) or survey.status != "completed":
        return render(request, "no_perm.html")
    return render(
        request,
        "offboarding/exit_process/survey_response.html",
        {
            "survey": survey,
            "rows": exit_survey.rows_for_display(survey),
            "back_url": back_to_panel(request, survey.case_id, default="survey"),
        },
    )


# --------------------------------------------------------------------------
# responses tab and export
# --------------------------------------------------------------------------


def _filtered(request):
    status = request.GET.get("status") or "completed"
    return status, exit_survey.responses(
        status=status if status in ("completed", "sent") else "all",
        exit_type=request.GET.get("type"),
        query=(request.GET.get("q") or "").strip() or None,
    )


def count_completed():
    return exit_survey.responses(status="completed").count()


def survey_tab(request):
    if not is_exit_admin(request.user):
        return render(request, "no_perm.html")
    status, rows = _filtered(request)
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "offboarding/exit_process/survey_tab.html",
        {
            "rows": list(page.object_list),
            "page": page,
            "status": status,
            "status_choices": STATUS_CHOICES,
            "querystring": _querystring(request),
        },
    )


@exit_access_required
def exit_survey_export(request):
    """Excel download of the filled-in surveys (HR only)."""
    if not is_exit_admin(request.user):
        return render(request, "no_perm.html")
    _status, rows = _filtered(request)
    rows = rows.filter(status="completed")
    content = exit_survey.build_workbook(rows)
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="exit-survey-{date.today().isoformat()}.xlsx"'
    )
    return response
