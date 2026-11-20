"""
vertex/cbv_expenses.py

Expenses on Ariel HR's list / filter / modal framework. The detail page, invoice download, the
"mark paid" action and the settings page are in vertex/views_expenses.py; the rules of each step
are in vertex/expenses.py.
"""

from typing import Any

from django.contrib import messages
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from horilla.methods import handle_no_permission
from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from vertex import access, expenses
from vertex.cbv import RELOAD_PAGE, VertexModal, _filter_indications
from vertex.cbv_appraisals import _create_attrs, _modal_attrs, _post_attrs
from vertex.filters import ExpenseFilter
from vertex.forms_expenses import (
    DecisionForm,
    ExpenseForm,
    InvoiceForm,
    RejectForm,
    SettingsForm,
)
from vertex.models import Expense, ExpenseSetting, ExpenseStatus


def can_approve_row(request, instance, *args, **kwargs):
    return access.can_approve_expense(request.user, instance)


def can_pay_row(request, instance, *args, **kwargs):
    return access.can_pay_expense(request.user, instance)


def can_upload_row(request, instance, *args, **kwargs):
    return access.can_upload_invoice(request.user, instance)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_expense"), name="dispatch")
class ExpensesView(TemplateView):
    template_name = "vertex/cbv/expenses.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["summary"] = access.expense_summary(self.request.user)
        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_expense"), name="dispatch")
class ExpensesNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = ExpenseFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status", "category", "payment_method"]
    filter_body_template = "vertex/cbv/expense_filter.html"
    hide_actions_button = True
    create_label = _("Raise expense")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-expense-list")
        if access.can_raise_expense(self.request.user):
            self.create_attrs = _create_attrs("vertex-expense-new")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_expense"), name="dispatch")
class ExpensesList(HorillaListView):
    model = Expense
    filter_class = ExpenseFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_expenses(self.request.user).values("pk")).select_related(
            "raised_by", "approver"
        )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-expense-list")

    @cached_property
    def columns(self):
        return [
            (_("Expense"), "title"),
            (_("Category"), "get_category_display"),
            (_("Amount"), "get_amount_display"),
            (_("Vendor"), "vendor"),
            (_("Date"), "expense_date"),
            (_("Status"), "get_status_html"),
            (_("Approval"), "get_approval_display"),
            (_("Raised by"), "get_raiser_name"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Date"), "expense_date"), (_("Amount"), "amount"), (_("Created"), "created_at")]

    row_status_indications = _filter_indications("status", ExpenseStatus.choices)
    row_status_class = "status-{status}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Upload invoice"),
            "icon": "document-attach-outline",
            "accessibility": "vertex.cbv_expenses.can_upload_row",
            "attrs": _modal_attrs("{get_invoice_url}"),
        },
        {
            "action": _("Approve"),
            "icon": "checkmark-done-outline",
            "accessibility": "vertex.cbv_expenses.can_approve_row",
            "attrs": _modal_attrs("{get_approve_url}"),
        },
        {
            "action": _("Reject"),
            "icon": "close-circle-outline",
            "accessibility": "vertex.cbv_expenses.can_approve_row",
            "attrs": _modal_attrs("{get_reject_url}"),
        },
        {
            "action": _("Mark paid"),
            "icon": "cash-outline",
            "accessibility": "vertex.cbv_expenses.can_pay_row",
            "attrs": _post_attrs("{get_pay_url}", "Mark this expense as paid?"),
        },
    ]


@method_decorator(login_required, name="dispatch")
class ExpenseFormView(VertexModal, HorillaFormView):
    """Modal: raise a payment request (the invoice can be added here or later)."""

    model = Expense
    form_class = ExpenseForm
    new_display_title = _("Raise expense")

    def dispatch(self, request, *args, **kwargs):
        if not access.can_raise_expense(request.user):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return None  # always a new request

    def form_valid(self, form):
        expenses.raise_expense(
            form.save(commit=False), access.employee_of(self.request.user), form.cleaned_data.get("invoice_file")
        )
        messages.success(self.request, _("Expense raised."))
        return self.HttpResponse(RELOAD_PAGE)


class _ExpenseStepForm(VertexModal, HorillaFormView):
    """Base of the approve / reject / invoice modals; ``allowed`` decides who may open and submit."""

    model = Expense
    form_class = DecisionForm
    success_message = ""

    @staticmethod
    def allowed(user, expense):
        return False

    def run(self, form, expense):
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        expense = access.visible_expenses(request.user).filter(pk=kwargs.get("pk")).first()
        if expense is None or not self.allowed(request.user, expense):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        expense = access.visible_expenses(self.request.user).filter(pk=self.kwargs["pk"]).first()
        try:
            self.run(form, expense)
        except expenses.ExpenseError as error:
            messages.error(self.request, str(error))
        else:
            messages.success(self.request, self.success_message)
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class ExpenseApproveFormView(_ExpenseStepForm):
    allowed = staticmethod(access.can_approve_expense)
    success_message = _("Expense approved.")

    def run(self, form, expense):
        expenses.approve(expense.pk, access.employee_of(self.request.user), form.cleaned_data.get("note", ""))


@method_decorator(login_required, name="dispatch")
class ExpenseRejectFormView(_ExpenseStepForm):
    form_class = RejectForm
    allowed = staticmethod(access.can_approve_expense)
    success_message = _("Expense rejected.")

    def run(self, form, expense):
        expenses.reject(expense.pk, access.employee_of(self.request.user), form.cleaned_data["note"])


@method_decorator(login_required, name="dispatch")
class ExpenseInvoiceFormView(_ExpenseStepForm):
    form_class = InvoiceForm
    allowed = staticmethod(access.can_upload_invoice)
    success_message = _("Invoice uploaded.")

    def run(self, form, expense):
        expenses.attach_invoice(expense, form.cleaned_data["invoice_file"])


@method_decorator(login_required, name="dispatch")
class ExpenseSettingFormView(VertexModal, HorillaFormView):
    model = ExpenseSetting
    form_class = SettingsForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm("vertex.change_expensesetting") or kwargs.get("pk") != ExpenseSetting.current().pk:
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Expense settings saved."))
        return self.HttpResponse(RELOAD_PAGE)
