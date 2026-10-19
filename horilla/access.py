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
