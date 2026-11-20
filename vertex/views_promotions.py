"""
vertex/views_promotions.py

The promotion detail page (with the step history) and the training delete action. Lists, the
recommend modal and the step modals are in vertex/cbv_promotions.py.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access
from vertex.models import PromotionStage


@login_required
@permission_required("vertex.view_promotion")
def promotion_detail(request, pk):
    promotion = access.visible_promotions(request.user).filter(pk=pk).first()
    if promotion is None:
        raise Http404
    stages = [s.value for s in (PromotionStage.RECOMMENDED, PromotionStage.HR_VALIDATED, PromotionStage.APPROVED, PromotionStage.COMPLETED)]
    stage_index = stages.index(promotion.stage) if promotion.stage in stages else -1
    return render(
        request,
        "vertex/promotion_detail.html",
        {
            "promotion": promotion,
            "steps": promotion.steps.select_related("actor"),
            "stage_index": stage_index,
            "rejected": promotion.stage == PromotionStage.REJECTED,
            "show_salary": access.can_see_salary(request.user, promotion),
            "can_validate": access.can_validate(request.user, promotion),
            "can_approve": access.can_approve(request.user, promotion),
            "can_complete": access.can_complete(request.user, promotion),
            "can_reject": access.can_reject(request.user, promotion),
            "payroll_note": promotion.stage == PromotionStage.COMPLETED
            and promotion.proposed_salary is not None
            and access.can_validate_promotions(request.user),
        },
    )


@login_required
@permission_required("vertex.delete_trainingrecommendation")
@post_only
def training_delete(request, pk):
    training = access.visible_training(request.user).filter(pk=pk).first()
    if training is None:
        raise Http404
    if not access.can_delete_training(request.user, training):
        return handle_no_permission(request)
    training.delete()
    messages.success(request, _("Training deleted."))
    return redirect("vertex-training")
