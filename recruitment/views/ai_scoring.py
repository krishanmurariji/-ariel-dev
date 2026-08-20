"""
recruitment/views/ai_scoring.py

Views for the AI candidate scoring tab: the tab content itself, the manual
"Score with AI" / re-score trigger, and its self-refreshing status fragment.
"""

import threading

from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from horilla.config import logger
from horilla.decorators import login_required, permission_required
from horilla.http.response import HorillaRedirect
from recruitment.models import Candidate, CandidateAIScore


def _running_cache_key(candidate_id):
    return f"candidate_ai_score_running_{candidate_id}"


def _run_score_in_background(candidate_id):
    from recruitment.ai_scoring import score_candidate

    try:
        candidate = Candidate.objects.filter(pk=candidate_id).first()
        if candidate:
            score_candidate(candidate)
    except Exception:
        logger.exception("Manual AI scoring failed for candidate %s", candidate_id)
    finally:
        cache.delete(_running_cache_key(candidate_id))


def kick_off_rescoring(candidate_id):
    """
    Starts AI scoring for one candidate in the background, unless a
    scoring run for this same candidate is already in progress. Shared
    with recruitment/views/job_suggestion.py and recruitment/cbv/pipeline.py
    so a candidate's score gets refreshed automatically whenever they're
    moved to a different job, not just via the manual "Score with AI"
    button - the score is meaningless once it was computed against a job
    the candidate is no longer applying for.
    """
    if not cache.add(_running_cache_key(candidate_id), True, timeout=None):
        return False
    threading.Thread(
        target=_run_score_in_background, args=(candidate_id,), daemon=True
    ).start()
    return True


def _score_status_context(candidate_id):
    return {
        "running": bool(cache.get(_running_cache_key(candidate_id))),
        "latest": CandidateAIScore.objects.filter(candidate_id=candidate_id).first(),
        "history": CandidateAIScore.objects.filter(candidate_id=candidate_id)[1:6],
        "candidate_id": candidate_id,
    }


@login_required
@permission_required("recruitment.view_candidateaiscore")
def candidate_ai_score_tab(request, pk, **kwargs):
    """
    AI Score tab content on the candidate profile page.
    """
    candidate_obj = Candidate.find(pk)
    if not candidate_obj:
        messages.error(request, _("Candidate not found"))
        return HorillaRedirect(request)
    return render(
        request,
        "candidate/ai_score_tab.html",
        {"candidate": candidate_obj, **_score_status_context(pk)},
    )


@login_required
@permission_required("recruitment.add_candidateaiscore")
def trigger_candidate_ai_score(request, pk):
    """
    Starts (or re-runs) AI scoring for one candidate in the background,
    unless a scoring run for this same candidate is already in progress.
    """
    candidate_obj = Candidate.find(pk)
    if not candidate_obj:
        messages.error(request, _("Candidate not found"))
        return HorillaRedirect(request)

    if kick_off_rescoring(pk):
        messages.success(request, _("AI scoring started."))
    else:
        messages.info(request, _("Scoring is already running for this candidate."))
    return render(
        request,
        "candidate/ai_score_status.html",
        _score_status_context(pk),
    )


@login_required
@permission_required("recruitment.view_candidateaiscore")
def candidate_ai_score_status(request, pk):
    """
    htmx-polled fragment showing whether scoring is running and the
    latest result - self-refreshes every few seconds.
    """
    return render(
        request,
        "candidate/ai_score_status.html",
        _score_status_context(pk),
    )
