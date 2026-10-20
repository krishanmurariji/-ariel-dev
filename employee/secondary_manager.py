"""
employee/secondary_manager.py

HR gives an employee any number of second managers and chooses, for each of them and per
kind of request, whether that person may only see the requests or also decide on them.
See horilla.access.manager_access and employee.models.SecondaryManager.
"""

from django import forms
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from employee.models import Employee, SecondaryManager
from horilla.access import ACCESS_LEVELS, APPROVE, REQUEST_TYPES, VIEW
from horilla.decorators import hx_request_required, login_required, permission_required

ACCESS_CHOICES = [
    ("", gettext_lazy("No access")),
    (VIEW, gettext_lazy("View only")),
    (APPROVE, gettext_lazy("Can approve")),
]


class AccessForm(forms.Form):
    """What one second manager may do, one choice per kind of request."""

    def __init__(self, *args, current=None, default=VIEW, **kwargs):
        super().__init__(*args, **kwargs)
        current = current or {}
        for key, label in REQUEST_TYPES:
            self.fields[f"access_{key}"] = forms.ChoiceField(
                label=gettext_lazy(label),
                choices=ACCESS_CHOICES,
                required=False,
                initial=current.get(key, default),
            )

    def access(self):
        """``{"resignation": "approve", ...}`` with only the kinds that were given access."""
        chosen = {}
        for key, _label in REQUEST_TYPES:
            value = self.cleaned_data.get(f"access_{key}")
            if value in ACCESS_LEVELS:
                chosen[key] = value
        return chosen


class AddManagerForm(AccessForm):
    manager = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        error_messages={"required": gettext_lazy("Please choose a manager.")},
    )

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        taken = list(
            SecondaryManager.objects.filter(employee_id=employee).values_list("manager_id", flat=True)
        )
        primary = employee.get_reporting_manager()
        if primary is not None:
            taken.append(primary.pk)
        people = Employee.objects.filter(is_active=True).exclude(pk__in=taken + [employee.pk])
        self.fields["manager"].queryset = people.order_by("employee_first_name", "employee_last_name")
        self.order_fields(["manager"])


def _rows(employee):
    return [
        (link, AccessForm(prefix=f"l{link.pk}", current=link.access, default=""))
        for link in SecondaryManager.objects.filter(employee_id=employee)
        .select_related("manager_id")
        .order_by("pk")
    ]


@login_required
@hx_request_required
@permission_required("employee.change_employeeworkinformation")
def secondary_manager_edit(request, emp_id):
    employee = get_object_or_404(Employee, pk=emp_id)
    notice = error = None
    add_form = AddManagerForm(employee=employee)
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("save", "remove"):
            link = get_object_or_404(
                SecondaryManager.objects.filter(employee_id=employee), pk=request.POST.get("link")
            )
            if action == "remove":
                link.delete()
                notice = _("The manager was removed.")
            else:
                form = AccessForm(request.POST, prefix=f"l{link.pk}")
                if form.is_valid():
                    link.access = form.access()
                    link.save()
                    notice = _("Saved.")
        elif action == "add":
            add_form = AddManagerForm(request.POST, employee=employee)
            if add_form.is_valid():
                SecondaryManager.objects.update_or_create(
                    employee_id=employee,
                    manager_id=add_form.cleaned_data["manager"],
                    defaults={"access": add_form.access()},
                )
                add_form = AddManagerForm(employee=employee)
                notice = _("The manager was added.")
    return render(
        request,
        "employee/secondary_manager_form.html",
        {
            "employee": employee,
            "rows": _rows(employee),
            "add_form": add_form,
            "add_access": [(label, add_form[f"access_{key}"]) for key, label in REQUEST_TYPES],
            "notice": notice,
            "error": error,
        },
    )
