"""
horilla/access.py

Object-level access checks for views that take the id of a record belonging to
an employee (a profile tab, a leave request, a document, ...).

``@login_required`` only proves who is asking, not that they may see *this*
record, so a view that fetches by id has to check ownership itself. The rule
used across the app is the same everywhere:

    the employee themselves, their reporting manager, or a user who holds the
    relevant permission.

Views should answer "no access" with a 404 (``Http404``), not a 403, so the
response does not reveal which ids exist.
"""


def current_employee(user):
    """The Employee record of a logged-in user, or None if they have none."""
    try:
        return user.employee_get
    except Exception:
        return None


def is_reporting_manager_of(user, employee):
    """True if ``user`` is the reporting manager of ``employee``."""
    me = current_employee(user)
    if me is None or employee is None:
        return False
    work_info = getattr(employee, "employee_work_info", None)
    manager_id = getattr(work_info, "reporting_manager_id_id", None)
    return manager_id is not None and manager_id == me.pk


def is_manager_in_chain(user, employee, nested=None):
    """
    True if ``user`` manages ``employee`` the way the list pages count it:
    the direct reporting manager and, while NESTED_SUBORDINATE_VISIBILITY is
    on, every manager further up the reporting chain.

    Single-record reads must use this (not ``is_reporting_manager_of``) wherever
    the matching list uses ``filtersubordinates``, otherwise a manager sees a
    row in the list and then gets a 404 when opening it.
    """
    from django.conf import settings

    me = current_employee(user)
    if me is None or employee is None:
        return False
    if nested is None:
        nested = getattr(settings, "NESTED_SUBORDINATE_VISIBILITY", True)
    seen = set()
    current = employee
    # Bounded and cycle-safe: bad data (A manages B, B manages A) must not loop.
    for _ in range(100):
        manager = getattr(
            getattr(current, "employee_work_info", None), "reporting_manager_id", None
        )
        if manager is None or manager.pk in seen:
            return False
        if manager.pk == me.pk:
            return True
        if not nested:
            return False
        seen.add(manager.pk)
        current = manager
    return False


# --------------------------------------------------------------------------
# managers: the reporting manager and an optional second manager
# --------------------------------------------------------------------------
#
# Every employee has a reporting manager, who keeps full access to their requests.
# HR may also give an employee any number of second managers (employee.SecondaryManager) and
# choose, per second manager and kind of request, whether that person may only see the
# requests ("view") or see and decide on them ("approve"). Any of the managers may decide;
# the first decision stands.
#
# Optionally (Settings > Approvals > Manager of Manager) the manager of the reporting manager
# can see - and, if the setting allows it, approve or reject - the requests as well.
#
# To bring another kind of request under this (leave, regularization, ...): add its name to
# REQUEST_TYPES, then use manager_access / managers_for / manager_q where that area now
# checks "is this the reporting manager".

VIEW, APPROVE = "view", "approve"
ACCESS_LEVELS = (VIEW, APPROVE)

REQUEST_TYPES = (("resignation", "Resignation / exit"),)


def _secondary_level(manager, employee, request_type):
    """What ``manager``, if they are one of ``employee``'s second managers, may do for this kind."""
    link = employee.secondary_manager_links.filter(manager_id=manager).first()
    if link is None:
        return None
    level = (link.access or {}).get(request_type)
    return level if level in ACCESS_LEVELS else None


def manager_of_manager_setting():
    """``(enabled, may_approve)`` of the company-wide "manager of manager" setting."""
    from employee.models import ManagerOfManagerSetting

    row = ManagerOfManagerSetting.objects.first()
    enabled = bool(row and row.enabled)
    return enabled, bool(enabled and row.can_approve)


def manager_of_manager(employee):
    """The reporting manager of ``employee``'s reporting manager, or None."""
    primary = getattr(getattr(employee, "employee_work_info", None), "reporting_manager_id", None)
    if primary is None:
        return None
    above = getattr(getattr(primary, "employee_work_info", None), "reporting_manager_id", None)
    # a loop in the hierarchy (A manages B who manages A) must not give anyone access to themselves
    if above is None or above.pk == employee.pk:
        return None
    return above


def _manager_of_manager_level(me, employee):
    """What the manager of manager may do, per the setting: None, ``view`` or ``approve``."""
    enabled, may_approve = manager_of_manager_setting()
    if not enabled:
        return None
    above = manager_of_manager(employee)
    if above is None or above.pk != me.pk:
        return None
    return APPROVE if may_approve else VIEW


def _higher(first, second):
    """The stronger of two access levels (either may be None)."""
    levels = [level for level in (first, second) if level in ACCESS_LEVELS]
    return max(levels, key=ACCESS_LEVELS.index) if levels else None


def manager_access(user, employee, request_type):
    """
    ``"approve"``, ``"view"`` or None: what ``user`` may do with ``employee``'s requests of
    this kind as a manager. The reporting manager always gets ``"approve"``.
    """
    me = current_employee(user)
    if me is None or employee is None:
        return None
    if is_reporting_manager_of(user, employee):
        return APPROVE
    return _higher(
        _secondary_level(me, employee, request_type),
        _manager_of_manager_level(me, employee),
    )


def can_manager_view(user, employee, request_type):
    return manager_access(user, employee, request_type) is not None


def can_manager_approve(user, employee, request_type):
    return manager_access(user, employee, request_type) == APPROVE


def managers_for(employee, request_type, level=VIEW):
    """
    The managers to tell (or to ask) about ``employee``'s request: the reporting manager,
    plus every second manager who has at least ``level`` for this kind of request.
    """
    found = []
    primary = getattr(getattr(employee, "employee_work_info", None), "reporting_manager_id", None)
    if primary is not None:
        found.append(primary)
    for link in employee.secondary_manager_links.select_related("manager_id").order_by("pk"):
        have = (link.access or {}).get(request_type)
        if (
            have in ACCESS_LEVELS
            and ACCESS_LEVELS.index(have) >= ACCESS_LEVELS.index(level)
            and all(link.manager_id_id != m.pk for m in found)
        ):
            found.append(link.manager_id)
    enabled, may_approve = manager_of_manager_setting()
    if enabled and (level == VIEW or may_approve):
        above = manager_of_manager(employee)
        if above is not None and all(above.pk != m.pk for m in found):
            found.append(above)
    return found


def manager_q(me, request_type, level=VIEW, path=""):
    """
    A ``Q`` for "the employees ``me`` manages at this level", for filtering a queryset
    that reaches the employee through ``path`` (e.g. ``"employee_id"``; empty for a
    queryset of employees).
    """
    from django.db.models import Q

    prefix = f"{path}__" if path else ""
    levels = list(ACCESS_LEVELS[ACCESS_LEVELS.index(level):])
    primary = Q(**{f"{prefix}employee_work_info__reporting_manager_id": me})
    # both conditions are on the same link row, because they sit in one filter() call
    secondary = Q(
        **{
            f"{prefix}secondary_manager_links__manager_id": me,
            f"{prefix}secondary_manager_links__access__{request_type}__in": levels,
        }
    )
    condition = primary | secondary
    enabled, may_approve = manager_of_manager_setting()
    if enabled and (level == VIEW or may_approve):
        condition |= Q(
            **{
                f"{prefix}employee_work_info__reporting_manager_id__"
                "employee_work_info__reporting_manager_id": me
            }
        )
    return condition


def can_access_employee(user, employee, perm="employee.view_employee"):
    """
    May ``user`` see or change data that belongs to ``employee``?

    Allowed for: anyone holding ``perm``, the employee themselves, and the
    employee's reporting manager.
    """
    if employee is None:
        return False
    if perm and user.has_perm(perm):
        return True
    me = current_employee(user)
    if me is not None and me.pk == employee.pk:
        return True
    return is_reporting_manager_of(user, employee)


def check_employee_access(user, employee, perm="employee.view_employee", also_allow=()):
    """
    Raise Http404 unless ``user`` may see data belonging to ``employee`` (see
    ``can_access_employee``). ``also_allow`` lists other employees who are
    legitimately involved in the record, e.g. the colleague a shift swap was
    requested from.
    """
    from django.http import Http404

    if can_access_employee(user, employee, perm):
        return
    me = current_employee(user)
    if me is not None and any(e is not None and e.pk == me.pk for e in also_allow):
        return
    raise Http404
