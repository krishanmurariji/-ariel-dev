"""
vertex/promotions.py

The promotion workflow of ArielVertex (Promotions controller), with its gaps fixed:

    Recommended -> (HR) validated -> (leadership) approved -> (HR) completed        or  rejected

* every stage change is recorded with who made it and the note they wrote (Vertex kept only the last note);
* a person can have only one open promotion (a database rule, so two clicks cannot create two);
* a stage change locks the row and re-checks the stage, so two people or two clicks cannot advance it twice;
* a rejection needs a reason;
* completing a promotion changes the employee's job position in Ariel HR; the salary is NOT changed here
  (Payroll Pro keeps its own append-only pay revisions, which HR records on the Pay Revisions page);
* the employee is told only about a completed promotion, never about a request that is still being decided.
"""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _t

from vertex.models import Promotion, PromotionStage, PromotionStep
from vertex.notify import employees_with_perm, notify_employees


class PromotionError(Exception):
    """A rule of the workflow stopped the action; the message is meant for the person who clicked."""


def _log(promotion, actor, from_stage, to_stage, note):
    PromotionStep.objects.create(
        promotion=promotion, from_stage=from_stage, to_stage=to_stage, actor=actor, note=(note or "").strip()
    )


def _locked(promotion_id, expected):
    promotion = Promotion.objects.select_for_update().select_related("employee", "proposed_position").get(pk=promotion_id)
    if promotion.stage not in expected:
        raise PromotionError(
            _t("This promotion is now '%(stage)s', so that step is no longer possible.")
            % {"stage": promotion.get_stage_display()}
        )
    return promotion


@transaction.atomic
def recommend(promotion, actor):
    """Save a new recommendation (its fields are filled in by the form) and tell HR."""
    employee = promotion.employee
    position = employee.get_job_position()
    promotion.recommended_by = actor
    promotion.current_designation = position.job_position if position else ""
    promotion.stage = PromotionStage.RECOMMENDED
    promotion.save()
    _log(promotion, actor, "", PromotionStage.RECOMMENDED, promotion.justification)
    notify_employees(
        actor, list(employees_with_perm("vertex.change_promotionvalidation")),
        _t("A promotion was recommended for %(name)s. Please validate it.") % {"name": employee.get_full_name()},
        reverse("vertex-promotions"), icon="trending-up-outline",
    )
    return promotion


@transaction.atomic
def validate(promotion_id, actor, note=""):
    promotion = _locked(promotion_id, [PromotionStage.RECOMMENDED])
    promotion.stage = PromotionStage.HR_VALIDATED
    promotion.decision_note = (note or "").strip()
    promotion.save(update_fields=["stage", "decision_note"])
    _log(promotion, actor, PromotionStage.RECOMMENDED, PromotionStage.HR_VALIDATED, note)
    name = promotion.employee.get_full_name()
    notify_employees(
        actor, list(employees_with_perm("vertex.change_promotionapproval")),
        _t("The promotion of %(name)s is validated by HR and waits for your approval.") % {"name": name},
        promotion.get_absolute_url(), icon="trending-up-outline",
    )
    notify_employees(
        actor, [promotion.recommended_by],
        _t("Your promotion recommendation for %(name)s was validated by HR.") % {"name": name},
        promotion.get_absolute_url(), icon="trending-up-outline",
    )
    return promotion


@transaction.atomic
def approve(promotion_id, actor, note=""):
    promotion = _locked(promotion_id, [PromotionStage.HR_VALIDATED])
    promotion.stage = PromotionStage.APPROVED
    promotion.decision_note = (note or "").strip()
    promotion.save(update_fields=["stage", "decision_note"])
    _log(promotion, actor, PromotionStage.HR_VALIDATED, PromotionStage.APPROVED, note)
    name = promotion.employee.get_full_name()
    notify_employees(
        actor, list(employees_with_perm("vertex.change_promotionvalidation")),
        _t("The promotion of %(name)s is approved by leadership. HR can complete it.") % {"name": name},
        promotion.get_absolute_url(), icon="trending-up-outline",
    )
    notify_employees(
        actor, [promotion.recommended_by],
        _t("Leadership approved the promotion of %(name)s.") % {"name": name},
        promotion.get_absolute_url(), icon="trending-up-outline",
    )
    return promotion


@transaction.atomic
def complete(promotion_id, actor, note=""):
    """Apply the new designation to the employee and tell them (the first time they hear of it)."""
    promotion = _locked(promotion_id, [PromotionStage.APPROVED])
    work = getattr(promotion.employee, "employee_work_info", None)
    if work is None:
        raise PromotionError(_t("This employee has no work information yet, so the designation cannot be changed."))
    work.job_position_id = promotion.proposed_position
    work.save()
    promotion.stage = PromotionStage.COMPLETED
    promotion.completed_at = timezone.now()
    if (note or "").strip():
        promotion.decision_note = note.strip()
    promotion.save(update_fields=["stage", "completed_at", "decision_note"])
    _log(promotion, actor, PromotionStage.APPROVED, PromotionStage.COMPLETED, note)
    notify_employees(
        actor, [promotion.employee],
        _t("Congratulations! You are now %(position)s.") % {"position": promotion.proposed_position.job_position},
        reverse("vertex-my-performance"), icon="trending-up-outline",
    )
    notify_employees(
        actor, [promotion.recommended_by],
        _t("The promotion of %(name)s is completed.") % {"name": promotion.employee.get_full_name()},
        promotion.get_absolute_url(), icon="trending-up-outline",
    )
    return promotion


@transaction.atomic
def reject(promotion_id, actor, note):
    if not (note or "").strip():
        raise PromotionError(_t("Say why the promotion is not going ahead."))
    promotion = _locked(
        promotion_id, [PromotionStage.RECOMMENDED, PromotionStage.HR_VALIDATED, PromotionStage.APPROVED]
    )
    previous = promotion.stage
    promotion.stage = PromotionStage.REJECTED
    promotion.decision_note = note.strip()
    promotion.save(update_fields=["stage", "decision_note"])
    _log(promotion, actor, previous, PromotionStage.REJECTED, note)
    # only the recommender is told: the employee never heard about a request that did not go ahead
    notify_employees(
        actor, [promotion.recommended_by],
        _t("The promotion of %(name)s was not approved: %(note)s")
        % {"name": promotion.employee.get_full_name(), "note": note.strip()},
        promotion.get_absolute_url(), icon="trending-up-outline",
    )
    return promotion
