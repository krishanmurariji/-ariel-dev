from django.conf import settings
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework.authentication import SessionAuthentication

from horilla_api.schema import OrderedTagSchemaGenerator

# Create schema view for Swagger and ReDoc
schema_view = get_schema_view(
    openapi.Info(
        title="Horilla API",
        default_version="v1",
        description="API documentation for Horilla HRMS. Click the 'Authorize' button at the top to authenticate.",
        terms_of_service="https://www.horilla.com/terms/",
        contact=openapi.Contact(email="support@horilla.com"),
        license=openapi.License(name="BSD License"),
    ),
    public=True,
    # AllowAny exposed the full API surface (every endpoint, every field)
    # to anyone unauthenticated, unconditionally, in every environment -
    # not gated by DEBUG or anything else. Left open for DEBUG (local
    # dev convenience); restricted to staff/superusers otherwise.
    #
    # SessionAuthentication is added here (on top of the project-wide
    # JWTAuthentication default) so a staff member who is simply logged
    # into the normal web app can open these docs in their browser -
    # without it, IsAdminUser would only ever recognize a JWT Bearer
    # token, meaning nobody could view the docs page without first
    # calling the login API separately and pasting a token into Swagger's
    # own "Authorize" button just to see the page that button lives on.
    authentication_classes=(
        (SessionAuthentication,) if not settings.DEBUG else ()
    ),
    permission_classes=(
        (permissions.AllowAny,)
        if settings.DEBUG
        else (permissions.IsAdminUser,)
    ),
    generator_class=OrderedTagSchemaGenerator,
)

urlpatterns = [
    # API Documentation URLs
    path(
        "swagger<format>/", schema_view.without_ui(cache_timeout=0), name="schema-json"
    ),
    path(
        "swagger/",
        schema_view.with_ui("swagger", cache_timeout=0),
        name="schema-swagger-ui",
    ),
    path("redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
    path("docs/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-docs"),
    # API Endpoints (static configuration)
    path("auth/", include("horilla_api.api_urls.auth.urls")),
    path("asset/", include("horilla_api.api_urls.asset.urls")),
    path("base/", include("horilla_api.api_urls.base.urls")),
    path("employee/", include("horilla_api.api_urls.employee.urls")),
    path("notifications/", include("horilla_api.api_urls.notifications.urls")),
    path("payroll/", include("horilla_api.api_urls.payroll.urls")),
    path("attendance/", include("horilla_api.api_urls.attendance.urls")),
    path("leave/", include("horilla_api.api_urls.leave.urls")),
    path("helpdesk/", include("horilla_api.api_urls.helpdesk.urls")),
    path("project/", include("horilla_api.api_urls.project.urls")),
    path("onboarding/", include("horilla_api.api_urls.onboarding.urls")),
    path("offboarding/", include("horilla_api.api_urls.offboarding.urls")),
    path("recruitment/", include("horilla_api.api_urls.recruitment.urls")),
    path("pms/", include("horilla_api.api_urls.pms.urls")),
]
