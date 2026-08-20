from django.apps import AppConfig


class GmailAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gmail_auth"

    def ready(self):
        from horilla.urls import include, path, urlpatterns

        urlpatterns.append(
            path("gmail/", include("gmail_auth.urls")),
        )
        return super().ready()
