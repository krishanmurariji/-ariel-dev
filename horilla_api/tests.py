"""
horilla_api/tests.py

Regression coverage for the cross-tenant object_check() fix shared by
horilla_api/api_views/recruitment/views.py and
horilla_api/api_views/onboarding/views.py.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Company, Department, JobPosition
from recruitment.models import Recruitment


class ObjectCheckFailsClosedTests(TestCase):
    """
    object_check() only re-applied per-company scoping
    "if filter_path and company is not None" - when the requesting API
    user had no linked Employee row at all (e.g. a service/admin account
    with the model's view permission but nothing else), `company` was
    None, so the whole scoping check was skipped and the unfiltered
    queryset's object was returned regardless of company. Any
    authenticated caller with the model-level permission could fetch
    another company's object by pk. Fixed by failing closed (returning
    None -> 404) when the caller's company can't be resolved, unless
    they're a superuser - mirroring
    OwnCompanyPrimaryKeyRelatedField.get_queryset() in
    horilla_api/api_serializers/recruitment/serializers.py.
    """

    def setUp(self):
        self.company = Company.objects.create(
            company="ObjectCheckCo", address="x", country="x", state="x",
            city="x", zip="00000",
        )
        self.dept = Department.objects.create(department="ObjectCheckDept")
        self.job = JobPosition.objects.create(
            job_position="ObjectCheckRole", department_id=self.dept
        )
        self.recruitment = Recruitment.objects.create(
            title="ObjectCheckRecruitment", company_id=self.company, job_position_id=self.job
        )

        User = get_user_model()
        # A user with the model-level permission but no linked Employee
        # row at all - the edge case that used to fail open.
        self.no_employee_user = User.objects.create_user(
            username="no.employee.user", password="123qwe"
        )
        self.no_employee_user.user_permissions.add(
            Permission.objects.get(codename="view_recruitment")
        )
        self.superuser = User.objects.create_superuser(
            username="objectcheck.super", email="objectcheck.super@example.com",
            password="123qwe",
        )

    def test_user_with_no_employee_row_cannot_fetch_object_by_pk(self):
        client = APIClient()
        client.force_authenticate(user=self.no_employee_user)
        response = client.get(f"/api/recruitment/recruitment/{self.recruitment.pk}/")
        self.assertEqual(
            response.status_code, 404,
            "a caller with no resolvable company must not be able to fetch "
            "any company's object by pk just by holding the model's view "
            "permission",
        )

    def test_superuser_can_still_fetch_the_object(self):
        client = APIClient()
        client.force_authenticate(user=self.superuser)
        response = client.get(f"/api/recruitment/recruitment/{self.recruitment.pk}/")
        self.assertEqual(response.status_code, 200)
