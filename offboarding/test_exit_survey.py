"""
The exit survey: the question list, sending, answering, the Survey section of
the Manage window, the responses tab, the Excel export and the settings page.
"""

import datetime
import itertools
from io import BytesIO
from types import SimpleNamespace

from django.http import QueryDict
from django.urls import reverse
from openpyxl import load_workbook

from offboarding import exit_service, exit_survey, sidebar
from offboarding.exit_service import ExitError
from offboarding.models import (
    ExitActivity,
    ExitSurvey,
    ExitSurveyQuestion,
    OffboardingGeneralSetting,
)
from offboarding.test_exit_pages import HX, TODAY, PageBase, _full_page, _letter, _person
from recruitment.models import RecruitmentSurvey, SurveyTemplate

DAY = datetime.timedelta
_n = itertools.count()


def question(text=None, kind="text", options="", mandatory=False, **extra):
    return ExitSurveyQuestion.objects.create(
        question=text or f"Question {next(_n)}",
        type=kind,
        options=options,
        is_mandatory=mandatory,
        sequence=next(_n),
        **extra,
    )


def post(**fields):
    data = QueryDict(mutable=True)
    for key, value in fields.items():
        if isinstance(value, (list, tuple)):
            data.setlist(key, value)
        else:
            data[key] = value
    return data


class SurveyBase(PageBase):
    def setUp(self):
        super().setUp()
        self.q_text = question("Why are you leaving?", "textarea", mandatory=True)
        self.q_rate = question("Rate your manager", "rating")
        self.q_pick = question("Main reason", "options", "Pay, Growth, Manager")
        self.q_multi = question("What did you like?", "multiple", "Team, Office, Tools")
        self.q_yes = question("Would you return?", "checkbox")
        self.q_num = question("Years here", "number")
        self.q_date = question("Best day", "date")
        self.case = self.accepted_case()

    def key(self, q):
        return f"q_{q.pk}"

    def good_answers(self):
        return post(**{
            self.key(self.q_text): "Better pay elsewhere",
            self.key(self.q_rate): "4",
            self.key(self.q_pick): "Growth",
            self.key(self.q_multi): ["Team", "Tools"],
            self.key(self.q_yes): "yes",
            self.key(self.q_num): "3.5",
            self.key(self.q_date): "2026-03-01",
        })


class QuestionListTests(SurveyBase):
    def test_only_active_questions_that_apply_are_asked_in_order(self):
        self.q_rate.is_active = False
        self.q_rate.save()
        asked = [q.pk for q in exit_survey.questions_for(self.emp)]
        self.assertNotIn(self.q_rate.pk, asked)
        self.assertEqual(asked, sorted(asked, key=lambda pk: ExitSurveyQuestion.objects.get(pk=pk).sequence))

    def test_a_question_is_checked_when_saved(self):
        cases = [
            ("", "text", ""),
            ("x" * 501, "text", ""),
            ("<script>x()</script>", "text", ""),
            ("Pick", "options", "OnlyOne"),
            ("Pick", "options", "A, a"),
            ("Pick", "options", "A, <img src=x onerror=y>"),
        ]
        for text, kind, options in cases:
            with self.subTest(text[:20], kind=kind):
                with self.assertRaises(ExitError):
                    exit_survey.clean_question(text, kind, options)
        self.assertEqual(
            exit_survey.clean_question(" Pick ", "options", "A ,B,  C"), ("Pick", "A, B, C")
        )
        self.assertEqual(exit_survey.clean_question("Hi", "text", "ignored"), ("Hi", ""))

    def test_copying_from_a_recruitment_survey(self):
        template = SurveyTemplate.objects.create(title="Candidate feedback")
        kept = RecruitmentSurvey.objects.create(question="How was the process?", type="rating", sequence=1)
        kept.template_id.add(template)
        choice = RecruitmentSurvey.objects.create(
            question="Source?", type="options", options="Friend, Ad", sequence=2, is_mandatory=True
        )
        choice.template_id.add(template)
        upload = RecruitmentSurvey.objects.create(question="Your CV", type="file", sequence=3)
        upload.template_id.add(template)
        percent = RecruitmentSurvey.objects.create(question="Fit %", type="percentage", sequence=4)
        percent.template_id.add(template)
        before = ExitSurveyQuestion.objects.count()
        copied, skipped = exit_survey.import_from_recruitment(template)
        self.assertEqual((copied, skipped), (3, 1))  # the file upload is left out
        self.assertEqual(ExitSurveyQuestion.objects.count(), before + 3)
        self.assertEqual(ExitSurveyQuestion.objects.get(question="Fit %").type, "number")
        self.assertTrue(ExitSurveyQuestion.objects.get(question="Source?").is_mandatory)
        again = exit_survey.import_from_recruitment(template)
        self.assertEqual(again, (0, 4))  # nothing is copied twice


class SendingTests(SurveyBase):
    def test_sending_freezes_the_questions(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        self.assertEqual(survey.status, "sent")
        self.assertEqual(len(survey.questions), 7)
        self.q_text.question = "Changed afterwards"
        self.q_text.save()
        survey.refresh_from_db()
        self.assertEqual(survey.questions[0]["question"], "Why are you leaving?")
        self.assertTrue(ExitActivity.objects.entire().filter(action="survey_sent").exists())

    def test_one_survey_per_exit_and_only_while_in_progress(self):
        exit_survey.send_survey(self.case, self.hr)
        with self.assertRaises(ExitError):
            exit_survey.send_survey(self.case, self.hr)
        exited = self.accepted_case(self.other)
        exit_service.mark_exited(exited)
        with self.assertRaises(ExitError):
            exit_survey.send_survey(exited, self.hr)

    def test_no_questions_means_nothing_to_send(self):
        ExitSurveyQuestion.objects.all().delete()
        with self.assertRaises(ExitError):
            exit_survey.send_survey(self.case, self.hr)

    def test_the_survey_goes_out_by_itself_when_the_setting_is_on(self):
        row = OffboardingGeneralSetting.objects.entire().first() or OffboardingGeneralSetting()
        row.exit_survey_auto_send = True
        row.save()
        third_user, third = _person(manager=self.mgr)
        case = self.accepted_case(third)
        self.assertIsNotNone(exit_survey.current_survey(case))

    def test_auto_send_with_no_questions_is_silently_skipped(self):
        ExitSurveyQuestion.objects.all().delete()
        row = OffboardingGeneralSetting.objects.entire().first() or OffboardingGeneralSetting()
        row.exit_survey_auto_send = True
        row.save()
        third_user, third = _person(manager=self.mgr)
        case = self.accepted_case(third)
        self.assertIsNone(exit_survey.current_survey(case))

    def test_reminders_are_rate_limited(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        exit_survey.remind(survey, self.hr)
        with self.assertRaises(ExitError):
            exit_survey.remind(survey, self.hr)

    def test_cancelling_the_exit_retires_the_survey_and_allows_a_fresh_one(self):
        first = exit_survey.send_survey(self.case, self.hr)
        exit_service.cancel_exit(self.case, self.hr, "stays")
        first.refresh_from_db()
        self.assertEqual((first.status, first.is_current), ("cancelled", False))
        letter = _letter(self.emp)
        case = exit_service.review_request(
            letter, self.hr, "accept", "ok", last_working_day=TODAY + DAY(days=15)
        )
        second = exit_survey.send_survey(case, self.hr)
        self.assertNotEqual(first.pk, second.pk)

    def test_the_icon_state(self):
        self.assertEqual(exit_survey.survey_state(self.case), "todo")
        survey = exit_survey.send_survey(self.case, self.hr)
        self.assertEqual(exit_survey.survey_state(self.case), "doing")
        answers, _ = exit_survey.clean_answers(survey, self.good_answers())
        exit_survey.submit(survey, self.emp, answers)
        self.assertEqual(exit_survey.survey_state(self.case), "done")


class AnsweringTests(SurveyBase):
    def setUp(self):
        super().setUp()
        self.survey = exit_survey.send_survey(self.case, self.hr)

    def test_good_answers_are_cleaned_and_typed(self):
        answers, errors = exit_survey.clean_answers(self.survey, self.good_answers())
        self.assertEqual(errors, {})
        self.assertEqual(answers[str(self.q_rate.pk)], 4)
        self.assertEqual(answers[str(self.q_multi.pk)], ["Team", "Tools"])
        self.assertEqual(answers[str(self.q_num.pk)], 3.5)
        self.assertEqual(answers[str(self.q_date.pk)], "2026-03-01")

    def test_a_required_question_must_be_answered_and_optional_ones_may_be_blank(self):
        answers, errors = exit_survey.clean_answers(self.survey, post())
        self.assertEqual(list(errors), [str(self.q_text.pk)])
        self.assertEqual(answers, {})

    def test_bad_answers_are_refused_one_by_one(self):
        bad = {
            self.q_text: "<script>x()</script>",
            self.q_rate: "9",
            self.q_pick: "Money",
            self.q_yes: "maybe",
            self.q_num: "abc",
            self.q_date: "31/12/2026",
        }
        data = post(**{self.key(q): v for q, v in bad.items()}, **{self.key(self.q_multi): ["Team", "Hacks"]})
        _answers, errors = exit_survey.clean_answers(self.survey, data)
        self.assertEqual(set(errors), {str(q.pk) for q in list(bad) + [self.q_multi]})

    def test_huge_or_non_finite_numbers_and_long_text_are_refused(self):
        for value in ("1e400", "NaN", "Infinity", "99999999999999999"):
            with self.subTest(value):
                _a, errors = exit_survey.clean_answers(
                    self.survey, post(**{self.key(self.q_text): "ok", self.key(self.q_num): value})
                )
                self.assertIn(str(self.q_num.pk), errors)
        _a, errors = exit_survey.clean_answers(self.survey, post(**{self.key(self.q_text): "x" * 2001}))
        self.assertIn(str(self.q_text.pk), errors)

    def test_only_the_leaver_can_hand_it_in_and_only_once(self):
        answers, _e = exit_survey.clean_answers(self.survey, self.good_answers())
        with self.assertRaises(ExitError):
            exit_survey.submit(self.survey, self.mgr, answers)
        done = exit_survey.submit(self.survey, self.emp, answers)
        self.assertEqual(done.status, "completed")
        self.assertIsNotNone(done.completed_at)
        with self.assertRaises(ExitError):
            exit_survey.submit(self.survey, self.emp, answers)
        self.assertTrue(ExitActivity.objects.entire().filter(action="survey_done").exists())

    def test_a_cancelled_exit_cannot_be_answered(self):
        answers, _e = exit_survey.clean_answers(self.survey, self.good_answers())
        exit_service.cancel_exit(self.case, self.hr, "stays")
        with self.assertRaises(ExitError):
            exit_survey.submit(self.survey, self.emp, answers)

    def test_readable_values(self):
        answers, _e = exit_survey.clean_answers(self.survey, self.good_answers())
        exit_survey.submit(self.survey, self.emp, answers)
        self.survey.refresh_from_db()
        shown = {q["question"]: text for q, text in exit_survey.rows_for_display(self.survey)}
        self.assertEqual(shown["Rate your manager"], "4 / 5")
        self.assertEqual(shown["What did you like?"], "Team, Tools")
        self.assertEqual(shown["Would you return?"], "Yes")
        self.assertEqual(shown["Best day"], "01 Mar 2026")


class ExportTests(SurveyBase):
    def finished(self, employee=None, text="Better pay elsewhere"):
        case = self.case if employee is None else self.accepted_case(employee)
        survey = exit_survey.send_survey(case, self.hr)
        data = self.good_answers()
        data[self.key(self.q_text)] = text
        answers, errors = exit_survey.clean_answers(survey, data)
        self.assertEqual(errors, {})
        return exit_survey.submit(survey, case.employee_id, answers)

    def sheet(self, content):
        return list(load_workbook(BytesIO(content)).active.values)

    def test_the_workbook_has_a_row_per_leaver_and_a_column_per_question(self):
        self.finished()
        rows = self.sheet(exit_survey.build_workbook(exit_survey.responses().filter(status="completed")))
        self.assertEqual(rows[0][0], "Employee")
        self.assertIn("Why are you leaving?", rows[0])
        self.assertEqual(len(rows), 2)
        column = rows[0].index("Why are you leaving?")
        self.assertEqual(rows[1][column], "Better pay elsewhere")
        self.assertEqual(rows[1][0], self.emp.get_full_name())

    def test_text_that_looks_like_a_formula_is_defused(self):
        self.finished(text='=HYPERLINK("http://evil","x")')
        rows = self.sheet(exit_survey.build_workbook(exit_survey.responses().filter(status="completed")))
        column = rows[0].index("Why are you leaving?")
        self.assertTrue(rows[1][column].startswith("'="))

    def test_filters(self):
        self.finished()
        exit_survey.send_survey(self.accepted_case(self.other), self.hr)  # waiting
        self.assertEqual(exit_survey.responses(status="completed").count(), 1)
        self.assertEqual(exit_survey.responses(status="sent").count(), 1)
        self.assertEqual(exit_survey.responses(status="all").count(), 2)
        self.assertEqual(exit_survey.responses(status="all", query=self.emp.employee_last_name).count(), 1)
        self.assertEqual(exit_survey.responses(status="all", exit_type="termination").count(), 0)


class SurveyPageTests(SurveyBase):
    def fill_url(self):
        return reverse("exit-survey-fill")

    def test_the_leaver_sees_and_submits_their_form(self):
        exit_survey.send_survey(self.case, self.hr)
        self.client.force_login(self.emp_user)
        page = _full_page(self, self.fill_url()).content.decode()
        self.assertIn("Why are you leaving?", page)
        self.assertIn("Years here", page)
        with self.settings(DEBUG=True):  # full-layout page, see _full_page
            bad = self.client.post(self.fill_url(), {}, follow=False)
        self.assertEqual(bad.status_code, 200)
        self.assertIn("This question is required.", bad.content.decode())
        good = self.client.post(self.fill_url(), {
            self.key(self.q_text): "Pay", self.key(self.q_pick): "Pay",
        })
        self.assertEqual(good.status_code, 302)
        self.assertEqual(exit_survey.current_survey(self.case).status, "completed")
        again = self.client.get(self.fill_url())
        self.assertEqual(again.status_code, 302)  # nothing left to fill in

    def test_a_script_in_an_answer_shows_an_error_not_a_crash(self):
        exit_survey.send_survey(self.case, self.hr)
        self.client.force_login(self.emp_user)
        with self.settings(DEBUG=True):
            response = self.client.post(self.fill_url(), {self.key(self.q_text): "<script>x()</script>"})
        self.assertIn("not allowed", response.content.decode())
        self.assertEqual(exit_survey.current_survey(self.case).status, "sent")

    def test_other_people_have_nothing_to_fill_in(self):
        exit_survey.send_survey(self.case, self.hr)
        for user in (self.mgr_user, self.hr_user, self.other_user):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.fill_url()).status_code, 302)
        self.assertEqual(exit_survey.current_survey(self.case).status, "sent")

    def panel(self, user, section="survey"):
        self.client.force_login(user)
        return self.client.get(reverse("exit-manage", args=[self.case.pk, section]), **HX)

    def test_hr_sends_the_survey_from_the_manage_window(self):
        html = self.panel(self.hr_user).content.decode()
        self.assertIn("Send survey to employee", html)
        self.assertIn("Why are you leaving?", html)  # preview
        response = self.client.post(reverse("exit-survey-send", args=[self.case.pk]), **HX)
        self.assertIn("has been sent", response.content.decode())
        self.assertEqual(response["HX-Trigger"], "exitPanelChanged")
        again = self.client.post(reverse("exit-survey-send", args=[self.case.pk]), **HX)
        self.assertIn("already been sent", again.content.decode())

    def test_a_manager_can_see_the_status_but_not_send_or_read(self):
        exit_survey.send_survey(self.case, self.hr)
        html = self.panel(self.mgr_user).content.decode()
        self.assertIn("Waiting for the employee", html)
        self.assertNotIn("Remind", html)
        before = ExitSurvey.objects.entire().count()
        self.client.post(reverse("exit-survey-send", args=[self.case.pk]), **HX)
        self.assertEqual(ExitSurvey.objects.entire().count(), before)

    def test_only_hr_can_read_the_answers(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        url = reverse("exit-survey-view", args=[survey.pk])
        self.client.force_login(self.hr_user)
        self.assertNotIn("Better pay", self.client.get(url, **HX).content.decode())  # not filled in yet
        answers, _e = exit_survey.clean_answers(survey, self.good_answers())
        exit_survey.submit(survey, self.emp, answers)
        self.assertIn("Better pay elsewhere", self.client.get(url, **HX).content.decode())
        panel = self.panel(self.mgr_user).content.decode()
        self.assertNotIn("Better pay elsewhere", panel)
        self.assertNotIn("View responses", panel)
        self.client.force_login(self.mgr_user)
        self.assertNotIn("Better pay elsewhere", self.client.get(url, **HX).content.decode())
        self.client.force_login(self.emp_user)
        self.assertNotIn("Better pay elsewhere", self.client.get(url, **HX).content.decode())

    def test_hr_chases_a_survey(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        self.client.force_login(self.hr_user)
        url = reverse("exit-survey-remind", args=[survey.pk])
        self.assertIn("Reminder sent", self.client.post(url, **HX).content.decode())
        self.assertIn("less than an hour ago", self.client.post(url, **HX).content.decode())

    def test_the_menu_dot_and_progress_icon(self):
        html = self.panel(self.hr_user, "summary").content.decode()
        self.assertIn("Survey", html)
        progress = self.tab(self.hr_user, "progress").content.decode()
        self.assertIn('title="Survey - ', progress)

    def test_no_questions_means_no_survey_icon(self):
        ExitSurveyQuestion.objects.all().delete()
        self.assertNotIn('title="Survey"', self.tab(self.hr_user, "progress").content.decode())

    def test_the_responses_tab_and_export_are_for_hr_only(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        answers, _e = exit_survey.clean_answers(survey, self.good_answers())
        exit_survey.submit(survey, self.emp, answers)
        tab = reverse("exit-process-tab", args=["survey"])
        self.client.force_login(self.hr_user)
        self.assertIn(self.emp.get_full_name(), self.client.get(tab, **HX).content.decode())
        export = self.client.get(reverse("exit-survey-export"))
        self.assertEqual(export.status_code, 200)
        self.assertIn("spreadsheetml", export["Content-Type"])
        rows = list(load_workbook(BytesIO(export.content)).active.values)
        self.assertEqual(len(rows), 2)
        self.client.force_login(self.mgr_user)
        self.assertNotIn(self.emp.get_full_name(), self.client.get(tab, **HX).content.decode())
        denied = self.client.get(reverse("exit-survey-export"))
        self.assertNotIn("spreadsheetml", denied.get("Content-Type", ""))
        self.client.force_login(self.emp_user)
        self.assertNotIn("spreadsheetml", self.client.get(reverse("exit-survey-export")).get("Content-Type", ""))

    def test_the_menu_entry_shows_only_while_a_survey_is_waiting(self):
        def visible(user):
            fresh = type(user).objects.get(pk=user.pk)
            return sidebar.my_exit_survey_accessibility(SimpleNamespace(user=fresh), None, None)

        self.assertFalse(visible(self.emp_user))
        exit_survey.send_survey(self.case, self.hr)
        self.assertTrue(visible(self.emp_user))
        self.assertFalse(visible(self.mgr_user))


class SurveySettingsTests(SurveyBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)

    def test_the_page_lists_questions_and_options(self):
        html = _full_page(self, reverse("exit-survey-settings")).content.decode()
        self.assertIn("Why are you leaving?", html)
        self.assertIn("Add question", html)

    def test_only_people_with_the_settings_permission_get_in(self):
        self.client.force_login(self.emp_user)
        before = ExitSurveyQuestion.objects.count()
        self.client.post(reverse("exit-survey-question-new"), {"question": "Sneaky", "type": "text", "sequence": 1})
        self.assertEqual(ExitSurveyQuestion.objects.count(), before)

    def test_adding_editing_and_deleting_a_question(self):
        self.client.post(reverse("exit-survey-question-new"), {
            "question": "Would you recommend us?", "type": "options", "options": "Yes, No, Maybe",
            "sequence": 50, "is_mandatory": "on",
        })
        added = ExitSurveyQuestion.objects.get(question="Would you recommend us?")
        self.assertEqual((added.options, added.is_mandatory, added.is_active), ("Yes, No, Maybe", True, True))
        self.client.post(reverse("exit-survey-question-update", args=[added.pk]), {
            "question": "Would you recommend us to a friend?", "type": "options",
            "options": "Yes, No", "sequence": 50, "is_active": "on",
        })
        added.refresh_from_db()
        self.assertEqual((added.question, added.options, added.is_mandatory), ("Would you recommend us to a friend?", "Yes, No", False))
        self.client.post(reverse("exit-survey-question-update", args=[added.pk]), {
            "question": added.question, "type": "options", "options": "Yes, No", "sequence": 50,
        })
        added.refresh_from_db()
        self.assertFalse(added.is_active)  # unticked "On"
        self.client.post(reverse("exit-survey-question-delete", args=[added.pk]))
        self.assertFalse(ExitSurveyQuestion.objects.filter(pk=added.pk).exists())

    def test_bad_questions_are_refused_with_a_message(self):
        before = ExitSurveyQuestion.objects.count()
        for data in (
            {"question": "", "type": "text", "sequence": 1},
            {"question": "Pick", "type": "options", "options": "One", "sequence": 1},
            {"question": "<script>x()</script>", "type": "text", "sequence": 1},
        ):
            with self.subTest(data):
                with self.settings(DEBUG=True):
                    response = self.client.post(reverse("exit-survey-question-new"), data, follow=True)
                self.assertEqual(response.status_code, 200)
        self.assertEqual(ExitSurveyQuestion.objects.count(), before)

    def test_deleting_a_question_leaves_surveys_already_sent_alone(self):
        survey = exit_survey.send_survey(self.case, self.hr)
        self.client.post(reverse("exit-survey-question-delete", args=[self.q_text.pk]))
        survey.refresh_from_db()
        self.assertEqual(len(survey.questions), 7)

    def test_the_auto_send_switch(self):
        self.client.post(reverse("exit-survey-auto-send"), {"auto_send": "on"})
        self.assertTrue(OffboardingGeneralSetting.objects.entire().filter(exit_survey_auto_send=True).exists())
        self.client.post(reverse("exit-survey-auto-send"), {})
        self.assertFalse(OffboardingGeneralSetting.objects.entire().filter(exit_survey_auto_send=True).exists())

    def test_importing_from_recruitment_through_the_page(self):
        template = SurveyTemplate.objects.create(title="Hiring feedback")
        source = RecruitmentSurvey.objects.create(question="Was the interview fair?", type="checkbox", sequence=1)
        source.template_id.add(template)
        self.client.post(reverse("exit-survey-import"), {"template": template.pk})
        self.assertTrue(ExitSurveyQuestion.objects.filter(question="Was the interview fair?").exists())
        self.client.post(reverse("exit-survey-import"), {"template": 999999})  # nothing happens, no crash

    def test_company_scope_protects_organisation_wide_questions(self):
        from base.models import Company

        company = Company.objects.create(company="Acme", address="x", country="IN", state="x", city="x", zip="1")
        session = self.client.session
        session["selected_company"] = str(company.pk)
        session.save()
        self.client.post(reverse("exit-survey-question-delete", args=[self.q_text.pk]))
        self.assertTrue(ExitSurveyQuestion.objects.filter(pk=self.q_text.pk).exists())
        self.client.post(reverse("exit-survey-question-new"), {"question": "Acme only", "type": "text", "sequence": 9})
        self.assertEqual(ExitSurveyQuestion.objects.get(question="Acme only").company_id, company)
