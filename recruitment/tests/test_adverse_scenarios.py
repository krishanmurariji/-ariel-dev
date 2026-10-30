"""
Adverse and edge cases for the recruitment pages changed in this release: the public Open Jobs page with
hostile or missing data, the interview week strip for people who should not see it, the candidate export
for everyone who should not get it, and the plain-English history summary on odd values.
"""

import io
import json
from datetime import timedelta

from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from base.models import Company, Department, JobPosition
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import grant, make_employee
from recruitment.models import Candidate, InterviewSchedule, Recruitment, Skill, Stage

SCRIPT = "<script>alert(1)</script>"


def company():
    return Company.objects.create(company="Adv Co", address="x", country="x", state="x", city="x", zip="00000")


def recruitment_with(title="Role", dept="Engineering", published=True, closed=False, active=True, **extra):
    department = Department.objects.get_or_create(department=dept)[0]
    job = JobPosition.objects.create(job_position=f"{title} job", department_id=department)
    rec = Recruitment.objects.create(
        title=title, vacancy=extra.pop("vacancy", 1), is_published=published, closed=closed, is_active=active,
        company_id=extra.pop("company_id", None), **extra,
    )
    rec.open_positions.add(job)
    return rec


class OpenJobsAdverse(IsolatedTestCase):
    def page(self, query=""):
        return Client(raise_request_exception=False).get(reverse("open-recruitments") + query)

    def test_hostile_text_is_escaped_everywhere(self):
        rec = recruitment_with(title=SCRIPT, dept='Dev"><img src=x onerror=alert(2)>', description="<img src=x onerror=alert(3)> plain words")
        skill = Skill.objects.create(title="<svg onload=alert(4)>")
        rec.skills.add(skill)
        response = self.page()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn(SCRIPT, body)
        self.assertNotIn("<img src=x onerror=alert(2)>", body)
        self.assertNotIn("<img src=x onerror=alert(3)>", body)
        self.assertNotIn("<svg onload=alert(4)>", body)
        self.assertIn("plain words", body)  # the readable part of the description survives

    def test_unpublished_closed_and_inactive_roles_are_never_listed(self):
        recruitment_with(title="Visible Role")
        recruitment_with(title="Unpublished Role", published=False)
        recruitment_with(title="Closed Role", closed=True)
        recruitment_with(title="Inactive Role", active=False)
        body = self.page().content.decode()
        self.assertIn("Visible Role", body)
        for hidden in ("Unpublished Role", "Closed Role", "Inactive Role"):
            self.assertNotIn(hidden, body)

    def test_a_role_with_nothing_filled_in_still_renders(self):
        rec = Recruitment.objects.create(title="Bare Role", vacancy=0, is_published=True)
        self.assertEqual(rec.skills.count(), 0)
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bare Role")
        self.assertNotContains(response, 'class="oj-skills"')

    def test_no_roles_at_all_shows_the_empty_state_and_no_create_button(self):
        response = self.page()
        self.assertContains(response, "No open roles")
        self.assertNotContains(response, "Create opening")
        self.assertEqual(response.context["departments"], [])

    def test_many_roles_and_many_skills(self):
        for number in range(60):
            rec = recruitment_with(title=f"Bulk Role {number}", dept=f"Dept {number % 7}")
            for skill_number in range(8):
                rec.skills.add(Skill.objects.get_or_create(title=f"skill{skill_number}")[0])
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_count"], 60)
        self.assertEqual(len(response.context["departments"]), 7)
        self.assertContains(response, "+4")  # 8 skills, 4 shown

    def test_garbage_query_strings_do_not_matter(self):
        recruitment_with(title="Query Role")
        for query in ("?q=%27%22<script>", "?page=-1&dept=%00", "?" + "a=1&" * 500, "?%ff%fe=1"):
            with self.subTest(query=query[:30]):
                self.assertEqual(self.page(query).status_code, 200)

    def test_unicode_and_very_long_text(self):
        recruitment_with(title="Développeur 開発者 🚀", description="é" * 5000)
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Développeur")

    def test_anyone_can_open_the_public_page_and_nobody_gets_a_create_button(self):
        recruitment_with(title="Public Role")
        user, _employee = make_employee("adv_plain")
        client = Client(raise_request_exception=False)
        client.force_login(user)
        for who in (Client(raise_request_exception=False), client):
            response = who.get(reverse("open-recruitments"))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Create opening")


class InterviewWeekAdverse(IsolatedTestCase):
    def setUp(self):
        self.company = company()
        department = Department.objects.create(department="Eng")
        job = JobPosition.objects.create(job_position="Dev", department_id=department)
        self.recruitment = Recruitment.objects.create(title="Wk", company_id=self.company, job_position_id=job)
        self.recruitment.open_positions.add(job)
        self.candidate = Candidate.objects.create(
            name=SCRIPT + " Mallory", email="mallory@example.com",
            recruitment_id=self.recruitment, job_position_id=job,
        )
        self.interviewer_user, self.interviewer = make_employee("wk_interviewer")
        grant(self.interviewer_user, "recruitment", "view_recruitment")
        self.other_user, self.other = make_employee("wk_other")
        grant(self.other_user, "recruitment", "view_recruitment")
        self.plain_user, _plain = make_employee("wk_plain")
        today = timezone.localdate()
        self.interview = InterviewSchedule.objects.create(
            candidate_id=self.candidate, interview_date=today, interview_time="10:00", description="x"
        )
        self.interview.employee_id.add(self.interviewer)

    def week(self, user=None):
        client = Client(raise_request_exception=False)
        if user:
            client.force_login(user)
        return client.get(reverse("interview-week"), HTTP_HX_REQUEST="true")

    def test_anonymous_visitors_get_nothing(self):
        response = self.week()
        self.assertNotIn(response.status_code, (200,))

    def test_someone_with_no_recruitment_role_sees_no_interviews(self):
        self.assertNotContains(self.week(self.plain_user), "Mallory")

    def test_an_interviewer_sees_only_their_own_interviews(self):
        self.assertContains(self.week(self.interviewer_user), "Mallory")
        self.assertNotContains(self.week(self.other_user), "Mallory")

    def test_a_hostile_candidate_name_is_escaped(self):
        body = self.week(self.interviewer_user).content.decode()
        self.assertNotIn(SCRIPT, body)

    def test_the_list_page_survives_junk_filters(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.interviewer_user)
        for query in ("?status=zzz", "?status=", "?candidate_id=abc", "?interview_date=not-a-date", "?page=999999", "?status=cancelled&status=completed"):
            with self.subTest(query=query):
                response = client.get(reverse("interview-list-view") + query, HTTP_HX_REQUEST="true") if self._has("interview-list-view") else None
                if response is not None:
                    self.assertLess(response.status_code, 500)

    @staticmethod
    def _has(name):
        try:
            reverse(name)
            return True
        except Exception:
            return False


class CandidateExportAdverse(IsolatedTestCase):
    def setUp(self):
        self.company = company()
        department = Department.objects.create(department="Exp")
        job = JobPosition.objects.create(job_position="Dev", department_id=department)
        self.recruitment = Recruitment.objects.create(title="Ex", company_id=self.company, job_position_id=job)
        self.recruitment.open_positions.add(job)
        Candidate.objects.create(
            name="Exported One", email="exported.one@example.com",
            recruitment_id=self.recruitment, job_position_id=job,
        )

    def export(self, user=None, **params):
        client = Client(raise_request_exception=False)
        if user:
            client.force_login(user)
            session = client.session
            session["selected_company"] = "all"
            session.save()
        return client.get(reverse("candidate-info-export"), params)

    @staticmethod
    def is_sheet(response):
        body = b"".join(response.streaming_content) if response.streaming else response.content
        return response.status_code == 200 and body[:2] == b"PK"

    def test_anonymous_gets_no_sheet(self):
        self.assertFalse(self.is_sheet(self.export()))

    def test_plain_employee_and_a_recruiter_without_the_permission_get_no_sheet(self):
        plain, _e = make_employee("ce_plain")
        stage_manager, manager = make_employee("ce_manager")
        self.recruitment.recruitment_managers.add(manager)
        for user in (plain, stage_manager):
            with self.subTest(user=user.username):
                self.assertFalse(self.is_sheet(self.export(user)))

    def test_a_view_only_permission_for_something_else_is_not_enough(self):
        user, _e = make_employee("ce_other_perm")
        grant(user, "recruitment", "view_recruitment")
        self.assertFalse(self.is_sheet(self.export(user)))

    def test_the_permission_holder_and_a_superuser_get_the_sheet(self):
        holder, _e = make_employee("ce_holder")
        grant(holder, "recruitment", "view_candidate")
        admin, _a = make_employee("ce_admin")
        admin.is_superuser = True
        admin.save()
        for user in (holder, admin):
            with self.subTest(user=user.username):
                self.assertTrue(self.is_sheet(self.export(user)))

    def test_junk_parameters_never_crash_it(self):
        holder, _e = make_employee("ce_holder2")
        grant(holder, "recruitment", "view_candidate")
        for params in ({"selected_fields": "nonsense"}, {"selected_fields": ["a", "b"]}, {"ids": "not json"}, {"ids": "[1, 'x']"}, {"stage": "zzz"}):
            with self.subTest(params=params):
                self.assertLess(self.export(holder, **params).status_code, 500)


class HistorySummaryWording(SimpleTestCase):
    def record(self, field, old, new, who="Asha Admin"):
        return {"when": "5 Nov 2026", "who": who, "kind": "change", "field": field, "old": old, "new": new}

    def test_odd_values_never_raise_and_never_show_python_leftovers(self):
        from recruitment.ai_history_summary import _fallback_sentence, _prompt_line

        weird = [None, "", "None", True, False, "True", "False", 0, 3.5, [], {}, "x" * 5000, "  ", "Stage - (Job)", "é 🚀", "a\nb"]
        for old in weird:
            for new in weird:
                for field in ("Stage", "Hired", "Canceled", "Profile", "Resume", "Joining date", "Start onboard", "Anything else"):
                    record = self.record(field, old, new)
                    with self.subTest(field=field, old=repr(old)[:20], new=repr(new)[:20]):
                        sentence = _fallback_sentence(record)
                        line = _prompt_line(record)
                        self.assertIsInstance(sentence, str)
                        self.assertNotIn("'None'", sentence)
                        self.assertNotIn("'True'", sentence)
                        self.assertNotIn("'False'", sentence)
                        self.assertTrue(line.startswith("5 Nov 2026"))

    def test_the_system_user_is_named(self):
        from recruitment.ai_history_summary import _who

        self.assertEqual(_who({"updated_by": None}), "Ariel Bot")

    def test_stage_names_lose_the_job_suffix_and_flags_read_as_words(self):
        from recruitment.ai_history_summary import _plain_change

        self.assertEqual(
            _plain_change(self.record("Stage", "Offered - (.Net Dev)", "Hired - (.Net Dev)")),
            "Asha Admin moved the candidate from Offered to Hired.",
        )
        self.assertEqual(_plain_change(self.record("Hired", "False", "True")), "Asha Admin marked the candidate as hired.")
        self.assertEqual(_plain_change(self.record("Hired", "True", "False")), "Asha Admin took off the hired mark.")
