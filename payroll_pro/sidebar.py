"""
payroll_pro/sidebar.py

Sidebar menu registration for the Payroll Pro section, mirroring the
permission-driven accessibility pattern of the stock payroll module.
"""

from django.apps import apps
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from horilla.menu import settings_menu

MENU = _("Payroll Pro")
IMG_SRC = "images/ui/wallet-outline.svg"

SUBMENUS = [
    {
        "menu": _("Tax Regime"),
        "redirect": reverse("pp-tax-regimes"),
        "accessibility": "payroll_pro.sidebar.tax_regime_accessibility",
    },
    {
        "menu": _("Salary Structure"),
        "redirect": reverse("pp-structures"),
        "accessibility": "payroll_pro.sidebar.structure_accessibility",
    },
    {
        "menu": _("Salary Overview"),
        "redirect": reverse("pp-salary-overview"),
        "accessibility": "payroll_pro.sidebar.overview_accessibility",
    },
    {
        "menu": _("Pay Revisions"),
        "redirect": reverse("pp-pay-revisions"),
        "accessibility": "payroll_pro.sidebar.overview_accessibility",
    },
    {
        "menu": _("Run Payroll"),
        "redirect": reverse("pp-run-payroll"),
        "accessibility": "payroll_pro.sidebar.run_accessibility",
    },
    # Payslips is intentionally not a separate top-level item — payslips are
    # reached from My Finance (and from the Run Payroll outcome card for
    # release/email); the standalone admin view still exists at pp-payslips
    # for anyone linking to it directly.
    # {
    #     "menu": _("Payslips"),
    #     "redirect": reverse("pp-payslips"),
    #     "accessibility": "payroll_pro.sidebar.payslip_accessibility",
    # },
    {
        "menu": _("My Finance"),
        "redirect": reverse("pp-my-finance"),
    },
]


def tax_regime_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("payroll_pro.view_taxregime")


def structure_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("payroll_pro.view_salarystructure")


def overview_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("payroll_pro.view_employeesalary")


def run_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("payroll_pro.view_payrollrun")


def payslip_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("payroll_pro.view_payrollrunrecord")


# ---------------------------------------------------------------------------
# Settings menu registration
# ---------------------------------------------------------------------------
def auto_run_settings_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("payroll_pro.view_payrollproautosetting")


@settings_menu.register
class PayrollProSettings:
    title = _("Payroll Pro")
    order = 8
    condition = lambda self, request: apps.is_installed("payroll_pro")
    items = [
        {
            "label": _("Payroll Auto Run"),
            "url": reverse_lazy("pp-auto-settings"),
            "accessibility": auto_run_settings_accessibility,
        },
    ]
