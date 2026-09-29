import hmac
import logging

from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings
from django.contrib.auth import authenticate
from django.core.cache import cache
from drf_yasg import openapi
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from base.backends import ConfiguredEmailBackend
from base.methods import generate_otp
from base.otp_mail import send_otp_email
from horilla_api.docs import document_api
from horilla_api.throttling import TrustedScopedRateThrottle

from ...api_serializers.auth.serializers import (
    GetEmployeeSerializer,
    LoginRequestSerializer,
    PasswordResetSerializer,
)


logger = logging.getLogger(__name__)

API_OTP_LIFETIME_SECONDS = 600
API_OTP_RESEND_AFTER_SECONDS = 60
API_OTP_MAX_ATTEMPTS = 3
API_OTP_LOCKOUT_SECONDS = 900


def second_factor_challenge(request, user):
    """
    Second step of the API login when TWO_FACTORS_AUTHENTICATION is on
    (the web login has the same rule). Returns None when the caller may be
    given tokens, otherwise the Response to send back instead: the caller
    posts the username/password once, gets a code by email, then posts again
    with the same fields plus "otp".
    """
    if not settings.TWO_FACTORS_AUTHENTICATION:
        return None
    try:
        mail_configured = ConfiguredEmailBackend().configuration is not None
    except Exception:
        logger.exception("2FA: could not read the email configuration")
        mail_configured = True  # fail closed
    if not mail_configured:
        logger.warning(
            "TWO_FACTORS_AUTHENTICATION is on but no outgoing email server is "
            "configured, so 2FA is NOT being enforced for API logins."
        )
        return None

    employee = getattr(user, "employee_get", None)
    if not employee or not employee.get_mail():
        return Response(
            {"error": "Two-factor login needs an employee record with an email."},
            status=403,
        )

    code_key = f"api_otp_code_{user.pk}"
    sent_key = f"api_otp_sent_{user.pk}"
    attempts_key = f"api_otp_attempts_{user.pk}"

    if cache.get(attempts_key, 0) >= API_OTP_MAX_ATTEMPTS:
        return Response(
            {"error": "Too many wrong codes. Please try again later."}, status=429
        )

    supplied = str(request.data.get("otp") or "").strip()
    stored = cache.get(code_key)

    if not supplied:
        if stored is None or not cache.get(sent_key):
            code = generate_otp()
            cache.set(code_key, code, API_OTP_LIFETIME_SECONDS)
            cache.set(sent_key, True, API_OTP_RESEND_AFTER_SECONDS)
            send_otp_email(employee, code)
        return Response(
            {
                "otp_required": True,
                "error": "A one-time code was emailed to you. "
                "Send it again with the same login as 'otp'.",
            },
            status=401,
        )

    if stored and hmac.compare_digest(supplied, stored):
        cache.delete_many([code_key, sent_key, attempts_key])
        return None

    cache.set(attempts_key, cache.get(attempts_key, 0) + 1, API_OTP_LOCKOUT_SECONDS)
    return Response(
        {"otp_required": True, "error": "Invalid or expired code."}, status=401
    )


class LoginAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [TrustedScopedRateThrottle]
    throttle_scope = "login"

    @document_api(
        operation_description="Authenticate user and return JWT access token with employee info",
        request_body=LoginRequestSerializer,
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "employee": openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "id": openapi.Schema(type=openapi.TYPE_INTEGER),
                            "full_name": openapi.Schema(type=openapi.TYPE_STRING),
                            "employee_profile": openapi.Schema(
                                type=openapi.TYPE_STRING,
                                description="Profile image URL",
                            ),
                        },
                    ),
                    "access": openapi.Schema(
                        type=openapi.TYPE_STRING, description="JWT access token"
                    ),
                    "face_detection": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    "face_detection_image": openapi.Schema(
                        type=openapi.TYPE_STRING,
                        description="Face detection image URL",
                        nullable=True,
                    ),
                    "geo_fencing": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    "company_id": openapi.Schema(
                        type=openapi.TYPE_INTEGER, nullable=True
                    ),
                },
            ),
        },
        tags=["auth"],
    )
    def post(self, request):
        if request.data.get("username") and request.data.get("password"):
            username = request.data.get("username")
            password = request.data.get("password")
            # request= is required now that AxesStandaloneBackend is in
            # AUTHENTICATION_BACKENDS - it needs the request to resolve
            # the client IP and check/record lockout state, and raises
            # AxesBackendRequestParameterRequired without it.
            user = authenticate(request, username=username, password=password)
            if user:
                challenge = second_factor_challenge(request, user)
                if challenge is not None:
                    return challenge
                refresh = RefreshToken.for_user(user)
                employee = user.employee_get
                face_detection = False
                face_detection_image = None
                geo_fencing = False
                company_id = None
                try:
                    face_detection = employee.get_company().face_detection.start
                except:
                    pass
                try:
                    geo_fencing = employee.get_company().geo_fencing.start
                except:
                    pass
                try:
                    face_detection_image = employee.face_detection.image.url
                except:
                    pass
                try:
                    company_id = employee.get_company().id
                except:
                    pass
                result = {
                    "employee": GetEmployeeSerializer(employee).data,
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "face_detection": face_detection,
                    "face_detection_image": face_detection_image,
                    "geo_fencing": geo_fencing,
                    "company_id": company_id,
                }
                return Response(result, status=200)
            else:
                # django-axes refuses a locked-out (username, IP) pair by
                # making authenticate() return None - the same thing a
                # wrong password does - so without this the caller would
                # see "Invalid credentials" for the whole lockout even with
                # the right password. Say so (429), matching the web login.
                if not AxesProxyHandler.is_allowed(
                    request, {"username": username, "password": password}
                ):
                    return Response(
                        {
                            "error": "Too many failed login attempts. "
                            "Please try again later."
                        },
                        status=429,
                    )
                return Response({"error": "Invalid credentials"}, status=401)
        else:
            return Response({"error": "Please provide Username and Password"})


class PasswordResetAPIView(APIView):
    """
    Allows an authenticated employee to change their own password.

    GET  — returns the fields required for the reset form.
    POST — verifies the old password and saves the new one.
    """

    permission_classes = [IsAuthenticated]

    def get(self, _request):
        return Response(
            {"fields": ["old_password", "new_password", "confirm_password"]},
            status=200,
        )

    def post(self, request):
        serializer = PasswordResetSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save()
        return Response({"message": "Password updated successfully."}, status=200)
