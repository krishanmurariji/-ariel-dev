"""
recruitment/views/job_suggestion.py

Views for the AI job match suggestion card on the AI Score tab: the
manual "Check for a better fit" trigger and its self-refreshing status
fragment. Applying a suggestion (moving the candidate to the suggested
job) is handled by the shared recruitment.cbv.pipeline.CandidateMovePipelineFormView
modal - see the "Move to suggested job" button in job_suggestion_status.html.
"""

import threading

from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.http.response import HorillaRedirect
from recruitment.models import Candidate, CandidateJobSuggestion


def _running_cache_key(candidate_id):
    return f"candidate_job_suggestion_running_{candidate_id}"


def _run_suggestion_in_background(candidate_id):
    from recruitment.ai_job_match import suggest_job_match

    try:
        candidate = Candidate.objects.filter(pk=candidate_id).first()
        if candidate:
            suggest_job_match(candidate)
    except Exception:
        from horilla.config import logger

        logger.exception(
            "Job match suggestion failed for candidate %s", candidate_id
        )
    finally:
        cache.delete(_running_cache_key(candidate_id))


def _suggestion_status_context(candidate_id):
    return {
        "running": bool(cache.get(_running_cache_key(candidate_id))),
        "latest": CandidateJobSuggestion.objects.filter(
            candidate_id=candidate_id
        ).first(),
        "candidate_id": candidate_id,
    }


@login_required
@permission_required("recruitment.add_candidatejobsuggestion")
def trigger_candidate_job_suggestion(request, pk):
    """
    Starts (or re-runs) the job match check for one candidate in the
    background, unless a check for this same candidate is already running.
    """
    candidate_obj = Candidate.find(pk)
    if not candidate_obj:
        messages.error(request, _("Candidate not found"))
        return HorillaRedirect(request)

    if not cache.add(_running_cache_key(pk), True, timeout=None):
        messages.info(request, _("Check is already running for this candidate."))
    else:
        threading.Thread(
            target=_run_suggestion_in_background, args=(pk,), daemon=True
        ).start()
        messages.success(request, _("Checking for a better job match."))
    return render(
        request,
        "candidate/job_suggestion_status.html",
        _suggestion_status_context(pk),
    )


@login_required
@permission_required("recruitment.view_candidatejobsuggestion")
def candidate_job_suggestion_status(request, pk):
    """
    htmx-polled fragment showing whether the check is running and the
    latest result - self-refreshes every few seconds.
    """
    return render(
        request,
        "candidate/job_suggestion_status.html",
        _suggestion_status_context(pk),
    )
