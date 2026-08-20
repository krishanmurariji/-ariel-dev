"""
recruitment/views/resume_autofill.py

Public "Autofill with AI" endpoint for the candidate application form
(/recruitment/application-form/): parses an uploaded resume and hands back
only the fields that passed validation - see recruitment/resume_ai_fill.py
for the parsing/validation itself.
"""

import logging

from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from recruitment.models import validate_pdf
from recruitment.resume_ai_fill import ResumeAutofillError, parse_resume_for_form

logger = logging.getLogger(__name__)

MAX_RESUME_SIZE = 5 * 1024 * 1024  # 5MB

RATE_LIMIT_MAX_CALLS = 8
RATE_LIMIT_WINDOW_SECONDS = 600


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _rate_limited(request):
    """
    This is a public, unauthenticated endpoint that triggers an LLM call,
    so it needs its own abuse guard rather than relying on login/permission
    checks like the rest of the recruitment views.
    """
    key = f"resume_ai_autofill_{_client_ip(request)}"
    count = cache.get(key, 0)
    if count >= RATE_LIMIT_MAX_CALLS:
        return True
    cache.set(key, count + 1, timeout=RATE_LIMIT_WINDOW_SECONDS)
    return False


@require_POST
def resume_ai_autofill(request):
    """
    Parses the posted resume file and returns validated field values only;
    anything the AI wasn't confident about comes back as valid=False so the
    frontend leaves that input blank instead of silently filling in a
    guess.
    """
    if _rate_limited(request):
        return JsonResponse(
            {"error": "Too many requests. Please try again in a few minutes."},
            status=429,
        )

    resume_file = request.FILES.get("resume")
    if not resume_file:
        return JsonResponse({"error": "No resume file was provided."}, status=400)

    if resume_file.size > MAX_RESUME_SIZE:
        return JsonResponse({"error": "Resume file is too large (max 5MB)."}, status=400)

    try:
        validate_pdf(resume_file)
    except ValidationError:
        return JsonResponse({"error": "Resume must be a PDF file."}, status=400)

    try:
        result = parse_resume_for_form(resume_file.read())
    except ResumeAutofillError as exc:
        logger.info("Resume AI autofill could not parse resume: %s", exc)
        return JsonResponse(
            {
                "error": "Couldn't read this resume automatically. "
                "Please fill the form in manually."
            }
        )
    except Exception:
        logger.exception("Unexpected error in resume AI autofill")
        return JsonResponse(
            {
                "error": "Something went wrong analyzing the resume. "
                "Please fill the form in manually."
            }
        )

    return JsonResponse(result)
