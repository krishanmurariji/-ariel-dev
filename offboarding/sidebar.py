"""
offboarding/sidebar.py
"""

import logging

from django.apps import apps
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

# from base.context_processors import resignation_request_enabled  (only the retired page used it)
from horilla.menu import settings_menu
from offboarding.templatetags.offboarding_filter import (
    any_manager,
    is_offboarding_employee,
)

logger = logging.getLogger(__name__)

MENU = _("Offboarding")
IMG_SRC = "images/ui/exit-outline.svg"
ACCESSIBILITY = "offboarding.sidebar.offboarding_accessibility"


SUBMENUS = [
    {
        "menu": _("Dashboard"),
        "redirect": reverse("offboarding-dashboard"),
        "accessibility": "offboarding.sidebar.dashboard_accessibility",
    },
    # Retired: the old "Resignation Letters" page. Requests are reviewed under Exit Process.
    # {
    #     "menu": _("Resignation Letters"),
    #     "redirect": reverse("resignation-request-view"),
    #     "accessibility": "offboarding.sidebar.resignation_letter_accessibility",
    # },
    {
        "menu": _("Exit Process"),
        "redirect": reverse("exit-process"),
        "accessibility": "offboarding.sidebar.exit_process_accessibility",
    },
    {
        "menu": _("My Exit Tasks"),
        "redirect": reverse("exit-my-tasks"),
        "accessibility": "offboarding.sidebar.my_exit_tasks_accessibility",
    },
    {
        "menu": _("My Exit Survey"),
        "redirect": reverse("exit-survey-fill"),
        "accessibility": "offboarding.sidebar.my_exit_survey_accessibility",
    },
]


def offboarding_accessibility(request, menu, user_perms, *args, **kwargs):
    """
    The Offboarding entry shows only when at least one of its items would.
    Being part of an exit on its own is not enough: an employee who is
    leaving gets their things through My Exit Tasks / My Exit Survey, and
    when there is nothing for them the entry would open an empty menu.
    """
    try:
        return any(
            check(request, menu, user_perms)
            for check in (
                dashboard_accessibility,
                # resignation_letter_accessibility,  (retired with the old page)
                exit_process_accessibility,
                my_exit_tasks_accessibility,
                my_exit_survey_accessibility,
            )
        )
    except Exception:
        logger.warning("Could not work out the Offboarding menu", exc_info=True)
        return False


def _has_exit_tasks(request):
    """
    Does this person have an exit task to do or approve? This runs while every
    page's menu is built, so a failure here (for example a database that has
    not been migrated yet) must hide the menu entry, never break the page.
    """
    from offboarding.exit_tasks import has_work

    try:
        employee = getattr(request.user, "employee_get", None)
        return bool(employee and has_work(employee))
    except Exception:
        logger.warning("Could not check exit tasks for the menu", exc_info=True)
        return False


def _has_exit_survey(request):
    """Is a survey waiting for this person? Fails safe, like _has_exit_tasks."""
    from offboarding.exit_survey import pending_for

    try:
        employee = getattr(request.user, "employee_get", None)
        return bool(employee and pending_for(employee))
    except Exception:
        logger.warning("Could not check the exit survey for the menu", exc_info=True)
        return False


def my_exit_survey_accessibility(request, menu, user_perms, *args, **kwargs):
    """Only someone with an exit survey to fill in sees this."""
    return _has_exit_survey(request)


def my_exit_tasks_accessibility(request, menu, user_perms, *args, **kwargs):
    """Only people with an exit task to do or approve see this."""
    return _has_exit_tasks(request)


# Retired with the old "Resignation Letters" page:
# def resignation_letter_accessibility(request, menu, user_perms, *args, **kwargs):
#     return resignation_request_enabled(request)[
#         "enabled_resignation_request"
#     ] and request.user.has_perm("offboarding.view_resignationletter")


def exit_process_accessibility(request, menu, user_perms, *args, **kwargs):
    """HR (offboarding view permission) or anyone with direct reports to review."""
    from offboarding.exit_pages import has_exit_access

    return has_exit_access(request)


def dashboard_accessibility(request, *args, **kwargs):
    """
    Check if the user has permission to view the dashboard.
    """

    return request.user.has_module_perms("offboarding") or any_manager(
        request.user.employee_get
    )


# ---------------------------------------------------------------------------
# Settings menu registration
# ---------------------------------------------------------------------------


def offboarding_rules_accessibility(request, submenu, user_perms, *args, **kwargs):
    user = request.user
    return user.has_perm("offboarding.change_offboardinggeneralsetting") or (
        apps.is_installed("payroll")
        and user.has_perm("payroll.change_payrollgeneralsetting")
    )


def exit_tasks_settings_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("offboarding.change_offboardinggeneralsetting")


@settings_menu.register
class OffboardingSettings:
    title = _("Offboarding")
    order = 8
    condition = lambda self, request: apps.is_installed("offboarding")
    items = [
        {
            "label": _("Exit Settings"),
            "url": reverse_lazy("exit-settings"),
            "accessibility": offboarding_rules_accessibility,
        },
        {
            "label": _("Exit Survey"),
            "url": reverse_lazy("exit-survey-settings"),
            "accessibility": exit_tasks_settings_accessibility,
        },
        {
            "label": _("Exit Tasks"),
            "url": reverse_lazy("exit-task-groups"),
            "accessibility": exit_tasks_settings_accessibility,
        },
    ]
