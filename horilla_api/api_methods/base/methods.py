from collections import Counter

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.http import QueryDict
from rest_framework.pagination import PageNumberPagination

from employee.models import EmployeeWorkInformation


def get_filter_url(current_url, request):
    url_parts = current_url.split("?")
    base_url = request.path
    query_params = QueryDict(url_parts[1], mutable=True)
    query_params.pop("groupby_field", None)
    return base_url + "?" + query_params.urlencode()


def groupby_queryset(request, url, field_name, queryset):
    queryset_with_counts = queryset.values(field_name)

    counts = Counter(item[field_name] for item in queryset_with_counts)

    result_list = []
    for i in counts:
        result_list.append({field_name: i, "count": counts[i]})

    counts_and_objects = []
    url = get_filter_url(url, request)
    for item in result_list:
        count = item["count"]
        related_fields = field_name.split("__")
        if item[field_name]:
            related_obj = queryset.filter(**{field_name: item[field_name]}).first()
            for field in related_fields:
                related_obj = getattr(related_obj, field)
            counts_and_objects.append(
                {
                    "count": count,
                    "name": str(related_obj),
                    "filter_url": f"{url}&{field_name}={item[field_name]}",
                }
            )
    pagination = PageNumberPagination()
    page = pagination.paginate_queryset(counts_and_objects, request)
    return pagination.get_paginated_response(page)


def permission_based_queryset(user, perm, queryset, user_obj=None):
    # Handle AnonymousUser during schema generation
    if not user.is_authenticated:
        return queryset.none()

    if user.has_perm(perm):
        return queryset

    try:
        queryset.model._meta.get_field("employee_id")
    except FieldDoesNotExist:
        # Written for employee-owned models (leave, attendance, ...); a
        # model with no employee_id field (Candidate, Recruitment, Stage,
        # ...) has no per-employee ownership to fall back to, so deny
        # rather than crash on the filter below with a FieldError.
        return queryset.none()

    employee = user.employee_get
    is_manager = EmployeeWorkInformation.objects.filter(
        reporting_manager_id=employee
    ).exists()
    if is_manager:
        if user_obj:
            return queryset.filter(
                Q(employee_id=employee)
                | Q(employee_id__employee_work_info__reporting_manager_id=employee)
            )
        manager_filter = Q(employee_id=employee)
        subordinates_filter = Q(
            employee_id__employee_work_info__reporting_manager_id=employee
        )
        merged_filter = manager_filter | subordinates_filter
        merged_queryset = queryset.filter(merged_filter)
        return merged_queryset

    return queryset.filter(employee_id=employee)


def owned_object(user, perm, model, pk, owner_field="employee_id", managers=True):
    """
    For models whose owner is not reached through ``employee_id`` (or is a
    many-to-many): the object with this pk if the user holds ``perm`` or is the
    owner, else None. With ``managers=True`` (the default) the owner's
    managers count too, by the same rule as the list pages' filtersubordinates
    (nested while NESTED_SUBORDINATE_VISIBILITY is on). Pass ``managers=False``
    for records whose list shows only your own rows (contracts, reimbursements)
    so a record is never readable by id when the list hides it.
    """
    from horilla.access import current_employee, is_manager_in_chain

    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        return None
    if user.has_perm(perm):
        return obj
    owners = getattr(obj, owner_field)
    if hasattr(owners, "all"):
        owners = owners.all()
    elif owners is not None:
        owners = [owners]
    me = current_employee(user)
    for owner in owners or []:
        if me is not None and owner.pk == me.pk:
            return obj
        if managers and is_manager_in_chain(user, owner):
            return obj
    return None


def can_act_on_employee(user, perm, employee, allow_self=False):
    """
    May ``user`` act (approve, validate, delete...) on a record that belongs to ``employee``?
    Yes for a holder of ``perm`` and for the employee's managers (nested while
    NESTED_SUBORDINATE_VISIBILITY is on); the employee themselves only with ``allow_self``,
    so nobody approves their own request just by being logged in.
    """
    from horilla.access import current_employee, is_manager_in_chain

    if employee is None:
        return False
    if user.has_perm(perm):
        return True
    me = current_employee(user)
    if me is not None and me.pk == employee.pk:
        return allow_self
    return is_manager_in_chain(user, employee)


def permitted_object(user, perm, model, pk):
    """
    Single-record counterpart of permission_based_queryset: the object with
    this pk if the user may see it (same rule as the list endpoints), else
    None. Callers answer None with 404 so a record's existence isn't leaked.
    """
    return (
        permission_based_queryset(user, perm, model.objects.all(), user_obj=True)
        .filter(pk=pk)
        .first()
    )
