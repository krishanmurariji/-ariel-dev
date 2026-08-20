"""
recruitment/tests/test_platform_conversion.py

Coverage for the "Platform Conversion Rate" dashboard widget
(recruitment/dashboard.py:recruitment_platform_conversion) - hire rate per
candidate Platform (LinkedIn, Indeed, etc.), grouped top-N + "Other" - and
its enable/disable setting (RecruitmentGeneralSetting.show_platform_conversion,
recruitment/views/views.py:platform_conversion_widget_setting).
"""

import json

from django.test import Client, TestCase

from base.models import Company, Department, JobPosition
from employee.models import Employee
from horilla_auth.models import HorillaUser
from recruitment import dashboard as rec_dashboard
from recruitment.models import Candidate, Platform, Recruitment, RecruitmentGeneralSetting


class PlatformConversionTestCase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Test Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        self.department = Department.objects.create(department="Engineering")
        self.job_position = JobPosition.objects.create(
            job_position="Backend Engineer", department_id=self.department
        )
        self.recruitment = Recruitment.objects.create(
            title="Backend Engineer",
            closed=False,
            is_active=True,
            company_id=self.company,
            job_position_id=self.job_position,
        )
        self.recruitment.open_positions.add(self.job_position)

        self.user = HorillaUser.objects.create_superuser(
            username="pcuser", email="pcuser@example.com", password="Pass123!"
        )
        Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Platform",
            employee_last_name="Tester",
            email="pcuser@example.com",
        )
        self.client = Client()
        self.client.login(username="pcuser", password="Pass123!")

    def tearDown(self):
        # See recruitment/tests/test_ai_scoring.py for why this is needed:
        # _thread_locals leaks the logged-in user across TestCase instances.
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None
        super().tearDown()

    def make_candidate(self, platform=None, hired=False, name="Cand", email=None):
        return Candidate.objects.create(
            name=name,
            email=email or f"{name.lower().replace(' ', '')}@example.com",
            recruitment_id=self.recruitment,
            job_position_id=self.job_position,
            platform=platform,
            hired=hired,
        )


class PlatformConversionDataTests(PlatformConversionTestCase):
    def test_no_candidates_returns_empty_list(self):
        resp = self.client.get("/recruitment/dashboard/api/platform-conversion/")
        self.assertEqual(json.loads(resp.content)["platforms"], [])

    def test_hire_rate_computed_per_platform(self):
        linkedin = Platform.objects.create(title="LinkedIn")
        indeed = Platform.objects.create(title="Indeed")
        self.make_candidate(platform=linkedin, hired=True, name="A")
        self.make_candidate(platform=linkedin, hired=False, name="B")
        self.make_candidate(platform=indeed, hired=False, name="C")

        resp = self.client.get("/recruitment/dashboard/api/platform-conversion/")
        platforms = {p["source"]: p for p in json.loads(resp.content)["platforms"]}

        self.assertEqual(platforms["LinkedIn"]["total"], 2)
        self.assertEqual(platforms["LinkedIn"]["hired"], 1)
        self.assertEqual(platforms["LinkedIn"]["rate"], 50.0)
        self.assertEqual(platforms["Indeed"]["total"], 1)
        self.assertEqual(platforms["Indeed"]["hired"], 0)
        self.assertEqual(platforms["Indeed"]["rate"], 0)

    def test_candidates_without_platform_are_not_specified(self):
        self.make_candidate(platform=None, hired=True, name="A")
        self.make_candidate(platform=None, hired=False, name="B")

        resp = self.client.get("/recruitment/dashboard/api/platform-conversion/")
        platforms = {p["source"]: p for p in json.loads(resp.content)["platforms"]}

        self.assertIn("Not Specified", platforms)
        self.assertEqual(platforms["Not Specified"]["total"], 2)
        self.assertEqual(platforms["Not Specified"]["hired"], 1)

    def test_platforms_beyond_top_n_collapse_into_other(self):
        # PLATFORM_CONVERSION_TOP_N is 6 - create 8 platforms with a
        # strictly decreasing candidate count so ranking is unambiguous,
        # then confirm only the top 6 show individually and the rest
        # are summed into a single "Other" bucket.
        platform_counts = []
        for i in range(8):
            platform = Platform.objects.create(title=f"Platform{i}")
            count = 8 - i  # Platform0 gets 8 candidates, Platform7 gets 1
            for j in range(count):
                self.make_candidate(
                    platform=platform, hired=(j == 0), name=f"P{i}C{j}"
                )
            platform_counts.append((platform.title, count))

        resp = self.client.get("/recruitment/dashboard/api/platform-conversion/")
        platforms = json.loads(resp.content)["platforms"]
        names = [p["source"] for p in platforms]

        top_6_names = [name for name, _ in platform_counts[:6]]
        for name in top_6_names:
            self.assertIn(name, names)
        self.assertNotIn("Platform6", names)
        self.assertNotIn("Platform7", names)
        self.assertIn("Other", names)

        other = next(p for p in platforms if p["source"] == "Other")
        # Platform6 (count=2) + Platform7 (count=1) collapsed together
        self.assertEqual(other["total"], 3)
        self.assertEqual(other["hired"], 2)  # one hire recorded per platform

    def test_widget_disabled_returns_disabled_flag_not_data(self):
        RecruitmentGeneralSetting.objects.create(
            company_id=None, show_platform_conversion=False
        )
        self.make_candidate(hired=True, name="A")

        resp = self.client.get("/recruitment/dashboard/api/platform-conversion/")
        data = json.loads(resp.content)
        self.assertTrue(data.get("disabled"))
        self.assertNotIn("platforms", data)

    def test_widget_enabled_by_default_when_no_setting_row_exists(self):
        self.assertFalse(RecruitmentGeneralSetting.objects.exists())
        resp = self.client.get("/recruitment/dashboard/api/platform-conversion/")
        data = json.loads(resp.content)
        self.assertIn("platforms", data)
        self.assertNotIn("disabled", data)


class PlatformConversionSettingToggleTests(PlatformConversionTestCase):
    def test_enabling_creates_setting_row_with_true(self):
        self.assertFalse(RecruitmentGeneralSetting.objects.exists())
        self.client.get(
            "/recruitment/platform-conversion-widget-setting/?show_platform_conversion=true",
            HTTP_HX_REQUEST="true",
        )
        settings = RecruitmentGeneralSetting.objects.get(company_id=None)
        self.assertTrue(settings.show_platform_conversion)

    def test_disabling_flips_existing_setting_to_false(self):
        RecruitmentGeneralSetting.objects.create(
            company_id=None, show_platform_conversion=True
        )
        self.client.get(
            "/recruitment/platform-conversion-widget-setting/?show_platform_conversion=false",
            HTTP_HX_REQUEST="true",
        )
        settings = RecruitmentGeneralSetting.objects.get(company_id=None)
        self.assertFalse(settings.show_platform_conversion)
