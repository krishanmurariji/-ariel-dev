"""
e2e_tests/test_e2e_recruitment_pipeline.py

Real-browser coverage for the Recruitment candidate list and pipeline
kanban view - both lean on htmx swaps and client-side JS that a
server-side Django test client (used everywhere else in this project's
test suite) structurally can't exercise. See e2e_tests/conftest.py.
"""

from recruitment.factories import PASSWORD


def test_login_redirects_to_dashboard(page, live_server, hr_user):
    employee, company = hr_user

    page.goto(f"{live_server.url}/login/")
    page.fill("input[name='username']", employee.email)
    page.fill("input[name='password']", PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")

    assert "/login" not in page.url, f"still on login page: {page.url}"


def test_candidate_list_shows_seeded_candidate(logged_in_page, live_server, recruitment_with_candidate):
    page, company = logged_in_page
    recruitment, candidate = recruitment_with_candidate

    page.goto(f"{live_server.url}/recruitment/candidate-view/")
    page.wait_for_load_state("networkidle")

    # The list itself renders via an htmx fragment request - wait for the
    # candidate's name to actually appear in the DOM rather than assuming
    # the first paint already has it.
    page.wait_for_selector(f"text={candidate.name}", timeout=10000)
    assert candidate.name in page.content()


def test_pipeline_kanban_shows_candidate_in_its_stage(logged_in_page, live_server, recruitment_with_candidate):
    page, company = logged_in_page
    recruitment, candidate = recruitment_with_candidate

    page.goto(f"{live_server.url}/recruitment/pipeline/")
    page.wait_for_load_state("networkidle")

    page.wait_for_selector(f"text={candidate.name}", timeout=15000)
    assert candidate.name in page.content()
