"""
This page handles the cbv methods for resignation tab
"""

from typing import Any

from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _

from employee.cbv.employee_profile import EmployeeProfileView
from employee.models import Employee
from horilla.methods import handle_no_permission
from horilla_views.cbv_methods import check_feature_enabled, login_required
from offboarding.cbv.resignation import ResignationLetterDetailView, ResignationListView
from offboarding.exit_service import _settings_for
from offboarding.models import OffboardingGeneralSetting, ResignationLetter


def may_view_letters_of(request, employee_pk):
    """
    The profile tab is for the employee's own resignation; anyone else needs
    the real view permission (the same one the main Resignations list
    requires). Hiding the tab for other people's profiles isn't enough - the
    URL behind it takes any employee id.
    """
    if request.user.has_perm("offboarding.view_resignationletter"):
        return True
    own = getattr(request.user, "employee_get", None)
    return own is not None and str(own.pk) == str(employee_pk)


def status_hidden_from(request, employee_pk):
    """
    Exit settings' "Show approval status to employees": when it is off, the
    employee does not see where their own request stands. HR (anyone who may
    view resignations) always does.
    """
    if request.user.has_perm("offboarding.view_resignationletter"):
        return False
    employee = Employee.objects.entire().filter(pk=employee_pk).first()
    return employee is not None and not _settings_for(employee).show_approval_status


# @method_decorator(check_feature_enabled("resignation_request", OffboardingGeneralSetting), name="dispatch")
class ResignationTabView(ResignationListView):
    """
    List view of resignation Tab in profile
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.action_method = "tab_actions_column"
        # The profile tab is read-only apart from Withdraw: no edit / delete column.
        self.option_method = ""
        self.bulk_select_option = False
        self.view_id = "resignation-container"
        pk = self.request.resolver_match.kwargs.get("pk")
        if status_hidden_from(self.request, pk):
            self.columns = [c for c in self.columns if c[1] != "get_status"]
        self.search_url = reverse("individual-resignation-tab-list", kwargs={"pk": pk})

    template_name = "cbv/resignation/resignation_tab.html"

    @method_decorator(login_required, name="dispatch")
    @method_decorator(
        check_feature_enabled("resignation_request", OffboardingGeneralSetting),
        name="dispatch",
    )
    def dispatch(self, *args, **kwargs):
        if not may_view_letters_of(self.request, self.kwargs.get("pk")):
            return handle_no_permission(self.request)
        return super(ResignationListView, self).dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs: Any):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        context["employee"] = pk
        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        pk = self.kwargs.get("pk")
        queryset = queryset.filter(employee_id=pk)
        return queryset

    row_attrs = """
                hx-get='{get_detail_tab_url}?instance_ids={ordered_ids}'
                hx-target="#genericModalBody"
                data-target="#genericModal"
                data-toggle="oh-modal-toggle"
                """


class ResignationTabDetailView(ResignationLetterDetailView):
    """
    Detail view of resignation tab in profile
    """

    @method_decorator(login_required, name="dispatch")
    @method_decorator(
        check_feature_enabled("resignation_request", OffboardingGeneralSetting),
        name="dispatch",
    )
    def dispatch(self, *args, **kwargs):
        owner_pk = (
            ResignationLetter.objects.entire()
            .filter(pk=self.kwargs.get("pk"))
            .values_list("employee_id", flat=True)
            .first()
        )
        if owner_pk is not None and not may_view_letters_of(self.request, owner_pk):
            return handle_no_permission(self.request)
        if owner_pk is not None and status_hidden_from(self.request, owner_pk):
            self.body = [row for row in self.body if row[1] != "get_status_badge"]
        return super(ResignationLetterDetailView, self).dispatch(*args, **kwargs)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.action_method = None


EmployeeProfileView.add_tab(
    tabs=[
        {
            "title": _("Resignation"),
            "view": ResignationTabView.as_view(),
            "accessibility": "offboarding.cbv.accessibility.resignation_accessibility",
        },
    ]
)
