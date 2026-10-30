"""
decorator functions for base
"""

from django.contrib import messages

from employee.models import EmployeeWorkInformation
from horilla.horilla_middlewares import _thread_locals
from horilla.http.response import HorillaRedirect
from horilla.methods import handle_no_permission

from .models import MultipleApprovalManagers, ShiftRequest, WorkTypeRequest

decorator_with_arguments = (
    lambda decorator: lambda *args, **kwargs: lambda func: decorator(
        func, *args, **kwargs
    )
)


@decorator_with_arguments
def shift_request_change_permission(function=None, *args, **kwargs):
    def check_permission(
        request,
        shift_request_id=None,
        *args,
        **kwargs,
    ):
        """
        This method is used to check the employee can change a shift request or not
        """
        shift_request = ShiftRequest.objects.get(id=shift_request_id)
        if (
            request.user.has_perm("base.change_shiftrequest")
            or request.user.employee_get
            == shift_request.employee_id.employee_work_info.reporting_manager_id
            or request.user.employee_get == shift_request.employee_id
        ):
            return function(request, *args, shift_request_id=shift_request_id, **kwargs)
        messages.info(request, "You dont have permission.")
        return HorillaRedirect(request)
        # return function(request, *args, **kwargs)

    return check_permission


@decorator_with_arguments
def work_type_request_change_permission(function=None, *args, **kwargs):
    def check_permission(
        request,
        work_type_request_id=None,
        *args,
        **kwargs,
    ):
        """
        This method is used to check the employee can change a shift request or not
        """
        work_type_request = WorkTypeRequest.objects.get(id=work_type_request_id)
        if (
            request.user.has_perm("base.change_worktyperequest")
            or request.user.employee_get
            == work_type_request.employee_id.employee_work_info.reporting_manager_id
            or request.user.employee_get == work_type_request.employee_id
        ):
            return function(
                request, *args, work_type_request_id=work_type_request_id, **kwargs
            )
        messages.info(request, "You dont have permission.")
        return HorillaRedirect(request)
        # return function(request, *args, **kwargs)

    return check_permission


@decorator_with_arguments
def manager_can_enter(function, perm, target=None, bulk=None):
    """
    Class-based-view version of horilla.decorators.manager_can_enter: users who have ``perm``
    get in; otherwise a manager does - of the people the record (``target``) or the list of
    records (``bulk``) belongs to, or of anybody when the view names no record.
    """

    def _function(self, *args, **kwargs):
        from horilla.decorators import manager_may_enter

        request = getattr(_thread_locals, "request")
        if not getattr(self, "request", None):
            self.request = request
        pairs = []
        if target is not None:
            pairs = [target(request, kwargs)]
        elif bulk is not None:
            pairs = bulk(request, kwargs)
        if manager_may_enter(request, perm, pairs):
            return function(self, *args, **kwargs)

        return handle_no_permission(request)

    return _function
