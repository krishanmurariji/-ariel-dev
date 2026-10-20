"""
employee/manager_of_manager.py

Settings > Approvals > Manager of Manager: whether the manager of an employee's reporting
manager can see that employee's requests, and whether they may approve / reject them too.
The rule itself lives in horilla.access (manager_of_manager_setting and manager_access).
"""

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from employee.models import ManagerOfManagerSetting
from horilla.decorators import hx_request_required, login_required, permission_required

PERMISSION = "employee.change_employeeworkinformation"


@login_required
@permission_required(PERMISSION)
def manager_of_manager_settings(request):
    return render(
        request,
        "employee/settings/manager_of_manager.html",
        {"setting": ManagerOfManagerSetting.current()},
    )


@login_required
@hx_request_required
@permission_required(PERMISSION)
@require_POST
def manager_of_manager_toggle(request):
    """Switch "enabled" or "can approve" on or off; the page reloads to show the result."""
    field = request.POST.get("field")
    if field not in ("enabled", "can_approve"):
        return HttpResponseBadRequest()
    row = ManagerOfManagerSetting.objects.first() or ManagerOfManagerSetting()
    value = request.POST.get("value") == "true"
    setattr(row, field, value)
    row.save()
    if field == "enabled":
        messages.success(
            request,
            _("Manager of manager access is on.") if value else _("Manager of manager access is off."),
        )
    else:
        messages.success(
            request,
            _("The manager of manager can now approve and reject.")
            if value
            else _("The manager of manager can now only view."),
        )
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response
