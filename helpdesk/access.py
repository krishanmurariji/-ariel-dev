"""
helpdesk/access.py

Who may open a single ticket. This mirrors how ticket_view / ticket_filter
decide which tickets a user sees in the lists, so a ticket reachable from a
list is reachable by its id and a ticket that is not listed for the user is
not either:

* anyone with ``helpdesk.view_ticket``
* the employee who raised it, or the user who created it
* an employee it is assigned to
* the raiser's reporting manager
* the department / job position / individual it was forwarded to
"""

from horilla.access import current_employee, is_reporting_manager_of


def can_view_ticket(user, ticket):
    if user.has_perm("helpdesk.view_ticket"):
        return True
    if ticket.created_by_id is not None and ticket.created_by_id == user.pk:
        return True
    me = current_employee(user)
    if me is None:
        return False
    if ticket.employee_id_id == me.pk:
        return True
    if ticket.assigned_to.filter(pk=me.pk).exists():
        return True
    if is_reporting_manager_of(user, ticket.employee_id):
        return True

    raised_on = str(ticket.raised_on)
    if ticket.assigning_type == "individual":
        return raised_on == str(me.pk)
    work_info = getattr(me, "employee_work_info", None)
    if work_info is None:
        return False
    if ticket.assigning_type == "department":
        return raised_on == str(getattr(work_info, "department_id_id", None))
    if ticket.assigning_type == "job_position":
        return raised_on == str(getattr(work_info, "job_position_id_id", None))
    return False
