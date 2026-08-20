"""
App configuration for the 'payroll_pro' app.

Keka-style payroll suite: Tax Regimes, Salary Structures, Salary Overview,
Run Payroll wizard and Payslip / My Finance employee self service.
"""

from django.apps import AppConfig
from django.conf import settings


class PayrollProConfig(AppConfig):
    """
    AppConfig for the 'payroll_pro' app.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "payroll_pro"
    verbose_name = "Payroll Pro"

    def ready(self) -> None:
        ready = super().ready()
        from django.urls import include, path

        from horilla.urls import urlpatterns
        from payroll_pro import scheduler  # noqa: F401  starts background jobs

        settings.APPS.append("payroll_pro")
        urlpatterns.append(
            path("payroll-pro/", include("payroll_pro.urls")),
        )
        return ready
