"""
"Initiate exit" (HR or a manager starting an exit for an employee) used to send no
email at all. Now the employee, their reporting manager, any second managers and the
manager of manager (when that setting is on) are told - everyone once, and not the
person who clicked.
"""

import datetime
from unittest import mock

from django.core import mail
from django.urls import reverse

from employee.models import ManagerOfManagerSetting, SecondaryManager
from horilla.isolated_testcase import IsolatedTestCase
from offboarding.models import OffboardingEmployee, ResignationLetter
from offboarding.test_exit_pages import _person

HX = {"HTTP_HX_REQUEST": "true"}
TODAY = datetime.date.today()


class InitiateMailBase(IsolatedTestCase):
    def setUp(self):
        self.hr_user, self.hr = _person(is_staff=True, is_superuser=True)
        self.top_user, self.top = _person()  # manager of the manager
        self.mgr_user, self.mgr = _person(manager=self.top)
        self.emp_user, self.emp = _person(manager=self.mgr)
        self.loner_user, self.loner = _person()  # reports to nobody
        mail.outbox.clear()

    def enable_manager_of_manager(self, on=True):
        ManagerOfManagerSetting.objects.update_or_create(
            pk=1, defaults={"enabled": on, "can_approve": False}
        )

    def payload(self, employee, **kw):
        data = {
            "employee": employee.pk,
            "exit_type": "termination",
            "reason": "Misconduct",
            "discussed": "yes",
            "discussion_summary": "Internal discussion notes.",
            "notice_date": TODAY.isoformat(),
            "last_working_day": (TODAY + datetime.timedelta(days=15)).isoformat(),
            "rehire_eligible": "no",
            "rehire_comment": "Internal rehire note.",
        }
        data.update(kw)
        return data

    def initiate(self, user, employee, **kw):
        self.client.force_login(user)
        return self.client.post(reverse("exit-initiate"), self.payload(employee, **kw), **HX)

    def recipients(self):
        return sorted(address for m in mail.outbox for address in m.to)

    def mail_to(self, person):
        found = [m for m in mail.outbox if person.get_mail() in m.to]
        self.assertEqual(len(found), 1, "%s got %d mails" % (person.get_full_name(), len(found)))
        return found[0]


class WhoIsToldTests(InitiateMailBase):
    def test_hr_initiating_tells_the_employee_and_the_reporting_manager(self):
        response = self.initiate(self.hr_user, self.emp)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.recipients(), sorted([self.emp.get_mail(), self.mgr.get_mail()])
        )

    def test_the_manager_of_manager_is_told_when_that_setting_is_on(self):
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.emp)
        self.assertEqual(
            self.recipients(),
            sorted([self.emp.get_mail(), self.mgr.get_mail(), self.top.get_mail()]),
        )

    def test_the_manager_of_manager_is_not_told_when_the_setting_is_off(self):
        self.enable_manager_of_manager(False)
        self.initiate(self.hr_user, self.emp)
        self.assertNotIn(self.top.get_mail(), self.recipients())

    def test_the_person_who_initiated_is_not_mailed_about_their_own_action(self):
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.emp)
        self.assertNotIn(self.hr.get_mail(), self.recipients())

    def test_a_manager_initiating_is_left_out_but_everyone_else_is_told(self):
        self.enable_manager_of_manager(True)
        response = self.initiate(
            self.mgr_user, self.emp, exit_type="resignation", reason="Relocating"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.recipients(), sorted([self.emp.get_mail(), self.top.get_mail()])
        )

    def test_a_second_manager_is_told_and_nobody_gets_two_mails(self):
        self.enable_manager_of_manager(True)
        # the manager of manager is also a second manager: still one mail
        SecondaryManager.objects.create(
            employee_id=self.emp, manager_id=self.top, access={"resignation": "view"}
        )
        self.initiate(self.hr_user, self.emp)
        self.assertEqual(self.recipients().count(self.top.get_mail()), 1)
        self.assertEqual(len(mail.outbox), 3)

    def test_an_employee_with_no_manager_only_mails_the_employee(self):
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.loner)
        self.assertEqual(self.recipients(), [self.loner.get_mail()])

    def test_people_unrelated_to_the_employee_are_not_mailed(self):
        stranger_user, stranger = _person()
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.emp)
        self.assertNotIn(stranger.get_mail(), self.recipients())
        self.assertNotIn(self.loner.get_mail(), self.recipients())


class WhatTheMailSaysTests(InitiateMailBase):
    def test_each_person_is_greeted_by_their_own_name(self):
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.emp)
        for person in (self.emp, self.mgr, self.top):
            message = self.mail_to(person)
            self.assertTrue(
                message.body.startswith("Hi %s," % person.get_full_name()), person.get_full_name()
            )
            self.assertIn("Hi <strong>%s</strong>," % person.get_full_name(), message.alternatives[0][0])

    def test_the_employees_mail_is_about_them_and_leaves_the_reason_out(self):
        self.initiate(self.hr_user, self.emp)
        message = self.mail_to(self.emp)
        self.assertEqual(message.subject, "Your termination process has been initiated")
        self.assertIn("has initiated your termination process", message.body)
        self.assertIn("Notice date", message.body)
        self.assertIn("Last working day", message.body)
        self.assertNotIn("Misconduct", message.body)
        self.assertNotIn("Misconduct", message.alternatives[0][0])

    def test_the_managers_mail_names_the_employee_and_shows_the_reason_and_status(self):
        self.initiate(self.hr_user, self.emp)
        message = self.mail_to(self.mgr)
        self.assertEqual(message.subject, "Exit initiated: %s" % self.emp.get_full_name())
        self.assertIn(
            "has initiated the termination of %s" % self.emp.get_full_name(), message.body
        )
        self.assertIn("Reason : Misconduct", message.body)
        self.assertIn("Status : Accepted - the exit process has started", message.body)

    def test_a_manager_initiated_exit_says_it_is_under_review(self):
        self.initiate(self.mgr_user, self.emp, exit_type="resignation", reason="Relocating")
        self.enable_manager_of_manager(False)
        # the manager initiated, so the employee is the only recipient here
        message = self.mail_to(self.emp)
        self.assertIn("has initiated your resignation process", message.body)
        letter = ResignationLetter.objects.entire().get(employee_id=self.emp)
        self.assertEqual(letter.status, "requested")

    def test_internal_notes_never_appear_in_any_mail(self):
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.emp)
        for message in mail.outbox:
            self.assertNotIn("Internal discussion notes.", message.body)
            self.assertNotIn("Internal rehire note.", message.body)
            self.assertNotIn("Internal discussion notes.", message.alternatives[0][0])

    def test_the_dates_in_the_mail_are_the_final_ones(self):
        last = TODAY + datetime.timedelta(days=15)
        self.initiate(self.hr_user, self.emp, last_working_day=last.isoformat())
        message = self.mail_to(self.emp)
        self.assertIn(last.strftime("%d %b %Y"), message.body)
        self.assertIn(TODAY.strftime("%d %b %Y"), message.body)


class FailureTests(InitiateMailBase):
    def test_a_mail_failure_does_not_undo_or_break_the_exit(self):
        with mock.patch(
            "offboarding.mailers.build_letterhead_email", side_effect=RuntimeError("smtp down")
        ):
            response = self.initiate(self.hr_user, self.emp)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            OffboardingEmployee.objects.entire().get(employee_id=self.emp).status, "in_progress"
        )

    def test_one_person_without_an_email_does_not_stop_the_others(self):
        from employee.models import Employee, EmployeeWorkInformation

        Employee.objects.filter(pk=self.mgr.pk).update(email="")
        EmployeeWorkInformation.objects.filter(employee_id=self.mgr).update(email="")
        self.enable_manager_of_manager(True)
        self.initiate(self.hr_user, self.emp)
        self.assertIn(self.emp.get_mail(), self.recipients())
        self.assertIn(self.top.get_mail(), self.recipients())

    def test_a_refused_form_sends_nothing(self):
        response = self.initiate(self.hr_user, self.emp, reason="Not a real reason")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_mail_is_not_sent_when_the_employee_is_not_allowed_to_be_picked(self):
        self.initiate(self.hr_user, self.emp)
        mail.outbox.clear()
        # already leaving: the form refuses, and nothing is mailed a second time
        response = self.initiate(self.hr_user, self.emp)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
