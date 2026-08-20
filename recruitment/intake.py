"""
recruitment/intake.py

Turns incoming candidate-application emails (read from a connected
Outlook or Gmail mailbox - see recruitment/intake_providers/) into
Candidate records: extracts resume text, asks a local LLM to pull out
candidate details and match the right open job, then creates the
Candidate or logs why it couldn't.

Nothing in this module branches on which provider a mailbox uses -
recruitment.intake_providers.get_provider(mailbox.provider) is the only
place that decision is made, and everything past that point operates on
the normalized IntakeMessage/IntakeAttachment shape.
"""

import base64
import json
import logging
import os
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _
from notifications.signals import notify

from horilla.methods import horilla_users_with_perms
from recruitment import ai_client
from recruitment.intake_providers import AttachmentTooLargeError, get_provider
from recruitment.intake_providers.base import IntakeMessage
from recruitment.models import (
    Candidate,
    CandidateIntakeLog,
    CandidateIntakeMailbox,
    IntakePollLock,
    Platform,
    Recruitment,
    Stage,
)
from recruitment.resume_utils import extract_resume_text

logger = logging.getLogger(__name__)

# The cache flag below is informational only (drives the "poll running"
# indicator in recruitment/views/candidate_intake.py) and is per-process,
# so it can go briefly stale in a multi-worker deployment - that's fine
# for a status display. The actual cross-worker-safe mutex is
# IntakePollLock (a DB row), claimed in run_guarded_poll().
POLL_RUNNING_CACHE_KEY = "candidate_intake_poll_running"
POLL_LAST_RESULT_CACHE_KEY = "candidate_intake_poll_last_result"

INTAKE_LOCK_PK = 1


class IntakeParseError(Exception):
    """Raised when the resume/email content could not be parsed into
    structured candidate data."""


def _open_recruitment_options():
    """
    Open recruitments, summarized for the LLM prompt.
    """
    options = []
    for recruitment in Recruitment.objects.filter(closed=False, is_active=True):
        options.append(
            {
                "id": recruitment.id,
                "title": recruitment.title,
                "description": (recruitment.description or "")[:500],
                "skills": [skill.title for skill in recruitment.skills.all()],
            }
        )
    return options


def parse_and_match(
    email_subject: str, email_body: str, resume_text: str, open_recruitments: list
) -> dict:
    """
    Ask the local LLM to extract candidate details from the resume/email and
    pick the best-matching open recruitment (by id) from `open_recruitments`.

    Returns a dict: {name, email, mobile, matched_recruitment_id, confidence}
    Raises IntakeParseError if the LLM is unreachable or returns malformed data.
    """
    prompt = (
        "You are extracting job-application data. Given the email and resume "
        "text below, and a list of currently open job openings, respond with "
        "ONLY a JSON object with keys: name, email, mobile, "
        "matched_recruitment_id, confidence (0-1 float).\n\n"
        "For matched_recruitment_id: compare the resume's skills, job title, "
        "and experience against each open job opening's title/skills/"
        "description. Prefer picking the closest matching id over returning "
        "null - loose or partial overlap (e.g. related technology, similar "
        "role family) still counts as a match. Only return null if the "
        "resume is clearly unrelated to every open job opening listed.\n\n"
        f"Open job openings: {json.dumps(open_recruitments)}\n\n"
        f"Email subject: {email_subject}\n"
        f"Email body: {email_body}\n\n"
        f"Resume text:\n{resume_text[:8000]}"
    )
    try:
        content = ai_client.chat(prompt, json_mode=True)
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise IntakeParseError(str(exc)) from exc

    if "name" not in data or "email" not in data:
        raise IntakeParseError(f"LLM response missing required fields: {data}")
    return data


def _resume_attachment(message: IntakeMessage):
    """
    Return the first PDF attachment on a normalized message, or None.
    """
    for attachment in message.attachments:
        if attachment.content_type == "application/pdf":
            return attachment
    return None


def _has_non_pdf_attachment(message: IntakeMessage) -> bool:
    return any(
        attachment.content_type != "application/pdf"
        for attachment in message.attachments
    )


def _log(
    mailbox,
    message: IntakeMessage,
    status,
    candidate=None,
    matched_recruitment=None,
    error_message="",
    resume_content=None,
    resume_name=None,
):
    """
    One row per message_id, always. A retry of a previously transient
    failure (parse_failed/error/daily_cap_exceeded) updates that same row
    with the latest outcome instead of inserting a second row (message_id
    is unique).

    resume_content/resume_name persist the resume on the log row itself,
    whenever a PDF attachment was found - regardless of the outcome - so
    it can be reviewed even for failed/no-match emails, not just created
    candidates.
    """
    defaults = dict(
        mailbox=mailbox,
        from_email=message.from_email,
        subject=message.subject,
        received_at=message.received_at,
        status=status,
        candidate=candidate,
        matched_recruitment=matched_recruitment,
        error_message=error_message,
        company_id=mailbox.company_id,
    )
    if resume_content is not None:
        defaults["resume"] = ContentFile(resume_content, name=resume_name)
    CandidateIntakeLog.objects.update_or_create(
        message_id=message.id,
        defaults=defaults,
    )
    return status


def _notify_mailbox_managers(mailbox, verb: str):
    """
    Shared helper for the two admin-alert cases below (daily cap hit,
    poll failure) - notifies whoever can manage intake mailboxes.
    """
    try:
        notify.send(
            mailbox,
            recipient=horilla_users_with_perms(
                "recruitment.change_candidateintakemailbox"
            ),
            verb=verb,
            icon="warning-outline",
            redirect=reverse("candidate-intake-mailbox-settings"),
        )
    except Exception:
        # A notification failure must never break the intake pipeline.
        logger.exception("Failed to send candidate intake admin notification")


def _daily_candidate_cap_status(mailbox) -> str:
    """
    Returns "daily_cap_exceeded" if this mailbox has already hit its daily
    auto-created-candidate limit today, else "" - checked first in
    process_message(), before any attachment/LLM work, so capped messages
    cost almost nothing. Sends one admin notification per mailbox per day
    the first time the cap is hit.
    """
    effective_cap = (
        mailbox.daily_candidate_cap
        if mailbox.daily_candidate_cap is not None
        else settings.INTAKE_DAILY_CANDIDATE_CAP
    )
    today = django_timezone.now().date()
    today_created = CandidateIntakeLog.objects.filter(
        mailbox=mailbox, status="created", created_at__date=today
    ).count()
    if today_created < effective_cap:
        return ""

    already_notified_today = CandidateIntakeLog.objects.filter(
        mailbox=mailbox, status="daily_cap_exceeded", created_at__date=today
    ).exists()
    if not already_notified_today:
        _notify_mailbox_managers(
            mailbox,
            verb=_(
                "Mailbox {mailbox} hit its daily candidate cap ({cap})."
            ).format(mailbox=mailbox, cap=effective_cap),
        )
    return "daily_cap_exceeded"


def process_message(mailbox: CandidateIntakeMailbox, message: IntakeMessage, force: bool = False):
    """
    Process a single normalized message: extract resume, parse/match, and
    create a Candidate, or write a CandidateIntakeLog row explaining why not.

    `force=True` (used by explicit user-triggered retries, see
    retry_message()) reprocesses regardless of any prior outcome; the
    automatic poll path (force=False) only retries transient failures.
    """
    # parse_failed/error/daily_cap_exceeded are transient (LLM timeout,
    # network blip, cap not yet reset) and get retried on the next poll;
    # every other status is definitive.
    transient_statuses = ("parse_failed", "error", "daily_cap_exceeded")
    if not force:
        existing_logs = CandidateIntakeLog.objects.filter(message_id=message.id)
        finalized = existing_logs.exclude(status__in=transient_statuses).first()
        if finalized:
            return finalized.status  # already finalized in an earlier poll

    cap_status = _daily_candidate_cap_status(mailbox)
    if cap_status:
        return _log(mailbox, message, cap_status)

    attachment = _resume_attachment(message)
    if attachment is None:
        if _has_non_pdf_attachment(message):
            return _log(mailbox, message, "invalid_resume_format")
        return _log(mailbox, message, "no_resume")

    attachment_name = attachment.filename or "resume.pdf"
    if not attachment_name.lower().endswith(".pdf"):
        attachment_name = f"{attachment_name}.pdf"
    resume_bytes = None  # set once decoded; every _log_here() call below

    # persists whatever resume bytes we have at that point, if any, so the
    # resume is reviewable in the grid regardless of how processing ends.
    def _log_here(status, **kwargs):
        return _log(
            mailbox,
            message,
            status,
            resume_content=resume_bytes,
            resume_name=attachment_name,
            **kwargs,
        )

    provider = get_provider(mailbox.provider)
    account = mailbox.get_provider_account()

    try:
        resume_bytes = provider.get_attachment_bytes(account, message, attachment)
    except AttachmentTooLargeError as exc:
        return _log(mailbox, message, "attachment_too_large", error_message=str(exc))
    except Exception as exc:
        logger.exception(
            "Failed to fetch intake attachment for message %s", message.id
        )
        return _log(mailbox, message, "error", error_message=str(exc))

    # Cheap magic-byte check before the (more expensive) PDF parse below -
    # catches a spoofed/mislabeled content-type claim without ever calling
    # into PyMuPDF on non-PDF bytes.
    if not resume_bytes.startswith(b"%PDF-"):
        return _log_here(
            "invalid_resume_format",
            error_message=_("Attachment is not a valid PDF."),
        )

    try:
        resume_text = extract_resume_text(resume_bytes)
        if not resume_text.strip():
            return _log_here(
                "parse_failed",
                error_message=_(
                    "No extractable text in the PDF (likely a scanned image)."
                ),
            )
        open_recruitments = _open_recruitment_options()
        parse_kwargs = dict(
            email_subject=message.subject,
            email_body=message.body_text,
            resume_text=resume_text,
            open_recruitments=open_recruitments,
        )
        parsed = parse_and_match(**parse_kwargs)
        # The local model is non-deterministic on this task: it can return
        # no match on one call and a correct match on an identical retry.
        # Retry once before giving up, but only when there's no fallback
        # recruitment to catch it anyway.
        if not parsed.get("matched_recruitment_id") and not mailbox.fallback_recruitment_id:
            parsed = parse_and_match(**parse_kwargs)
    except IntakeParseError as exc:
        return _log_here("parse_failed", error_message=str(exc))
    except Exception as exc:  # PDF corrupt, etc. - never abort the whole batch
        logger.exception("Unexpected error processing intake message %s", message.id)
        return _log_here("error", error_message=str(exc))

    matched_recruitment = None
    recruitment_id = parsed.get("matched_recruitment_id")
    if recruitment_id:
        matched_recruitment = Recruitment.objects.filter(
            id=recruitment_id, closed=False, is_active=True
        ).first()
    if matched_recruitment is None:
        matched_recruitment = mailbox.fallback_recruitment_id
    if matched_recruitment is None:
        if recruitment_id:
            reason = _(
                "LLM suggested recruitment id {id}, but it isn't open/active."
            ).format(id=recruitment_id)
        else:
            confidence = parsed.get("confidence")
            reason = _(
                "LLM found no matching open recruitment (name: {name}, confidence: {confidence})."
            ).format(name=parsed.get("name") or "?", confidence=confidence)
        return _log_here("no_job_match", error_message=reason)

    stages = matched_recruitment.stage_set.all()
    stage = stages.filter(stage_type="applied").first()
    if stage is None:
        stage = stages.order_by("sequence").first()
    # Candidate.save() only auto-fills job_position_id from the recruitment's
    # own job_position_id field, which can be unset even when open_positions
    # has an entry (real data we hit during testing) - fall back to the
    # first open position in that case too, not just for event-based ones.
    job_position_id = matched_recruitment.job_position_id
    if job_position_id is None:
        job_position_id = matched_recruitment.open_positions.first()

    email_intake_platform, _created = Platform.objects.get_or_create(
        title__iexact="Email Intake", defaults={"title": "Email Intake"}
    )
    candidate_obj = Candidate(
        name=parsed.get("name") or "",
        email=parsed.get("email") or "",
        mobile=parsed.get("mobile") or "",
        recruitment_id=matched_recruitment,
        job_position_id=job_position_id,
        stage_id=stage,
        platform=email_intake_platform,
    )
    candidate_obj.resume.save(
        attachment_name,
        ContentFile(resume_bytes),
        save=False,
    )
    try:
        candidate_obj.full_clean()
        # A nested atomic block so an IntegrityError here (e.g. a race on
        # the email+recruitment unique constraint) only rolls back this
        # save, not any outer transaction a caller might be in - without
        # it, the _log_here() write right after would fail too.
        with transaction.atomic():
            candidate_obj.save()
    except IntegrityError:
        return _log_here("duplicate", matched_recruitment=matched_recruitment)
    except Exception as exc:
        from django.core.exceptions import ValidationError

        error_dict = getattr(exc, "error_dict", {}) if isinstance(exc, ValidationError) else {}
        # Candidate.Meta.unique_together = ("email", "recruitment_id") is a
        # two-field constraint, so Django reports it under "__all__"
        # (NON_FIELD_ERRORS), not under "email".
        if "email" in error_dict or "__all__" in error_dict:
            return _log_here("duplicate", matched_recruitment=matched_recruitment)
        if "job_position_id" in error_dict:
            return _log_here(
                "no_job_match",
                matched_recruitment=matched_recruitment,
                error_message=_("Could not determine a valid job position."),
            )
        logger.exception("Unexpected error creating candidate from intake")
        return _log_here("error", error_message=str(exc))

    return _log_here(
        "created",
        candidate=candidate_obj,
        matched_recruitment=matched_recruitment,
    )


def retry_message(log: CandidateIntakeLog) -> str:
    """
    Explicit user-triggered retry of one already-seen email (e.g. from the
    "Retry Selected" bulk action) - re-fetches it from its provider and
    reprocesses it regardless of its previous status, unlike the automatic
    poll which only retries transient failures.
    """
    mailbox = log.mailbox
    provider = get_provider(mailbox.provider)
    message = provider.get_message(mailbox.get_provider_account(), log.message_id)
    return process_message(mailbox, message, force=True)


def process_mailbox(mailbox: CandidateIntakeMailbox):
    """
    Poll a single mailbox's inbox for new messages and process each one.
    Doesn't advance the checkpoint past any message that only failed
    transiently (LLM timeout/network/daily cap), so it gets retried next
    poll. If the fetch was truncated by INTAKE_MAX_MESSAGES_PER_POLL or the
    poll ran past INTAKE_POLL_MAX_DURATION_SECONDS, the checkpoint only
    advances to the last message actually processed - never to "now" - so
    nothing fetched-but-unprocessed is silently skipped; it's picked up on
    a later poll instead.
    """
    transient_statuses = ("parse_failed", "error", "daily_cap_exceeded")
    since = mailbox.last_polled_at
    provider = get_provider(mailbox.provider)
    account = mailbox.get_provider_account()
    result = provider.list_new_messages(
        account, since=since, max_messages=settings.INTAKE_MAX_MESSAGES_PER_POLL
    )

    started = time_module.time()
    earliest_unresolved = None
    last_processed_at = None
    duration_budget_hit = False

    for message in result.messages:
        if time_module.time() - started > settings.INTAKE_POLL_MAX_DURATION_SECONDS:
            duration_budget_hit = True
            break
        try:
            status = process_message(mailbox, message)
        except Exception:
            logger.exception(
                "Failed to process intake message %s for mailbox %s",
                message.id,
                mailbox,
            )
            status = _log(mailbox, message, "error", error_message="Unhandled exception")
        last_processed_at = message.received_at
        if status in transient_statuses and earliest_unresolved is None:
            earliest_unresolved = message.received_at

    if earliest_unresolved:
        mailbox.last_polled_at = earliest_unresolved - timedelta(seconds=1)
    elif result.truncated or duration_budget_hit:
        mailbox.last_polled_at = last_processed_at or since
    else:
        mailbox.last_polled_at = datetime.now(timezone.utc)
    mailbox.save()


def poll_all_mailboxes():
    """
    Scheduler entrypoint: process every active CandidateIntakeMailbox.
    """
    for mailbox in CandidateIntakeMailbox.objects.filter(is_active=True):
        was_erroring = bool(mailbox.last_error)
        try:
            process_mailbox(mailbox)
        except Exception as exc:
            # Couldn't even list the inbox (expired OAuth token, provider
            # API down, network issue) - surface it on the mailbox itself
            # so it's visible in the UI, not just the server log, and
            # notify admins the first time (not every 5-minute retry while
            # the same problem persists).
            logger.exception("Failed to poll candidate intake mailbox %s", mailbox)
            mailbox.last_error = str(exc)
            mailbox.save(update_fields=["last_error"])
            if not was_erroring:
                _notify_mailbox_managers(
                    mailbox,
                    verb=_("Polling mailbox {mailbox} failed: {error}").format(
                        mailbox=mailbox, error=str(exc)
                    ),
                )
        else:
            if mailbox.last_error:
                mailbox.last_error = ""
                mailbox.save(update_fields=["last_error"])


def _claim_intake_poll_lock(token: str) -> bool:
    """
    Atomic conditional UPDATE - a real DB-level compare-and-swap, correct
    across every app-server process/worker (unlike a LocMemCache lock,
    which only protects within one process). Reclaims a lock abandoned by
    a worker that died mid-poll once it's older than
    INTAKE_POLL_LOCK_STALE_SECONDS (comfortably longer than
    INTAKE_POLL_MAX_DURATION_SECONDS, so this never preempts a
    legitimately still-running poll).
    """
    stale_cutoff = django_timezone.now() - timedelta(
        seconds=settings.INTAKE_POLL_LOCK_STALE_SECONDS
    )
    updated = IntakePollLock.objects.filter(pk=INTAKE_LOCK_PK).filter(
        Q(locked_at__isnull=True) | Q(locked_at__lt=stale_cutoff)
    ).update(locked_at=django_timezone.now(), locked_by=token)
    return updated > 0


def _release_intake_poll_lock(token: str):
    IntakePollLock.objects.filter(pk=INTAKE_LOCK_PK, locked_by=token).update(
        locked_at=None, locked_by=""
    )


def run_guarded_poll() -> bool:
    """
    Runs poll_all_mailboxes() unless one is already in progress anywhere
    (checked atomically via IntakePollLock, shared across every
    app-server process - the scheduler's automatic interval job and any
    manually-triggered run, on any worker, can never overlap). Returns
    True if it ran, False if skipped.
    """
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if not _claim_intake_poll_lock(token):
        return False
    cache.set(POLL_RUNNING_CACHE_KEY, True, timeout=None)
    started = time_module.time()
    error = None
    try:
        poll_all_mailboxes()
    except Exception as exc:
        logger.exception("Candidate intake poll failed")
        error = str(exc)
    finally:
        cache.set(
            POLL_LAST_RESULT_CACHE_KEY,
            {
                "finished_at": django_timezone.now(),
                "duration_seconds": round(time_module.time() - started, 1),
                "error": error,
            },
            timeout=None,
        )
        cache.delete(POLL_RUNNING_CACHE_KEY)
        _release_intake_poll_lock(token)
    return True
