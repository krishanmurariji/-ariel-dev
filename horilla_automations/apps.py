"""
App configuration for the Horilla Automations app.
Initializes model choices and starts automation when the server runs.
"""

import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class HorillaAutomationConfig(AppConfig):
    """Configuration class for the Horilla Automations Django app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "horilla_automations"

    def ready(self) -> None:
        ready = super().ready()
        if any(
            cmd in sys.argv
            for cmd in [
                "makemigrations",
                "migrate",
                "compilemessages",
                "flush",
                "shell",
            ]
        ):
            return ready
        try:

            from base.templatetags.horillafilters import app_installed
            from employee.models import Employee
            from horilla_automations.methods.methods import get_related_models
            from horilla_automations.models import MODEL_CHOICES

            recruitment_installed = False
            if app_installed("recruitment"):
                recruitment_installed = True

            models = [Employee]
            if recruitment_installed:
                from recruitment.models import Candidate

                models.append(Candidate)

            main_models = models
            for main_model in main_models:
                related_models = get_related_models(main_model)

                for model in related_models:
                    path = f"{model.__module__}.{model.__name__}"
                    MODEL_CHOICES.append((path, model.__name__))
            MODEL_CHOICES.append(("employee.models.Employee", "Employee"))
            MODEL_CHOICES.append(
                ("pms.models.EmployeeKeyResult", "Employee Key Results")
            )

            MODEL_CHOICES = list(set(MODEL_CHOICES))
            try:
                from horilla_automations.signals import start_automation

                start_automation()
            except Exception:
                # If this fails (e.g. the DB isn't reachable yet when this
                # worker boots), post_save signals for Candidate/Employee/etc
                # never get connected in THIS process - every mail/notification
                # automation (recruitment's "Application Received" and
                # rejection mail among them) then silently never fires for
                # any request this worker handles, for its entire lifetime,
                # with nothing logged anywhere. Log it so it's actually
                # visible, and see ensure_automation_ready() (called from
                # ThreadLocalMiddleware) for the self-healing retry.
                logger.exception(
                    "Failed to start mail/notification automations - "
                    "signals not connected in this worker; will retry on "
                    "the next request via ensure_automation_ready()."
                )
        except Exception:
            logger.exception(
                "horilla_automations startup failed before automations could "
                "be initialized (model choices setup)."
            )
        return ready
