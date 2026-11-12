"""
vertex/views_appraisals.py

The appraisal detail page and the POST actions of the appraisal module: activate / close a cycle and
delete a goal. Lists and modal forms are in vertex/cbv_appraisals.py.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access, appraisals
from vertex.models import AppraisalCycle, AppraisalStage


@login_required
@permission_required("vertex.view_appraisal")
def appraisal_detail(request, pk):
    appraisal = access.visible_appraisals(request.user).filter(pk=pk).first()
    if appraisal is None:
        raise Http404
    released = appraisal.stage == AppraisalStage.RELEASED
    stage_index = [s.value for s in AppraisalStage].index(appraisal.stage)
    return render(
        request,
        "vertex/appraisal_detail.html",
        {
            "appraisal": appraisal,
            "stage_index": stage_index,
            "released": released,
            "show_manager": access.can_see_manager_part(request.user, appraisal),
            "can_self": access.can_self_assess(request.user, appraisal),
            "can_evaluate": access.can_evaluate(request.user, appraisal),
            "can_release": access.can_release(request.user, appraisal),
            "goals": access.visible_goals(request.user).filter(
                employee=appraisal.employee, cycle=appraisal.cycle
            ),
        },
    )


@login_required
@permission_required("vertex.change_appraisalcycle")
@post_only
def cycle_activate(request, pk):
    cycle = AppraisalCycle.objects.filter(pk=pk).first()
    if cycle is None:
        raise Http404
    try:
        created = appraisals.activate_cycle(cycle, access.employee_of(request.user))
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("vertex-cycles")
    messages.success(
        request,
        _("Cycle opened. %(count)s appraisals were created.") % {"count": len(created)},
    )
    return redirect("vertex-cycles")


@login_required
@permission_required("vertex.change_appraisalcycle")
@post_only
def cycle_close(request, pk):
    cycle = AppraisalCycle.objects.filter(pk=pk).first()
    if cycle is None:
        raise Http404
    try:
        appraisals.close_cycle(cycle)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("vertex-cycles")
    messages.success(request, _("Cycle closed."))
    return redirect("vertex-cycles")


@login_required
@permission_required("vertex.delete_goal")
@post_only
def goal_delete(request, pk):
    goal = access.visible_goals(request.user).filter(pk=pk).first()
    if goal is None:
        raise Http404
    if not access.can_delete_goal(request.user, goal):
        return handle_no_permission(request)
    goal.delete()
    messages.success(request, _("Goal deleted."))
    return redirect("vertex-goals")
