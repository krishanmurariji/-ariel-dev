import threading

from django.db import transaction
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from recruitment.models import (
    Candidate,
    CandidateDocument,
    CandidateDocumentRequest,
    RejectedCandidate,
    Recruitment,
    Stage,
)


DEFAULT_STAGES = [
    ("Applied", "applied"),
    ("Screening", "screening"),
    ("1 Round Interview", "interview"),
    ("2 Round Interview", "interview"),
    ("3 Round Interview", "interview"),
    ("Offered", "offered"),
    ("Hired", "hired"),
    ("Rejected", "cancelled"),
]


@receiver(post_save, sender=Recruitment)
def create_initial_stage(sender, instance, created, **kwargs):
    """
    This is post save method, used to create the default pipeline stages
    for a newly created recruitment.
    """
    if created:
        for sequence, (stage_name, stage_type) in enumerate(DEFAULT_STAGES):
            Stage.objects.create(
                sequence=sequence,
                recruitment_id=instance,
                stage=stage_name,
                stage_type=stage_type,
            )


def _in_background(send, *args, **kwargs):
    """
    Mail never holds up the person who clicked: it is sent from a background thread, so a slow
    or unreachable mail server cannot delay or break saving an application or a rejection.
    """

    def _run():
        from django.db import close_old_connections

        try:
            send(*args, **kwargs)
        finally:
            close_old_connections()

    threading.Thread(target=_run, daemon=True).start()


@receiver(post_save, sender=Candidate)
def send_portal_mail_to_new_candidate(sender, instance, created, **kwargs):
    """
    The built-in "application received" mail with the candidate portal login, for every new
    candidate however they were created (the application form, staff, email intake). It follows
    Settings > Recruitment > "Send Candidate Portal Mail" and is sent once the candidate is
    safely saved. See recruitment/portal_mail.py.
    """
    if not created:
        return
    from recruitment.portal_mail import portal_link, send_portal_mail

    link = portal_link()  # needs the request, which the background thread does not have
    transaction.on_commit(lambda: _in_background(send_portal_mail, instance, link=link))


@receiver(post_save, sender=RejectedCandidate)
def send_rejection_mail_to_candidate(sender, instance, created, **kwargs):
    """
    The built-in "Application Status - Not Selected" mail, sent when a candidate is rejected,
    once the rejection is safely saved. See recruitment/rejection_mail.py.
    """
    if not created:
        return
    from recruitment.rejection_mail import send_rejection_mail

    transaction.on_commit(lambda: _in_background(send_rejection_mail, instance.candidate_id))


@receiver(post_save, sender=Candidate)
def auto_score_new_candidate(sender, instance, created, **kwargs):
    """
    Automatically runs AI scoring, then checks for a better-fitting open
    job, for every newly created candidate that has a resume attached,
    regardless of how it was created (manual form, email intake, etc.) -
    non-blocking, in a background thread, deferred until the creating
    transaction actually commits so the thread is guaranteed to see the
    new row. The job-match check runs after scoring (not in parallel) so
    it can compare against the freshly computed AI score rather than a
    stale/missing one.
    """
    if not created or not instance.resume:
        return

    def _run():
        from django.db import close_old_connections

        from recruitment.ai_job_match import suggest_job_match
        from recruitment.ai_scoring import score_candidate

        try:
            score_candidate(instance)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Automatic AI scoring failed for candidate %s", instance.pk
            )
        try:
            instance.refresh_from_db(fields=["latest_ai_score"])
            suggest_job_match(instance)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Automatic job match check failed for candidate %s", instance.pk
            )
        finally:
            # This thread's DB connection is otherwise only reclaimed
            # whenever the garbage collector gets around to it, not when
            # the thread actually finishes - under a burst of submissions
            # that leaves connections open far longer than needed, which is
            # exactly what leaked past teardown in testing. This is the
            # same call Django itself makes after every request; a
            # manually-managed thread has to make it explicitly.
            close_old_connections()

    transaction.on_commit(lambda: threading.Thread(target=_run, daemon=True).start())


@receiver(m2m_changed, sender=CandidateDocumentRequest.candidate_id.through)
def mail_candidates_when_document_requested(sender, instance, action, reverse, pk_set, **kwargs):
    """
    Mail each candidate a document is newly requested from - whichever screen (onboarding,
    the candidate's profile, the API) made the request. See onboarding/document_mails.py.
    """
    if action != "post_add" or not pk_set:
        return
    from onboarding.document_mails import mail_newly_requested

    transaction.on_commit(lambda: mail_newly_requested(instance, pk_set, reverse))


@receiver(m2m_changed, sender=CandidateDocumentRequest.candidate_id.through)
def document_request_m2m_changed(sender, instance, action, **kwargs):
    if action == "post_add":
        candidate_document_create(instance)

    elif action == "post_remove":
        candidate_document_create(instance)


def candidate_document_create(instance):
    candidates = instance.candidate_id.all()
    for candidate in candidates:
        document, created = CandidateDocument.objects.get_or_create(
            candidate_id=candidate,
            document_request_id=instance,
            defaults={"title": f"Upload {instance.title}"},
        )
        document.title = f"Upload {instance.title}"
        document.save()
