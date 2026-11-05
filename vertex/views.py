"""
vertex/views.py

Foundation pages. Each later phase adds its own views module section.

* Roles - Super Admin / HR Director assign a portal role to each employee
  (capability ``roles.manage``, as in ArielVertex).
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.decorators import login_required
from horilla.post_only import post_only
from vertex.access import capability_required, portal_role
from vertex.models import VertexProfile
from vertex.roles import Cap, PortalRole, capabilities_for


@login_required
@capability_required(Cap.ROLES_MANAGE)
def roles_view(request):
    search = request.GET.get("search", "").strip()
    employees = Employee.objects.filter(is_active=True).select_related(
        "vertex_profile", "employee_work_info__department_id"
    )
    if search:
        from django.db.models import Q

        employees = employees.filter(
            Q(employee_first_name__icontains=search)
            | Q(employee_last_name__icontains=search)
            | Q(badge_id__icontains=search)
        )
    rows = []
    for employee in employees.order_by("employee_first_name", "employee_last_name"):
        profile = getattr(employee, "vertex_profile", None)
        rows.append(
            {
                "employee": employee,
                "role": profile.portal_role if profile else PortalRole.EMPLOYEE,
                "explicit": profile is not None,
            }
        )
    return render(
        request,
        "vertex/roles.html",
        {
            "rows": rows,
            "roles": PortalRole.choices,
            "search": search,
            "my_role": portal_role(request.user),
            "role_capability_counts": {
                value: len(capabilities_for(value)) for value, _label in PortalRole.choices
            },
        },
    )


@login_required
@capability_required(Cap.ROLES_MANAGE)
@post_only
def role_update(request, employee_id):
    employee = get_object_or_404(Employee, pk=employee_id)
    role = request.POST.get("portal_role", "")
    if role not in PortalRole.values:
        messages.error(request, _("Unknown role."))
        return redirect("vertex-roles")
    profile, _created = VertexProfile.objects.get_or_create(employee=employee)
    profile.portal_role = role
    profile.save(update_fields=["portal_role", "updated_at"])
    messages.success(
        request,
        _("%(name)s is now %(role)s.")
        % {"name": employee.get_full_name(), "role": PortalRole(role).label},
    )
    return redirect("vertex-roles")
