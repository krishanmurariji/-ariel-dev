"""
recruitment/views/ai_history_summary.py

Views for the AI history summary on the candidate History tab: the
manual "Summarize with AI" trigger and its self-refreshing status
fragment. Mirrors recruitment/views/ai_scoring.py.
"""

import threading

from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from horilla.config import logger
from horilla.decorators import login_required
from horilla.http.response import HorillaRedirect
from recruitment.decorators import manager_can_enter
from recruitment.models import Candidate, CandidateHistorySummary


def _running_cache_key(candidate_id):
    return f"candidate_history_summary_running_{candidate_id}"


def _run_summary_in_background(candidate_id):
    from recruitment.ai_history_summary import summarize_candidate_history

    try:
        candidate = Candidate.objects.filter(pk=candidate_id).first()
        if candidate:
            summarize_candidate_history(candidate)
    except Exception:
        logger.exception(
            "Manual AI history summary failed for candidate %s", candidate_id
        )
    finally:
        cache.delete(_running_cache_key(candidate_id))


def _summary_status_context(candidate_id):
    return {
        "running": bool(cache.get(_running_cache_key(candidate_id))),
        "latest": CandidateHistorySummary.objects.filter(
            candidate_id=candidate_id
        ).first(),
        "candidate_id": candidate_id,
    }


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def trigger_candidate_history_summary(request, pk):
    """
    Starts (or re-runs) an AI history summary for one candidate in the
    background, unless a run for this same candidate is already in
    progress.
    """
    candidate_obj = Candidate.find(pk)
    if not candidate_obj:
        messages.error(request, _("Candidate not found"))
        return HorillaRedirect(request)

    if not cache.add(_running_cache_key(pk), True, timeout=None):
        messages.info(request, _("Summary is already being generated."))
    else:
        threading.Thread(
            target=_run_summary_in_background, args=(pk,), daemon=True
        ).start()
        messages.success(request, _("AI summary started."))
    return render(
        request,
        "candidate/history_ai_summary_status.html",
        _summary_status_context(pk),
    )


@login_required
@manager_can_enter(perm="recruitment.view_candidate")
def candidate_history_summary_status(request, pk):
    """
    htmx-polled fragment showing whether summarization is running and
    the latest result - self-refreshes every few seconds.
    """
    return render(
        request,
        "candidate/history_ai_summary_status.html",
        _summary_status_context(pk),
    )
