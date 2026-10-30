import logging
import os
from functools import wraps
from urllib.parse import urlencode

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.datastructures import MultiValueDictKeyError
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from horilla import settings
from horilla.http import HorillaRedirect
from horilla.methods import handle_no_permission
from horilla.settings import BASE_DIR, TEMPLATES

logger = logging.getLogger(__name__)

TEMPLATES[0]["DIRS"] = [os.path.join(BASE_DIR, "templates")]

decorator_with_arguments = (
    lambda decorator: lambda *args, **kwargs: lambda func: decorator(
        func, *args, **kwargs
    )
)


def check_manager(employee, instance):
    from employee.models import Employee

    try:
        if isinstance(instance, Employee):
            return instance.employee_work_info.reporting_manager_id == employee
        return employee == instance.employee_id.employee_work_info.reporting_manager_id
    except:
        return False


@decorator_with_arguments
def permission_required(function, perm):
    def _function(request, *args, **kwargs):
        if request.user.has_perm(perm):
            return function(request, *args, **kwargs)

        return handle_no_permission(request)

    # Accumulate perms so login_required's @wraps propagates them automatically.
    _function._required_perms = getattr(function, "_required_perms", []) + [perm]
    return _function


@decorator_with_arguments
def any_permission_required(function, perms):
    def _function(request, *args, **kwargs):
        if any(request.user.has_perm(perm) for perm in perms):
            return function(request, *args, **kwargs)

        return handle_no_permission(request)

    return _function


decorator_with_arguments = (
    lambda decorator: lambda *args, **kwargs: lambda func: decorator(
        func, *args, **kwargs
    )
)


def _employee_of_record(record):
    """The Employee a record belongs to (the record itself if it is one), or None."""
    from employee.models import Employee

    if isinstance(record, Employee):
        return record
    employee = getattr(record, "employee_id", None)
    return employee if isinstance(employee, Employee) else None


def _find_record(model, record_id):
    """The row with this id, ignoring the selected-company filter; None if there is none."""
    try:
        return model._base_manager.filter(pk=record_id).first()
    except (TypeError, ValueError, OverflowError):
        return None


def employee_of(model, kwarg="obj_id"):
    """
    For ``manager_can_enter(target=...)``: the view's ``kwarg`` is the id of a ``model`` row
    (``"app_label.ModelName"``), and that row belongs to an employee.
    """

    def resolve(request, kwargs):
        record = _find_record(apps.get_model(model), kwargs.get(kwarg))
        return record, _employee_of_record(record)

    return resolve


def employee_by_id(kwarg="obj_id"):
    """For ``manager_can_enter(target=...)``: the view's ``kwarg`` is an Employee id."""

    def resolve(request, kwargs):
        record = _find_record(apps.get_model("employee.Employee"), kwargs.get(kwarg))
        return record, record

    return resolve


MAX_BULK_IDS = 5000


def employee_from(*resolvers):
    """For ``manager_can_enter(target=...)``: the first resolver whose record exists decides."""

    def resolve(request, kwargs):
        for resolver in resolvers:
            record, owner = resolver(request, kwargs)
            if record is not None:
                return record, owner
        return None, None

    return resolve


def employees_of_ids(model, key="ids"):
    """
    For ``manager_can_enter(bulk=...)``: the request carries ``model`` ids under ``key``. Every
    value in POST *and* GET is read, as a JSON list or as single ids, so repeating the parameter
    cannot hide a row from this check that the view will still act on. Returns one
    ``(record, employee)`` pair per row that exists; anything that cannot be understood as an
    id (or too many ids) adds a pair with no owner, which only a permission holder gets past.
    """
    import json

    def resolve(request, kwargs):
        unreadable = False
        ids = []
        for value in request.POST.getlist(key) + request.GET.getlist(key):
            text = str(value).strip()
            if text.startswith("["):
                try:
                    items = json.loads(text)
                except ValueError:
                    unreadable = True
                    continue
                if not isinstance(items, list):
                    unreadable = True
                    continue
                ids.extend(items)
            else:
                ids.append(text)
        if len(ids) > MAX_BULK_IDS:
            return [(True, None)]
        model_class = apps.get_model(model)
        numbers = []
        for value in ids:
            if isinstance(value, (bool, float, dict, list)):
                unreadable = True
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                unreadable = True
                continue
            if abs(number) < 2**62:  # a larger number is no row's id
                numbers.append(number)
        # one query for the whole list (with the owner), not one per id
        rows = model_class._base_manager.filter(pk__in=numbers)
        try:
            if model_class._meta.get_field("employee_id").is_relation:
                rows = rows.select_related("employee_id")
        except FieldDoesNotExist:
            pass
        pairs = [(record, _employee_of_record(record)) for record in rows]
        if unreadable:
            pairs.append((True, None))
        return pairs

    return resolve


def _manages(user, employee):
    """True if ``user`` manages ``employee`` the way the list pages count it."""
    from horilla.access import is_manager_in_chain

    return is_manager_in_chain(user, employee)


@decorator_with_arguments
def delete_permission(function):
    def _function(request, *args, **kwargs):
        user = request.user
        record = _find_record(kwargs["model"], kwargs.get("obj_id"))
        employee = _employee_of_record(record)
        # a manager may act only on records of the people they manage
        is_manager = employee is not None and _manages(user, employee)
        if (
            request.user.has_perm(
                kwargs["model"]._meta.app_label
                + ".delete_"
                + kwargs["model"]._meta.model_name
            )
            or is_manager
        ):
            return function(request, *args, **kwargs)

        return handle_no_permission(
            request, message=_("You dont have permission for delete.")
        )

    return _function


decorator_with_arguments = (
    lambda decorator: lambda *args, **kwargs: lambda func: decorator(
        func, *args, **kwargs
    )
)


@decorator_with_arguments
def duplicate_permission(function):
    def _function(request, *args, **kwargs):
        user = request.user
        employee = user.employee_get
        record = _find_record(kwargs["model"], kwargs.get("obj_id"))
        record_employee = _employee_of_record(record)
        is_manager = record_employee is not None and _manages(user, record_employee)

        app_label = kwargs["model"]._meta.app_label
        model_name = kwargs["model"]._meta.model_name
        try:
            obj_id = kwargs["obj_id"]
            object_instance = kwargs["model"].objects.filter(pk=obj_id).first()
            if object_instance.employee_id == employee:
                return function(request, *args, **kwargs)
        except:
            pass
        permission = f"{app_label}.add_{model_name}"
        if request.user.has_perm(permission) or is_manager:
            return function(request, *args, **kwargs)

        return handle_no_permission(
            request, message=_("You dont have permission for duplicate action.")
        )

    return _function


decorator_with_arguments = (
    lambda decorator: lambda *args, **kwargs: lambda func: decorator(
        func, *args, **kwargs
    )
)


def manager_may_enter(request, perm, pairs):
    """
    Whether the signed-in user may run a view that needs ``perm`` and acts on ``pairs``
    (``(record, employee)`` rows, see ``employee_of`` / ``employees_of_ids``): anyone holding
    the permission; otherwise a manager of every one of those employees. With no rows to look
    at (list pages, forms that create), anyone who manages somebody.
    """
    from base.models import MultipleApprovalManagers
    from employee.models import EmployeeWorkInformation

    leave_perm = [
        "leave.view_leaverequest",
        "leave.change_leaverequest",
        "leave.delete_leaverequest",
    ]
    user = request.user
    employee = user.employee_get
    # a row that does not exist is left to the view to report
    pairs = [(record, owner) for record, owner in pairs if record is not None]

    def names_approver(record):
        approvals = getattr(record, "multiple_approvals", None)
        approvals = approvals() if callable(approvals) else None
        return bool(approvals and employee in approvals["managers"])

    if perm in leave_perm:
        is_approval_manager = MultipleApprovalManagers.objects.filter(
            employee_id=employee.id
        ).exists()
        if is_approval_manager:
            if not pairs:
                return True
            # only for the requests that name them as an approver
            if all(names_approver(record) for record, _owner in pairs):
                return True
    if user.has_perm(perm):
        return True
    if pairs:
        managed = {}

        def manages(owner):
            if owner is None:
                return False
            if owner.pk not in managed:
                managed[owner.pk] = _manages(user, owner)
            return managed[owner.pk]

        return all(manages(owner) for _record, owner in pairs)
    return EmployeeWorkInformation.objects.filter(reporting_manager_id=employee).exists()


@decorator_with_arguments
def manager_can_enter(function, perm, target=None, bulk=None):
    """
    Lets in users who have ``perm`` and, otherwise, managers.

    Pass ``target`` (``employee_of`` / ``employee_by_id``) whenever the view acts on one employee's
    record, or ``bulk`` (``employees_of_ids``) when it acts on a list of them: a manager then gets in
    only if they manage every one of those people. Without either, anyone who manages somebody
    gets in, as before.
    """

    @wraps(function)
    def _function(request, *args, **kwargs):
        pairs = []
        if target is not None:
            pairs = [target(request, kwargs)]
        elif bulk is not None:
            pairs = bulk(request, kwargs)
        if manager_may_enter(request, perm, pairs):
            return function(request, *args, **kwargs)

        return handle_no_permission(request)

    return _function


@decorator_with_arguments
def is_recruitment_manager(function, perm):
    from recruitment.models import Recruitment

    """
    This method is used to check permission to employee for enter to the function if the employee
    do not have permission also checks, has manager of any recruitment.
    """

    def _function(request, *args, **kwargs):

        user = request.user
        perm = "recruitment.view_recruitmentsurvey"
        is_manager = False
        recs = Recruitment.objects.all()
        for i in recs:
            for manager in i.recruitment_managers.all():
                if request.user.employee_get == manager:
                    is_manager = True

        if user.has_perm(perm) or is_manager:
            return function(request, *args, **kwargs)

        return handle_no_permission(request)

    return _function


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        path = request.path
        res = path.split("/", 2)[1].capitalize().replace("-", " ").upper()
        if res == "PMS":
            res = "Performance"
        request.session["title"] = res
        if path == "" or path == "/":
            request.session["title"] = "Dashboard".upper()

        login_url = reverse("login")
        try:
            query_string = urlencode(request.GET)
        except:
            query_string = None
        redirect_url = f"{login_url}?next={request.path}"
        if query_string:
            redirect_url += f"&{query_string}"

        employee = getattr(request.user, "employee_get", None)

        if (
            not request.user.is_authenticated
            or not request.user.is_active
            or not employee
            or not employee.is_active
        ):
            if request.headers.get("HX-Request"):
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
            return redirect(redirect_url)
        try:
            func = view_func(request, *args, **kwargs)
        except KeyError:
            raise
        except Exception as e:
            logger.error(e)
            if (
                "notifications_notification" in str(e)
                and request.headers.get("X-Requested-With") != "XMLHttpRequest"
            ):
                messages.warning(request, str(e))
                referer = request.META.get("HTTP_REFERER", "/")
                # Prevent open redirect + XSS
                if not url_has_allowed_host_and_scheme(
                    referer,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    referer = "/"

                return redirect(referer)

            if not settings.DEBUG:
                messages.error(request, str(e))
                return render(request, "went_wrong.html", status=404)
            raise e
        return func

    return wrapped_view


def hx_request_required(view_func):
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        key = "HTTP_HX_REQUEST"
        if key not in request.META.keys():
            return render(request, "405.html", status=405)
        return view_func(request, *args, **kwargs)

    return wrapped_view


@decorator_with_arguments
def owner_can_enter(
    function,
    perm: str,
    model: object,
    manager_access=False,
    employee_field="employee_id",
):
    from employee.models import Employee, EmployeeWorkInformation

    """
    Only the users with permission, or the owner, or employees manager can enter,
    If manager_access:True then all the managers can enter
    """

    def _function(request, *args, **kwargs):
        if kwargs:
            instance_id = kwargs[list(kwargs.keys())[0]]
        else:
            instance_id = request.GET.get("employee_id") or request.POST.get(
                "employee_id"
            )
        if model == Employee:
            employee = Employee.objects.filter(id=instance_id).first()
        else:
            try:
                obj = model.objects.filter(id=instance_id).first()
                employee = getattr(obj, employee_field, None) if obj else None
            except:
                messages.error(request, ("Sorry, something went wrong!"))
                return HorillaRedirect(request)
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
        )
        if can_enter or not employee:
            return function(request, *args, **kwargs)
        return render(request, "no_perm.html")

    return _function


def install_required(function):
    from base.models import BiometricAttendance, Company, TrackLateComeEarlyOut

    def _function(request, *args, **kwargs):
        if request.path_info.endswith("late-come-early-out-view/"):
            selected_company = request.session.get("selected_company")
            if selected_company == "all":
                company = None
            else:
                company = Company.objects.filter(id=selected_company).first()

            object, created = TrackLateComeEarlyOut.objects.get_or_create(
                company_id=company
            )
            if not object or object.is_enable:
                return function(request, *args, **kwargs)
            else:
                messages.info(
                    request,
                    _("Please enable the Track Late Come & Early Out from settings"),
                )
                return HorillaRedirect(request)
        selected_company = request.session.get("selected_company")
        if selected_company == "all":
            biometric_company = None
        else:
            biometric_company = Company.objects.filter(id=selected_company).first()
        object = BiometricAttendance.objects.filter(
            company_id=biometric_company
        ).first()
        if not object or object.is_installed:
            return function(request, *args, **kwargs)
        else:
            messages.info(
                request,
                _(
                    "Please activate the biometric attendance feature in the settings menu."
                ),
            )
            return HorillaRedirect(request)

    return _function


@decorator_with_arguments
def meeting_manager_can_enter(function, perm, answerable=False):
    from employee.models import Employee

    def _function(request, *args, **kwargs):

        user = request.user
        employee = user.employee_get
        is_answer_employee = False

        is_manager = (
            Employee.objects.filter(
                meeting_manager__isnull=False,
            )
            .filter(id=employee.id)
            .exists()
        )

        if answerable:
            is_answer_employee = (
                Employee.objects.filter(
                    meeting_answer_employees__isnull=False,
                )
                .filter(id=employee.id)
                .exists()
            )

        if user.has_perm(perm) or is_manager or is_answer_employee:
            return function(request, *args, **kwargs)

        return handle_no_permission(request)

    return _function


DECORATOR_MAP = {
    "login_required": login_required,
    "permission_required": permission_required,
    "delete_permission": delete_permission,
    "duplicate_permission": duplicate_permission,
    "manager_can_enter": manager_can_enter,
    "is_recruitment_manager": is_recruitment_manager,
    "hx_request_required": hx_request_required,
    "owner_can_enter": owner_can_enter,
    "install_required": install_required,
    "meeting_manager_can_enter": meeting_manager_can_enter,
}


def get_decorator(decorator_string, args=None):
    decorator = DECORATOR_MAP.get(decorator_string)
    if decorator:
        if args is not None:
            if isinstance(args, (list, tuple)):
                return decorator(*args)
            else:
                return decorator(args)
        else:
            return decorator
    return None


def apply_decorators(decorators):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            decorated_func = func
            for decorator_item in decorators:
                if isinstance(decorator_item, str):
                    decorator = get_decorator(decorator_item)
                elif (
                    isinstance(decorator_item, (list, tuple))
                    and len(decorator_item) == 2
                ):
                    decorator_string, decorator_args = decorator_item
                    decorator = get_decorator(decorator_string, decorator_args)
                else:
                    print(f"Warning: Invalid decorator format: {decorator_item}")
                    continue

                if decorator:
                    if callable(decorator):
                        decorated_func = decorator(decorated_func)
                    else:
                        # For decorators returned by decorator_with_arguments
                        decorated_func = decorator(decorated_func)
                else:
                    print(f"Warning: Decorator '{decorator_item}' not found or invalid")
            return decorated_func(*args, **kwargs)

        return wrapper

    return decorator


@decorator_with_arguments
def check_integration_enabled(func, app_name):
    """
    Decorator to check if the integration app is installed and enabled.
    """
    from base.models import IntegrationApps

    @wraps(func)
    def wrapper(request=None, *args, **kwargs):
        if not IntegrationApps.objects.filter(
            app_label=app_name, is_enabled=True
        ).exists():
            if request:
                try:
                    app_config = apps.get_app_config(app_name)
                    app_verbose_name = app_config.verbose_name
                except LookupError:
                    app_verbose_name = app_name

                return handle_no_permission(
                    request, message=f"Access to '{app_verbose_name}' is disabled."
                )

            return None

        return func(request, *args, **kwargs)

    return wrapper
