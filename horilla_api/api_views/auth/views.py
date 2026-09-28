from axes.handlers.proxy import AxesProxyHandler
from django.contrib.auth import authenticate
from drf_yasg import openapi
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from horilla_api.docs import document_api
from horilla_api.throttling import TrustedScopedRateThrottle

from ...api_serializers.auth.serializers import (
    GetEmployeeSerializer,
    LoginRequestSerializer,
    PasswordResetSerializer,
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
