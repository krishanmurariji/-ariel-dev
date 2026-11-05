"""
vertex/access.py

Resolve a request user to a portal role and check capabilities.

* Django superusers act as Super Admin.
* A user with no VertexProfile (or no employee) acts as a plain Employee.
"""

from functools import wraps

from horilla.methods import handle_no_permission
from vertex.roles import PortalRole, capabilities_for


def portal_role(user):
    if not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser:
        return PortalRole.SUPER_ADMIN
    employee = getattr(user, "employee_get", None)
    if employee is None:
        return PortalRole.EMPLOYEE
    profile = getattr(employee, "vertex_profile", None)
    return profile.portal_role if profile else PortalRole.EMPLOYEE


def has_cap(user, capability) -> bool:
    role = portal_role(user)
    return role is not None and capability in capabilities_for(role)


def capability_required(capability):
    """View decorator: no-permission response unless the portal role grants ``capability``."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not has_cap(request.user, capability):
                return handle_no_permission(request)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
