from django.contrib import messages
from django.shortcuts import render

from base.methods import check_manager
from helpdesk.models import Ticket
from horilla.http.response import HorillaRedirect

decorator_with_arguments = (
    lambda decorator: lambda *args, **kwargs: lambda func: decorator(
        func, *args, **kwargs
    )
)


@decorator_with_arguments
def ticket_owner_can_enter(function, perm: str, model: object, manager_access=False):
    from employee.models import Employee, EmployeeWorkInformation

    """
    Only the users with permission, or the owner, or employees manager can enter,
    If manager_access:True then all the managers can enter
    """

    def _function(request, *args, **kwargs):
        instance_id = kwargs[list(kwargs.keys())[0]]
        if model == Employee:
            employee = Employee.objects.get(id=instance_id)
            instance = None
        else:
            try:
                instance = model.objects.get(id=instance_id)
                employee = instance.employee_id
            except:
                messages.error(request, ("Sorry, something went wrong!"))
                return HorillaRedirect(request)
        # Only this ticket counts: the assignee/creator checks used to be
        # unscoped querysets, true for anyone who had ever touched any ticket.
        is_ticket_party = (
            isinstance(instance, Ticket)
            and (
                instance.assigned_to.filter(pk=request.user.employee_get.pk).exists()
                or instance.created_by_id == request.user.pk
            )
        )
        can_enter = (
            request.user.employee_get == employee
            or request.user.has_perm(perm)
            or check_manager(request.user.employee_get, employee)
            or (
                EmployeeWorkInformation.objects.filter(
                    reporting_manager_id__employee_user_id=request.user
                ).exists()
                if manager_access
                else False
            )
            or is_ticket_party
        )
        if can_enter:
            return function(request, *args, **kwargs)
        return render(request, "no_perm.html")

    return _function
