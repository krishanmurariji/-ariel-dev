"""
recruitment/views/candidate_intake.py

Plain views for the Candidate Email Intake feature that don't fit the
CBV list/nav/form pattern: toggling a mailbox active, deleting a mailbox,
the two standalone pages, and the manual poll trigger/status widget.
"""

import threading

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from horilla.config import logger
from horilla.decorators import login_required, permission_required
from horilla.http.response import HorillaRedirect
from recruitment.models import CandidateIntakeLog, CandidateIntakeMailbox


@login_required
@permission_required("recruitment.change_candidateintakemailbox")
def update_isactive_intake_mailbox(request, obj_id):
    """
    htmx function to update is_active field on a CandidateIntakeMailbox.
    """
    mailbox = CandidateIntakeMailbox.find(obj_id)
    if not mailbox:
        return HorillaRedirect(
            request, message=_("No mailbox found matching the query.")
        )

    if request.POST.get("is_active") == "on":
        mailbox.is_active = True
        messages.success(request, _("Mailbox activated successfully."))
    else:
        mailbox.is_active = False
        messages.success(request, _("Mailbox deactivated successfully."))
    mailbox.save()

    return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")


@login_required
@permission_required("recruitment.delete_candidateintakemailbox")
def delete_intake_mailbox(request, pk):
    """
    Delete a candidate intake mailbox.
    """
    try:
        CandidateIntakeMailbox.objects.get(id=pk).delete()
        messages.success(request, _("Mailbox disconnected."))
    except Exception as e:
        logger.error(e)
        messages.error(request, _("Something went wrong"))
    return HorillaRedirect(request)


@login_required
@permission_required("recruitment.view_candidateintakemailbox")
def candidate_intake_mailbox_settings(request):
    """
    Standalone page listing connected candidate intake mailboxes.
    """
    return render(request, "mailbox/mailbox_page.html")


@login_required
@permission_required("recruitment.view_candidateintakelog")
def candidate_intake_logs(request):
    """
    Standalone page listing every email the intake mailboxes have processed.
    """
    return render(request, "mailbox/intake_logs_page.html")


def _next_scheduled_run():
    """
    Next time the automatic (every-5-minutes) scheduler job will poll,
    independent of any manually-triggered run.
    """
    try:
        from recruitment.scheduler import scheduler

        job = scheduler.get_job("poll_candidate_intake_mailboxes")
        return job.next_run_time if job else None
    except Exception:
        return None


def _poll_status_context():
    from recruitment.intake import POLL_LAST_RESULT_CACHE_KEY, POLL_RUNNING_CACHE_KEY

    mailbox_errors = [
        (mailbox.mailbox_account_display(), mailbox.last_error)
        for mailbox in CandidateIntakeMailbox.objects.filter(is_active=True).exclude(
            last_error=""
        )
    ]
    return {
        "running": bool(cache.get(POLL_RUNNING_CACHE_KEY)),
        "last_result": cache.get(POLL_LAST_RESULT_CACHE_KEY),
        "next_scheduled_run": _next_scheduled_run(),
        "mailbox_errors": mailbox_errors,
    }


@login_required
@permission_required("recruitment.change_candidateintakemailbox")
def trigger_intake_poll(request):
    """
    Starts a poll of every active mailbox in the background, if one
    isn't already running.
    """
    from recruitment.intake import POLL_RUNNING_CACHE_KEY, run_guarded_poll

    if cache.get(POLL_RUNNING_CACHE_KEY):
        messages.info(request, _("A poll is already running."))
    else:
        threading.Thread(target=run_guarded_poll, daemon=True).start()
        messages.success(request, _("Poll started."))
    return render(request, "mailbox/intake_poll_status.html", _poll_status_context())


@login_required
@permission_required("recruitment.view_candidateintakelog")
def intake_poll_status(request):
    """
    htmx-polled fragment showing whether a poll is currently running and
    when the last one finished - self-refreshes every few seconds.
    """
    return render(request, "mailbox/intake_poll_status.html", _poll_status_context())


@login_required
@permission_required("recruitment.view_candidateintakelog")
def candidate_intake_log_detail(request, pk):
    """
    Modal body: full detail of one processed email (opened by clicking a
    row on the Email Applications page).
    """
    log = CandidateIntakeLog.objects.filter(pk=pk).first()
    if not log:
        return HorillaRedirect(request, message=_("Log entry not found."))
    return render(request, "mailbox/intake_log_detail.html", {"instance": log})


@login_required
@permission_required("recruitment.change_candidateintakemailbox")
def retry_selected_intake_logs(request):
    """
    Re-fetches and reprocesses each selected CandidateIntakeLog row from
    Graph, regardless of its previous status - unlike the automatic poll,
    which only retries transient (parse_failed/error) outcomes.
    """
    from recruitment.intake import retry_message

    ids = request.POST.getlist("ids")
    retried = 0
    failed = 0
    for log in CandidateIntakeLog.objects.filter(pk__in=ids):
        try:
            retry_message(log)
            retried += 1
        except Exception:
            logger.exception("Failed to retry candidate intake log %s", log.pk)
            failed += 1

    if retried:
        messages.success(request, _("Retried {count} email(s).").format(count=retried))
    if failed:
        messages.error(
            request, _("{count} retry attempt(s) failed - see server logs.").format(count=failed)
        )
    if not ids:
        messages.info(request, _("No rows selected."))

    return HttpResponse(
        "<script>htmx.trigger(document.body, 'refreshIntakeLog'); $('#reloadMessagesButton').click();</script>"
    )
