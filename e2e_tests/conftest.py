"""
e2e_tests/conftest.py

Browser-level E2E tests using Playwright + pytest-django's live_server
fixture (a real Django dev server on a background thread, backed by the
same file-based SQLite test DB used elsewhere in this project - see
horilla/settings/base.py DATABASES["default"]["TEST"]["NAME"]).

These are deliberately separate from the existing unittest/manage.py
test suite (recruitment/tests/, onboarding/tests.py): pytest.ini scopes
pytest's own discovery to this e2e_tests/ directory only, so `manage.py
test` and `pytest` never collide or double-run each other's tests.

Run with: venv/Scripts/python.exe -m pytest e2e_tests/
(headed, to watch it): pytest e2e_tests/ --headed --slowmo 300
"""

import asyncio
import sys

if sys.platform == "win32":
    # Playwright's sync API drives the Node.js browser-automation
    # process via asyncio subprocess creation, which Windows only
    # supports under ProactorEventLoop - SelectorEventLoop (which
    # something in this process's import chain may have already set as
    # the default policy) raises NotImplementedError from
    # _make_subprocess_transport. Must be set before pytest-playwright's
    # own `playwright` fixture calls sync_playwright().start().
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import pytest

from recruitment.factories import PASSWORD, CompanyFactory, EmployeeFactory, RecruitmentFactory
from recruitment.models import Candidate, Stage


@pytest.fixture
def hr_user(db):
    """An HR employee with full recruitment + onboarding permissions."""
    company = CompanyFactory()
    employee = EmployeeFactory(
        company=company,
        perms=[
            "view_recruitment", "add_recruitment", "change_recruitment",
            "view_candidate", "add_candidate", "change_candidate",
            "view_stage", "add_stage", "change_stage",
        ],
    )
    return employee, company


@pytest.fixture
def recruitment_with_candidate(db, hr_user):
    employee, company = hr_user
    recruitment = RecruitmentFactory(company_id=company, title="E2E Test Recruitment")
    stage = Stage.objects.filter(recruitment_id=recruitment).order_by("sequence").first()
    candidate = Candidate.objects.create(
        name="E2E Test Candidate", email="e2e.candidate@example.com",
        recruitment_id=recruitment, job_position_id=recruitment.job_position_id, stage_id=stage,
    )
    return recruitment, candidate


@pytest.fixture
def logged_in_page(page, live_server, hr_user):
    """A Playwright page already logged in as the HR fixture user, with
    its company selected in session - mirrors what a real browser
    session looks like after a normal login + company picker use."""
    employee, company = hr_user
    page.goto(f"{live_server.url}/login/")
    page.fill("input[name='username']", employee.email)
    page.fill("input[name='password']", PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")
    return page, company
