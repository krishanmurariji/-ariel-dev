"""
offboarding/exit_survey.py

The exit survey: the feedback form a leaver fills in before they go.

HR keeps one list of questions (Settings > Offboarding > Exit Survey). When a
survey is sent for an exit, a *copy* of the questions is frozen onto it, so
changing the questions later never rewrites feedback that has already been
given. The leaver answers once; HR reads the responses and can export them.

Like the other exit_*.py modules this knows nothing about screens.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from horilla.models import has_xss
from offboarding import exit_service
from offboarding.exit_service import ExitError
from offboarding.models import ExitSurvey, ExitSurveyQuestion, OffboardingEmployee

SHORT_LIMIT = 300
LONG_LIMIT = 2000
REMINDER_GAP = 60 * 60  # seconds


# --------------------------------------------------------------------------
# the question list
# --------------------------------------------------------------------------


def questions_for(employee):
    """Active questions that apply to this employee's company, in order."""
    info = getattr(employee, "employee_work_info", None)
    company_id = getattr(info, "company_id_id", None)
    rows = ExitSurveyQuestion.objects.entire().filter(is_active=True)
    rows = rows.filter(Q(company_id__isnull=True) | Q(company_id=company_id))
    return rows.order_by("sequence", "pk")


def choices_of(question):
    return [c.strip() for c in (question.options or "").split(",") if c.strip()]


def clean_question(question, qtype, options):
    """Validate a question being saved; returns (text, options string)."""
    question = (question or "").strip()
    if not question:
        raise ExitError(_("Please type the question."))
    if len(question) > 500:
        raise ExitError(_("A question can be at most 500 characters."))
    if has_xss(question):
        raise ExitError(_("The question contains content that is not allowed."))
    options = (options or "").strip()
    if qtype in ("options", "multiple"):
        parts = [p.strip() for p in options.split(",") if p.strip()]
        if len(parts) < 2:
            raise ExitError(_("Give at least two choices, separated by commas."))
        if len(set(p.lower() for p in parts)) != len(parts):
            raise ExitError(_("The same choice appears twice."))
        if any(has_xss(p) or len(p) > 100 for p in parts):
            raise ExitError(_("A choice is too long or contains content that is not allowed."))
        options = ", ".join(parts)
    else:
        options = ""
    return question, options


IMPORT_TYPES = {
    "checkbox": "checkbox",
    "options": "options",
    "multiple": "multiple",
    "text": "text",
    "textarea": "textarea",
    "number": "number",
    "percentage": "number",
    "date": "date",
    "rating": "rating",
    # "file" is not offered in the exit survey
}


@transaction.atomic
def import_from_recruitment(template, company_id=None):
    """
    Copy the questions of an existing recruitment survey template into the
    exit survey. Questions already present (same text) are skipped, and file
    upload questions are left out. Returns (copied, skipped).
    """
    existing = {
        q.question.strip().lower()
        for q in ExitSurveyQuestion.objects.entire().filter(
            Q(company_id__isnull=True) | Q(company_id=company_id)
        )
    }
    from recruitment.models import RecruitmentSurvey

    start = (
        ExitSurveyQuestion.objects.entire().order_by("-sequence").values_list("sequence", flat=True).first()
        or 0
    )
    copied = skipped = 0
    questions = RecruitmentSurvey.objects.entire().filter(template_id=template).order_by("sequence", "pk")
    for source in questions:
        kind = IMPORT_TYPES.get(source.type)
        text = (source.question or "").strip()
        if kind is None or not text or text.lower() in existing:
            skipped += 1
            continue
        try:
            text, options = clean_question(text, kind, ", ".join(
                c.strip() for c in (source.options or "").split(",") if c.strip()
            ))
        except ExitError:
            skipped += 1
            continue
        start += 1
        ExitSurveyQuestion.objects.create(
            company_id_id=company_id,
            question=text,
            type=kind,
            options=options,
            is_mandatory=source.is_mandatory,
            sequence=start,
        )
        existing.add(text.lower())
        copied += 1
    return copied, skipped


# --------------------------------------------------------------------------
# sending and answering
# --------------------------------------------------------------------------


def current_survey(case):
    return (
        ExitSurvey.objects.entire()
        .filter(case_id=case, is_current=True)
        .exclude(status="cancelled")
        .order_by("-pk")
        .first()
    )


def _snapshot(questions):
    return [
        {
            "id": q.pk,
            "question": q.question,
            "type": q.type,
            "options": choices_of(q),
            "mandatory": q.is_mandatory,
        }
        for q in questions
    ]


@transaction.atomic
def send_survey(case, by=None):
    """Send the survey to the leaver (freezing today's questions onto it)."""
    case = type(case).objects.entire().select_for_update().get(pk=case.pk)
    if case.status != "in_progress":
        raise ExitError(_("A survey can only be sent while the exit is in progress."))
    if current_survey(case) is not None:
        raise ExitError(_("A survey has already been sent for this exit."))
    questions = list(questions_for(case.employee_id))
    if not questions:
        raise ExitError(_("There are no exit survey questions yet. Add some in Exit Settings first."))
    survey = ExitSurvey.objects.create(
        case_id=case, sent_by=by, questions=_snapshot(questions), answers={}
    )
    exit_service.log_activity(
        case.employee_id, "survey_sent", by, exit_service._letter_of(case)
    )
    exit_service._tell(
        case.employee_id,
        by,
        _("Please take a few minutes to fill in your exit survey."),
        redirect=reverse("exit-survey-fill"),
    )
    from offboarding.mailers import send_exit_survey_mail

    survey.mailed = send_exit_survey_mail(case, survey)
    return survey


def _due(case, setting, today):
    """Has the time come to send this exit's survey automatically?"""
    if not getattr(setting, "exit_survey_auto_send", False):
        return False
    days = getattr(setting, "exit_survey_send_before_days", None)
    if not days or not case.last_working_day:
        return True  # "when the exit starts"
    return (case.last_working_day - today).days <= days


def auto_send(case):
    """
    Called when an exit starts: send the survey now if Exit Settings say so. If
    they say "N days before the last working day" and that day has not come yet,
    the daily job (send_due) sends it then.
    """
    setting = exit_service._settings_for(case.employee_id)
    if not _due(case, setting, date.today()):
        return None
    if not questions_for(case.employee_id).exists():
        return None
    return send_survey(case, None)


def send_due(today=None):
    """
    Daily job: send the survey to everyone whose exit is in progress, who has
    not been sent one, and whose chosen send date (N days before their last
    working day) has come. Returns how many were sent.
    """
    today = today or date.today()
    sent = 0
    cases = (
        OffboardingEmployee.objects.entire()
        .filter(status="in_progress", last_working_day__isnull=False)
        .select_related("employee_id")
    )
    for case in cases:
        if current_survey(case) is not None:
            continue
        setting = exit_service._settings_for(case.employee_id)
        if not getattr(setting, "exit_survey_send_before_days", None):
            continue  # "when the exit starts" was handled at the start
        if not _due(case, setting, today) or not questions_for(case.employee_id).exists():
            continue
        try:
            send_survey(case, None)
        except ExitError:
            continue
        sent += 1
    return sent


def retire_surveys(case):
    """The exit was cancelled: unfinished surveys are cancelled, all become history."""
    ExitSurvey.objects.entire().filter(case_id=case, status="sent").update(status="cancelled")
    ExitSurvey.objects.entire().filter(case_id=case).update(is_current=False)


@transaction.atomic
def remind(survey, by):
    survey = ExitSurvey.objects.entire().select_for_update().get(pk=survey.pk)
    if survey.status != "sent":
        raise ExitError(_("Only a survey that has not been filled in can be chased."))
    now = timezone.now()
    if survey.last_reminded_at and (now - survey.last_reminded_at).total_seconds() < REMINDER_GAP:
        raise ExitError(_("A reminder was sent less than an hour ago."))
    survey.last_reminded_at = now
    survey.save()
    exit_service._tell(
        survey.case_id.employee_id,
        by,
        _("Reminder: please fill in your exit survey."),
        redirect=reverse("exit-survey-fill"),
    )
    from offboarding.mailers import send_exit_survey_mail

    survey.mailed = send_exit_survey_mail(survey.case_id, survey, reminder=True)
    return survey


def _field(question_id):
    return f"q_{question_id}"


def clean_answers(survey, data):
    """
    Check what the leaver submitted against the frozen questions.
    Returns (answers, errors): answers keyed by question id (as text), errors
    keyed the same way with a message each.
    """
    answers, errors = {}, {}
    required = _("This question is required.")
    for q in survey.questions:
        key = str(q["id"])
        kind = q["type"]
        if kind == "multiple":
            raw = [v.strip() for v in data.getlist(_field(q["id"])) if v.strip()]
        else:
            raw = (data.get(_field(q["id"])) or "").strip()
        if not raw:
            if q["mandatory"]:
                errors[key] = required
            continue
        try:
            answers[key] = _convert(q, raw)
        except ValueError as problem:
            errors[key] = str(problem)
    return answers, errors


def _convert(q, raw):
    kind = q["type"]
    if kind in ("text", "textarea"):
        limit = SHORT_LIMIT if kind == "text" else LONG_LIMIT
        if len(raw) > limit:
            raise ValueError(_("Please keep this under %(n)s characters.") % {"n": limit})
        if has_xss(raw):
            raise ValueError(_("This text contains content that is not allowed."))
        return raw
    if kind == "number":
        try:
            number = Decimal(raw)
        except InvalidOperation:
            raise ValueError(_("Please enter a number."))
        if not number.is_finite() or abs(number) >= Decimal(10) ** 12:
            raise ValueError(_("Please enter a number."))
        return int(number) if number == number.to_integral_value() else float(number)
    if kind == "date":
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise ValueError(_("Please enter a valid date."))
    if kind == "checkbox":
        if raw not in ("yes", "no"):
            raise ValueError(_("Please choose Yes or No."))
        return raw
    if kind == "rating":
        if raw not in ("1", "2", "3", "4", "5"):
            raise ValueError(_("Please give a rating from 1 to 5."))
        return int(raw)
    if kind == "options":
        if raw not in q["options"]:
            raise ValueError(_("Please choose one of the options."))
        return raw
    if kind == "multiple":
        if any(value not in q["options"] for value in raw):
            raise ValueError(_("Please choose from the options shown."))
        return list(dict.fromkeys(raw))
    raise ValueError(_("This question cannot be answered."))


@transaction.atomic
def submit(survey, employee, answers):
    """The leaver hands in their answers (once)."""
    survey = ExitSurvey.objects.entire().select_for_update().get(pk=survey.pk)
    if survey.case_id.employee_id_id != employee.pk:
        raise ExitError(_("This survey is not yours to fill in."))
    if survey.status != "sent" or not survey.is_current:
        raise ExitError(_("This survey is no longer open."))
    survey.answers = answers
    survey.status = "completed"
    survey.completed_at = timezone.now()
    survey.save()
    exit_service.log_activity(
        survey.case_id.employee_id, "survey_done", employee, exit_service._letter_of(survey.case_id)
    )
    return survey


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def survey_state(case):
    """grey = not sent, yellow = waiting for the leaver, green = filled in."""
    survey = current_survey(case)
    if survey is None:
        return "todo"
    return "done" if survey.status == "completed" else "doing"


def survey_applies(case):
    """Show the survey icon only when a survey exists or sending one is possible."""
    return current_survey(case) is not None or questions_for(case.employee_id).exists()


def pending_for(employee):
    """The open survey waiting on this person, if any."""
    return (
        ExitSurvey.objects.entire()
        .filter(
            case_id__employee_id=employee,
            case_id__status="in_progress",
            status="sent",
            is_current=True,
        )
        .order_by("-pk")
        .first()
    )


def rows_for_display(survey):
    """[(question, readable answer)] for showing a response."""
    out = []
    for q in survey.questions:
        value = survey.answers.get(str(q["id"]))
        out.append((q, display_value(q, value)))
    return out


def display_value(question, value):
    if value in (None, "", []):
        return ""
    if question["type"] == "multiple":
        return ", ".join(str(v) for v in value)
    if question["type"] == "checkbox":
        return _("Yes") if value == "yes" else _("No")
    if question["type"] == "rating":
        return f"{value} / 5"
    if question["type"] == "date":
        try:
            return date.fromisoformat(value).strftime("%d %b %Y")
        except ValueError:
            return str(value)
    return str(value)


def responses(status="completed", exit_type=None, query=None, scope=None):
    """Surveys for the responses list. `scope(qs)` lets the caller narrow by who may see them."""
    # .objects (not .entire()): follows the company chosen at the top of the page
    rows = ExitSurvey.objects.select_related(
        "case_id__employee_id", "sent_by"
    ).exclude(status="cancelled")
    if status in ("completed", "sent"):
        rows = rows.filter(status=status)
    if exit_type in ("resignation", "termination"):
        rows = rows.filter(case_id__exit_type=exit_type)
    if query:
        employee = "case_id__employee_id"
        rows = rows.filter(
            Q(**{f"{employee}__employee_first_name__icontains": query})
            | Q(**{f"{employee}__employee_last_name__icontains": query})
            | Q(**{f"{employee}__badge_id__icontains": query})
        )
    if scope is not None:
        rows = scope(rows)
    return rows.order_by("-completed_at", "-pk")


# --------------------------------------------------------------------------
# Excel export
# --------------------------------------------------------------------------


def _safe_cell(value):
    """Stop spreadsheet programs treating text as a formula (CSV/Excel injection)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def build_workbook(surveys):
    """An .xlsx of completed responses: one row per leaver, one column per question."""
    from io import BytesIO

    from openpyxl import Workbook

    surveys = list(surveys)
    columns = []
    for survey in surveys:
        for q in survey.questions:
            if q["question"] not in columns:
                columns.append(q["question"])
    book = Workbook()
    sheet = book.active
    sheet.title = "Exit survey"
    sheet.append(
        ["Employee", "Employee ID", "Department", "Job position", "Exit type", "Last working day", "Submitted on"]
        + [_safe_cell(c) for c in columns]
    )
    for survey in surveys:
        employee = survey.case_id.employee_id
        by_text = {q["question"]: display_value(q, survey.answers.get(str(q["id"]))) for q in survey.questions}
        sheet.append(
            [
                _safe_cell(employee.get_full_name()),
                _safe_cell(employee.badge_id or ""),
                _safe_cell(str(employee.get_department() or "")),
                _safe_cell(str(employee.get_job_position() or "")),
                survey.case_id.get_exit_type_display(),
                survey.case_id.last_working_day.strftime("%d %b %Y") if survey.case_id.last_working_day else "",
                survey.completed_at.strftime("%d %b %Y") if survey.completed_at else "",
            ]
            + [_safe_cell(by_text.get(c, "")) for c in columns]
        )
    out = BytesIO()
    book.save(out)
    return out.getvalue()
