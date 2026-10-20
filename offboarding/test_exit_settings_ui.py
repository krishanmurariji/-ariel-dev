"""
Exit Settings and Exit Survey settings, used from the page (htmx): every change
answers with a confirmation the page pops up, windows open for adding and editing,
the people on a notice period policy can be seen, moved and removed, and the
survey notification opens the survey.
"""

import datetime
import json

from django.contrib import messages
from django.contrib.messages import get_messages
from django.test import override_settings
from django.urls import reverse

from base.models import Company
from offboarding import exit_service, exit_survey
from offboarding.models import (
    EmployeeNoticePolicy,
    ExitReasonOption,
    ExitSurveyQuestion,
    NoticePeriodPolicy,
    OffboardingGeneralSetting,
)
from offboarding.test_exit_pages import HX, PLAIN_STATIC, _person
from horilla.isolated_testcase import IsolatedTestCase


def notice(response):
    """The confirmation the page will show: the Django message the view added (the
    application's own pop-up shows it) and whether the window closes."""
    last = list(get_messages(response.wsgi_request))[-1]
    close = json.loads(response["HX-Trigger"])["exitSettingsNotice"]["close"]
    kind = "success" if last.level == messages.SUCCESS else "error"
    return {"kind": kind, "message": str(last), "close": close}


@override_settings(STORAGES=PLAIN_STATIC)
class ExitSettingsInPageTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Co", address="a", country="c", state="s", city="c", zip="1"
        )
        self.admin_user, _ = _person(is_staff=True, is_superuser=True)
        self.plain_user, self.plain = _person()
        self.client.force_login(self.admin_user)

    def scope(self, value):
        session = self.client.session
        session["selected_company"] = value
        session.save()

    # -- a confirmation after every change --------------------------------------

    def test_saving_the_rules_confirms_and_does_not_reload(self):
        response = self.client.post(
            reverse("exit-settings-save"), {"review_mode": "approval", "last_working_day_holiday_rule": "none"}, **HX
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(notice(response), {"kind": "success", "message": "Exit settings saved.", "close": False})
        self.assertTrue(OffboardingGeneralSetting.objects.entire().filter(company_id__isnull=True).exists())

    def test_a_plain_post_still_returns_to_the_page(self):
        response = self.client.post(reverse("exit-settings-save"), {"review_mode": "approval", "last_working_day_holiday_rule": "none"})
        self.assertRedirects(response, reverse("exit-settings"), fetch_redirect_response=False)

    def test_a_refused_change_says_so_and_keeps_the_window_open(self):
        self.client.post(reverse("exit-reason-save"), {"title": "Health", "exit_type": "resignation"}, **HX)
        response = self.client.post(
            reverse("exit-reason-save"), {"title": "health", "exit_type": "resignation"}, **HX
        )
        self.assertEqual(notice(response)["kind"], "error")
        self.assertFalse(notice(response)["close"])
        self.assertEqual(ExitReasonOption.objects.entire().filter(title__iexact="health").count(), 1)

    def test_a_successful_change_tells_the_page_to_close_the_window_and_refresh(self):
        response = self.client.post(
            reverse("exit-reason-save"), {"title": "Health", "exit_type": "resignation"}, **HX
        )
        events = json.loads(response["HX-Trigger"])
        self.assertTrue(events["exitSettingsNotice"]["close"])
        self.assertIn("exitSettingsChanged", events)

    # -- exit reasons -----------------------------------------------------------

    def test_the_reason_window_opens_for_adding_and_renaming(self):
        add = self.client.get(reverse("exit-reason-form") + "?type=termination", **HX)
        self.assertContains(add, "Add an exit reason")
        self.assertContains(add, 'value="termination"')
        reason = ExitReasonOption.objects.entire().get(title="Relocating", exit_type="resignation")
        rename = self.client.get(reverse("exit-reason-edit-form", args=[reason.pk]), **HX)
        self.assertContains(rename, "Relocating")

    def test_a_reason_can_be_removed_even_after_it_was_used(self):
        reason = ExitReasonOption.objects.entire().get(title="Relocating", exit_type="resignation")
        response = self.client.post(reverse("exit-reason-delete", args=[reason.pk]), **HX)
        self.assertEqual(notice(response)["message"], "Reason removed.")
        self.assertFalse(ExitReasonOption.objects.entire().filter(pk=reason.pk).exists())

    def test_the_page_offers_to_remove_and_rename_each_reason(self):
        with self.settings(DEBUG=True):
            body = self.client.get(reverse("exit-settings")).content.decode()
        self.assertIn("Remove this reason", body)
        self.assertIn("Rename", body)

    # -- sections refresh on their own -------------------------------------------

    def test_sections_can_be_fetched_on_their_own(self):
        self.assertContains(self.client.get(reverse("exit-settings-section", args=["reasons"])), "Relocating")
        self.assertContains(self.client.get(reverse("exit-settings-section", args=["policies"])), "Notice period policies")
        self.assertEqual(self.client.get(reverse("exit-settings-section", args=["nope"])).status_code, 404)

    def test_only_those_with_the_permission_get_the_windows(self):
        self.client.force_login(self.plain_user)
        for url, content in (
            (reverse("exit-reason-form"), "Add an exit reason"),
            (reverse("notice-policy-form"), "New notice period policy"),
            (reverse("exit-settings-section", args=["reasons"]), "Exit reasons"),
            (reverse("exit-survey-question-form"), "Add a survey question"),
        ):
            self.assertNotContains(self.client.get(url, **HX), content, msg_prefix=url)

    # -- notice period policies: who is on them -----------------------------------

    def make_policy(self, title="Senior", days=60, **extra):
        return NoticePeriodPolicy.objects.create(title=title, days=days, **extra)

    def test_the_people_window_lists_who_is_on_the_policy_by_name(self):
        policy = self.make_policy()
        other = self.make_policy("Probation", 15)
        EmployeeNoticePolicy.objects.create(employee_id=self.plain, policy_id=policy)
        body = self.client.get(reverse("notice-policy-people", args=[policy.pk]), **HX).content.decode()
        self.assertIn(self.plain.get_full_name(), body)
        self.assertIn('"employees": "%s"' % self.plain.pk, body)  # their move / remove buttons
        self.assertIn("Remove", body)
        self.assertIn("Probation", body)  # a place to move them to
        elsewhere = self.client.get(reverse("notice-policy-people", args=[other.pk]), **HX).content.decode()
        self.assertNotIn('"employees": "%s"' % self.plain.pk, elsewhere)  # no move / remove buttons for them

    def test_removing_someone_puts_them_back_on_the_default_and_redraws_the_window(self):
        policy = self.make_policy()
        EmployeeNoticePolicy.objects.create(employee_id=self.plain, policy_id=policy)
        response = self.client.post(
            reverse("notice-policy-assign"),
            {"policy": "", "employees": [self.plain.pk], "viewing": policy.pk},
            **HX,
        )
        self.assertFalse(EmployeeNoticePolicy.objects.entire().exists())
        self.assertContains(response, "Nobody has been put on this policy yet.")
        self.assertEqual(notice(response)["kind"], "success")
        self.assertFalse(notice(response)["close"])  # the window stays open

    def test_moving_someone_to_another_policy(self):
        policy = self.make_policy()
        other = self.make_policy("Probation", 15)
        EmployeeNoticePolicy.objects.create(employee_id=self.plain, policy_id=policy)
        self.client.post(
            reverse("notice-policy-assign"),
            {"policy": other.pk, "employees": [self.plain.pk], "viewing": policy.pk},
            **HX,
        )
        self.assertEqual(EmployeeNoticePolicy.objects.entire().get(employee_id=self.plain).policy_id, other)

    def test_adding_people_from_the_window(self):
        policy = self.make_policy()
        response = self.client.post(
            reverse("notice-policy-assign"),
            {"policy": policy.pk, "employees": [self.plain.pk], "viewing": policy.pk},
            **HX,
        )
        self.assertContains(response, self.plain.get_full_name())
        self.assertEqual(EmployeeNoticePolicy.objects.entire().get(employee_id=self.plain).policy_id, policy)

    def test_choosing_nobody_is_refused_with_a_message(self):
        policy = self.make_policy()
        response = self.client.post(reverse("notice-policy-assign"), {"policy": policy.pk, "viewing": policy.pk}, **HX)
        self.assertEqual(notice(response)["kind"], "error")

    def test_a_policy_with_people_cannot_be_deleted_and_says_why(self):
        policy = self.make_policy()
        EmployeeNoticePolicy.objects.create(employee_id=self.plain, policy_id=policy)
        response = self.client.post(reverse("notice-policy-delete", args=[policy.pk]), **HX)
        self.assertEqual(notice(response)["kind"], "error")
        self.assertIn("Move them", notice(response)["message"])
        self.assertTrue(NoticePeriodPolicy.objects.entire().filter(pk=policy.pk).exists())

    def test_the_policy_windows_open_for_new_and_edit(self):
        policy = self.make_policy()
        self.assertContains(self.client.get(reverse("notice-policy-form"), **HX), "New notice period policy")
        self.assertContains(self.client.get(reverse("notice-policy-edit-form", args=[policy.pk]), **HX), "Senior")

    def test_the_page_shows_the_count_as_a_button_that_opens_the_names(self):
        policy = self.make_policy()
        EmployeeNoticePolicy.objects.create(employee_id=self.plain, policy_id=policy)
        with self.settings(DEBUG=True):
            body = self.client.get(reverse("exit-settings")).content.decode()
        self.assertIn(reverse("notice-policy-people", args=[policy.pk]), body)
        self.assertIn("1 employee", body)


@override_settings(STORAGES=PLAIN_STATIC)
class ExitSurveySettingsInPageTests(IsolatedTestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Co", address="a", country="c", state="s", city="c", zip="1"
        )
        self.admin_user, _ = _person(is_staff=True, is_superuser=True)
        self.client.force_login(self.admin_user)

    def make_question(self, text="Why are you leaving?", **extra):
        return ExitSurveyQuestion.objects.create(question=text, type="text", **extra)

    def test_the_question_window_opens_for_adding_and_editing(self):
        add = self.client.get(reverse("exit-survey-question-form"), **HX)
        self.assertContains(add, "Add a survey question")
        question = self.make_question()
        edit = self.client.get(reverse("exit-survey-question-edit-form", args=[question.pk]), **HX)
        self.assertContains(edit, "Edit survey question")
        self.assertContains(edit, "Why are you leaving?")

    def test_adding_a_question_confirms_and_the_list_refreshes(self):
        response = self.client.post(
            reverse("exit-survey-question-new"),
            {"question": "Any feedback?", "type": "textarea", "sequence": 1},
            **HX,
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(notice(response)["message"], "The question has been saved.")
        listing = self.client.get(reverse("exit-survey-questions-section"))
        self.assertContains(listing, "Any feedback?")

    def test_a_bad_question_is_refused_and_the_window_stays_open(self):
        response = self.client.post(
            reverse("exit-survey-question-new"),
            {"question": "Pick one", "type": "options", "options": "only-one", "sequence": 1},
            **HX,
        )
        self.assertEqual(notice(response)["kind"], "error")
        self.assertFalse(notice(response)["close"])
        self.assertFalse(ExitSurveyQuestion.objects.entire().exists())

    def test_deleting_a_question_confirms(self):
        question = self.make_question()
        response = self.client.post(reverse("exit-survey-question-delete", args=[question.pk]), **HX)
        self.assertIn("deleted", notice(response)["message"])
        self.assertFalse(ExitSurveyQuestion.objects.entire().filter(pk=question.pk).exists())

    def test_the_auto_send_switch_confirms_in_plain_words(self):
        on = self.client.post(reverse("exit-survey-auto-send"), {"auto_send": "on"}, **HX)
        self.assertIn("sent automatically when an exit starts", notice(on)["message"])
        off = self.client.post(reverse("exit-survey-auto-send"), {}, **HX)
        self.assertIn("only be sent when HR sends it", notice(off)["message"])

    def test_the_page_offers_the_send_times(self):
        with self.settings(DEBUG=True):
            body = self.client.get(reverse("exit-survey-settings")).content.decode()
        for text in (
            "When the exit starts",
            "1 week before the last working day",
            "2 days before the last working day",
            "1 day before the last working day",
        ):
            self.assertIn(text, body)
        self.assertNotIn("No email is sent", body)

    def test_organisation_wide_questions_cannot_be_opened_for_editing_inside_a_company(self):
        question = self.make_question()
        session = self.client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        response = self.client.get(reverse("exit-survey-question-edit-form", args=[question.pk]), **HX)
        self.assertEqual(response.status_code, 404)


@override_settings(STORAGES=PLAIN_STATIC)
class SurveyNotificationTests(IsolatedTestCase):
    def test_the_notification_opens_the_survey_page(self):
        leaver_user, leaver = _person()
        ExitSurveyQuestion.objects.create(question="Why?", type="text")
        case = exit_service.start_exit_case(
            leaver, exit_type="resignation", last_working_day=datetime.date.today() + datetime.timedelta(days=10)
        )
        exit_survey.send_survey(case, None)
        notification = leaver_user.notifications.order_by("-pk").first()
        self.assertIsNotNone(notification)
        self.assertEqual(notification.data["redirect"], reverse("exit-survey-fill"))


class SurveyEmailTests(IsolatedTestCase):
    """The survey reaches the leaver twice: a notification and an email."""

    def setUp(self):
        from django.core import mail

        self.mail = mail
        self.leaver_user, self.leaver = _person()
        self.leaver.email = "leaver@example.com"
        self.leaver.save()
        ExitSurveyQuestion.objects.create(question="Why?", type="text")
        self.case = exit_service.start_exit_case(
            self.leaver, exit_type="resignation",
            last_working_day=datetime.date.today() + datetime.timedelta(days=10),
        )

    def sent_to_leaver(self):
        return [m for m in self.mail.outbox if "leaver@example.com" in m.to]

    def test_sending_the_survey_emails_the_leaver_as_well(self):
        survey = exit_survey.send_survey(self.case, None)
        self.assertTrue(survey.mailed)
        self.assertEqual(len(self.sent_to_leaver()), 1)
        self.assertIn("exit survey", self.sent_to_leaver()[0].subject)
        self.assertTrue(self.leaver_user.notifications.exists())

    def test_a_reminder_is_emailed_too(self):
        survey = exit_survey.send_survey(self.case, None)
        self.mail.outbox.clear()
        reminded = exit_survey.remind(survey, None)
        self.assertTrue(reminded.mailed)
        mails = self.sent_to_leaver()
        self.assertEqual(len(mails), 1)
        self.assertIn("Reminder", mails[0].subject)

    def test_no_email_address_still_sends_the_notification(self):
        self.leaver.email = ""
        self.leaver.save()
        survey = exit_survey.send_survey(self.case, None)
        self.assertFalse(survey.mailed)
        self.assertEqual(self.sent_to_leaver(), [])
        self.assertTrue(self.leaver_user.notifications.exists())

    def test_automatic_sending_emails_as_well(self):
        OffboardingGeneralSetting.objects.create(exit_survey_auto_send=True)
        user, other = _person()
        other.email = "auto@example.com"
        other.save()
        exit_service.start_exit_case(
            other, exit_type="resignation",
            last_working_day=datetime.date.today() + datetime.timedelta(days=10),
        )
        self.assertEqual([m for m in self.mail.outbox if "auto@example.com" in m.to].__len__(), 1)


@override_settings(STORAGES=PLAIN_STATIC)
class SurveySendTimeTests(IsolatedTestCase):
    """The survey can go out when the exit starts, or 1 week / 2 days / 1 day before the last day."""

    def setUp(self):
        self.admin_user, _ = _person(is_staff=True, is_superuser=True)
        self.client.force_login(self.admin_user)
        ExitSurveyQuestion.objects.create(question="Why?", type="text")

    def setting(self, auto=True, before=None):
        return OffboardingGeneralSetting.objects.create(
            exit_survey_auto_send=auto, exit_survey_send_before_days=before
        )

    def exit_in(self, days):
        _user, employee = _person()
        return exit_service.start_exit_case(
            employee,
            exit_type="resignation",
            last_working_day=datetime.date.today() + datetime.timedelta(days=days),
        )

    def test_the_choice_is_saved_and_confirmed(self):
        response = self.client.post(
            reverse("exit-survey-auto-send"), {"auto_send": "on", "send_before": "7"}, **HX
        )
        self.assertIn("1 week before the last working day", notice(response)["message"])
        row = OffboardingGeneralSetting.objects.entire().get(company_id__isnull=True)
        self.assertEqual(row.exit_survey_send_before_days, 7)
        self.assertTrue(row.exit_survey_auto_send)

    def test_only_the_offered_times_are_accepted(self):
        self.client.post(reverse("exit-survey-auto-send"), {"auto_send": "on", "send_before": "5"}, **HX)
        row = OffboardingGeneralSetting.objects.entire().get(company_id__isnull=True)
        self.assertIsNone(row.exit_survey_send_before_days)  # back to "when the exit starts"

    def test_when_the_exit_starts_the_survey_goes_out_at_once(self):
        self.setting(before=None)
        self.assertIsNotNone(exit_survey.current_survey(self.exit_in(30)))

    def test_a_later_send_time_waits_for_its_day(self):
        self.setting(before=7)
        case = self.exit_in(30)
        self.assertIsNone(exit_survey.current_survey(case))
        self.assertEqual(exit_survey.send_due(), 0)

    def test_the_daily_job_sends_it_when_the_day_comes(self):
        row = self.setting(auto=False, before=7)
        early = self.exit_in(8)
        due = self.exit_in(7)
        OffboardingGeneralSetting.objects.filter(pk=row.pk).update(exit_survey_auto_send=True)
        self.assertEqual(exit_survey.send_due(), 1)
        self.assertIsNone(exit_survey.current_survey(early))
        self.assertIsNotNone(exit_survey.current_survey(due))
        self.assertEqual(exit_survey.send_due(), 0)  # never twice

    def test_two_days_and_one_day_before(self):
        self.setting(before=2)
        case = self.exit_in(3)
        self.assertEqual(exit_survey.send_due(today=datetime.date.today()), 0)
        self.assertEqual(exit_survey.send_due(today=datetime.date.today() + datetime.timedelta(days=1)), 1)
        self.assertIsNotNone(exit_survey.current_survey(case))

    def test_an_exit_that_starts_inside_the_window_is_sent_at_once(self):
        self.setting(before=7)
        self.assertIsNotNone(exit_survey.current_survey(self.exit_in(3)))

    def test_nothing_is_sent_when_automatic_sending_is_off(self):
        self.setting(auto=False, before=7)
        case = self.exit_in(3)
        self.assertEqual(exit_survey.send_due(), 0)
        self.assertIsNone(exit_survey.current_survey(case))

    def test_someone_already_sent_one_by_hr_is_left_alone(self):
        self.setting(before=7)
        case = self.exit_in(20)
        exit_survey.send_survey(case, None)
        self.assertEqual(exit_survey.send_due(today=datetime.date.today() + datetime.timedelta(days=15)), 0)
        self.assertEqual(case.exit_surveys.count(), 1)


from offboarding.test_exit_task_pages import _group_post  # noqa: E402
from offboarding.test_exit_tasks import TaskBase  # noqa: E402
from onboarding.models import TaskTemplate  # noqa: E402
from offboarding.models import ExitTaskGroupRule  # noqa: E402


@override_settings(STORAGES=PLAIN_STATIC)
class ExitTaskGroupsInPageTests(TaskBase):
    """Exit Tasks: a list, a window to add and edit a group, an on/off switch."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)

    def test_the_window_opens_for_a_new_group_and_for_editing(self):
        new = self.client.get(reverse("exit-task-group-new"), **HX)
        self.assertContains(new, "New task group")
        self.assertContains(new, "Add task")
        self.assertContains(new, "data-xu-wide")  # the wide window
        edit = self.client.get(reverse("exit-task-group-edit", args=[self.group.pk]), **HX)
        self.assertContains(edit, "Edit task group")
        self.assertContains(edit, "Return laptop")

    def test_saving_a_group_confirms_closes_the_window_and_refreshes(self):
        response = self.client.post(
            reverse("exit-task-group-new"), _group_post("Finance", rows=[{"title": "Clear advances"}]), **HX
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(notice(response), {"kind": "success", "message": "The task group has been saved.", "close": True})
        self.assertIn("exitSettingsChanged", json.loads(response["HX-Trigger"]))
        self.assertTrue(TaskTemplate.objects.entire().filter(title="Finance", phase="offboarding").exists())

    def test_a_group_with_a_problem_comes_back_in_the_window_with_the_message(self):
        response = self.client.post(reverse("exit-task-group-new"), _group_post("Empty", rows=[]), **HX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add at least one task")
        self.assertContains(response, "xgForm")  # the form again, not a refresh
        self.assertFalse(TaskTemplate.objects.entire().filter(title="Empty").exists())

    def test_the_switch_turns_a_group_off_and_on(self):
        off = self.client.post(reverse("exit-task-group-toggle", args=[self.group.pk]), **HX)
        self.assertIn("is now off", notice(off)["message"])
        self.assertFalse(ExitTaskGroupRule.objects.get(template_id=self.group).is_active)
        self.assertFalse(notice(off)["close"])
        on = self.client.post(reverse("exit-task-group-toggle", args=[self.group.pk]), **HX)
        self.assertIn("is now on", notice(on)["message"])

    def test_deleting_a_group_confirms(self):
        response = self.client.post(reverse("exit-task-group-delete", args=[self.group.pk]), **HX)
        self.assertIn("was deleted", notice(response)["message"])
        self.assertFalse(TaskTemplate.objects.entire().filter(pk=self.group.pk).exists())

    def test_the_list_refreshes_on_its_own(self):
        listing = self.client.get(reverse("exit-task-groups-section"))
        self.assertContains(listing, "IT clearance")
        self.assertContains(listing, "Return laptop")
        self.assertContains(listing, reverse("exit-task-group-edit", args=[self.group.pk]))

    def test_people_without_the_permission_get_nothing(self):
        self.client.force_login(self.emp_user)
        for url, content in (
            (reverse("exit-task-groups-section"), "IT clearance"),
            (reverse("exit-task-group-new"), "Add task"),
        ):
            self.assertNotContains(self.client.get(url, **HX), content, msg_prefix=url)
