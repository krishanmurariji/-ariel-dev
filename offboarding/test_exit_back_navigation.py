"""
Windows opened from the Manage exit panel (change owner, skip, edit last working day,
cancel exit, survey responses) must lead back to the panel on "Cancel" / "Back", not
close everything. The same windows opened from a list page still just close.
"""

from django.urls import reverse

from offboarding import exit_survey
from offboarding.test_exit_pages import HX
from offboarding.test_exit_survey import SurveyBase
from offboarding.test_exit_task_pages import TaskPageBase


def panel_url(case, section):
    return reverse("exit-manage", args=[case.pk, section])


class WindowsFromThePanelTests(SurveyBase):
    def get(self, name, *args, query=""):
        self.client.force_login(self.hr_user)
        return self.client.get(reverse(name, args=args) + query, **HX).content.decode()

    def test_edit_date_goes_back_to_the_summary(self):
        html = self.get("exit-change-date", self.case.pk, query="?from=panel&section=summary")
        self.assertIn(f'hx-get="{panel_url(self.case, "summary")}"', html)
        # a failed save keeps the way back (the form posts to its own full address)
        self.assertIn("from=panel", html)

    def test_cancel_exit_goes_back_to_the_summary(self):
        html = self.get("exit-cancel", self.case.pk, query="?from=panel&section=summary")
        self.assertIn(f'hx-get="{panel_url(self.case, "summary")}"', html)

    def test_survey_responses_go_back_to_the_survey_tab(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        answers, errors = exit_survey.clean_answers(survey, self.good_answers())
        self.assertEqual(errors, {})
        exit_survey.submit(survey, self.case.employee_id, answers)
        html = self.get("exit-survey-view", survey.pk, query="?from=panel&section=survey")
        self.assertIn(f'hx-get="{panel_url(self.case, "survey")}"', html)
        self.assertIn("Back", html)

    def test_from_a_list_page_they_still_just_close(self):
        for name in ("exit-change-date", "exit-cancel"):
            html = self.get(name, self.case.pk)
            self.assertNotIn("exit-process/manage/", html, name)
            self.assertIn("removeClass('oh-modal--show')", html, name)

    def test_a_made_up_section_falls_back_to_the_summary(self):
        html = self.get("exit-cancel", self.case.pk, query="?from=panel&section=../../evil")
        self.assertIn(f'hx-get="{panel_url(self.case, "summary")}"', html)

    def test_the_panel_links_carry_where_to_come_back_to(self):
        self.client.force_login(self.hr_user)
        summary = self.client.get(panel_url(self.case, "summary"), **HX).content.decode()
        self.assertIn("from=panel&amp;section=summary", summary.replace("&section", "&amp;section"))


class TaskWindowFromThePanelTests(TaskPageBase):
    def test_cancel_returns_to_the_tasks_tab(self):
        task = self.task("Exit interview")
        self.client.force_login(self.hr_user)
        html = self.client.get(
            reverse("exit-task-action", args=[task.pk, "owner"]) + "?next=panel", **HX
        ).content.decode()
        self.assertIn(f'hx-get="{panel_url(self.case, "tasks")}"', html)

    def test_from_my_tasks_cancel_still_closes(self):
        task = self.task("Exit interview")
        self.client.force_login(self.mgr_user)
        html = self.client.get(
            reverse("exit-task-action", args=[task.pk, "complete"]) + "?next=mine", **HX
        ).content.decode()
        self.assertNotIn("exit-process/manage/", html)
        self.assertIn("removeClass('oh-modal--show')", html)
