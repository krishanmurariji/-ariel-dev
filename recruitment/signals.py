import threading

from django.db import transaction
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from recruitment.models import (
    Candidate,
    CandidateDocument,
    CandidateDocumentRequest,
    Recruitment,
    Stage,
)


@receiver(post_save, sender=Recruitment)
def create_initial_stage(sender, instance, created, **kwargs):
    """
    This is post save method, used to create initial stage for the recruitment
    """
    if created:
        applied_stage = Stage()
        applied_stage.sequence = 0
        applied_stage.recruitment_id = instance
        applied_stage.stage = "Applied"
        applied_stage.stage_type = "applied"
        applied_stage.save()

        initial_stage = Stage()
        initial_stage.sequence = 1
        initial_stage.recruitment_id = instance
        initial_stage.stage = "Initial"
        initial_stage.stage_type = "initial"
        initial_stage.save()


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
