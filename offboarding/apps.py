from django.apps import AppConfig
from django.conf import settings


class OffboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "offboarding"

    def ready(self):
        from django.urls import include, path

        from horilla.urls import urlpatterns

        from offboarding import scheduler  # noqa: F401  (starts the daily job)

        settings.APPS.append("offboarding")
        urlpatterns.append(
            path("offboarding/", include("offboarding.urls")),
        )
        super().ready()
