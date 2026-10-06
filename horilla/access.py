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
