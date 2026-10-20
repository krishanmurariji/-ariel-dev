"""
Manager of manager: Settings > Approvals > Manager of Manager lets the manager of an employee's
reporting manager see that employee's requests (and, with a second switch, approve / reject
them). It works next to the reporting manager and the second managers, which keep working as
they were.
"""

from unittest import mock

from django.core.exceptions import ObjectDoesNotExist
from django.test import override_settings
from django.urls import reverse

from base.sidebar import ApprovalsSettings
from employee.models import Employee, EmployeeWorkInformation, ManagerOfManagerSetting, SecondaryManager
from horilla.access import (
    APPROVE,
    VIEW,
    manager_access,
    manager_of_manager,
    managers_for,
)
from offboarding import exit_service, mailers
from offboarding.exit_service import ExitError
from offboarding.test_exit_pages import HX, PLAIN_STATIC, PageBase, _person


def fresh(employee):
    return Employee.objects.get(pk=employee.pk)


def set_setting(enabled, can_approve=False):
    ManagerOfManagerSetting.objects.all().delete()
    return ManagerOfManagerSetting.objects.create(enabled=enabled, can_approve=can_approve)


class ChainBase(PageBase):
    """boss manages mgr, mgr manages emp (their reporting manager); `other` is unrelated."""

    def setUp(self):
        super().setUp()
        self.boss_user, self.boss = _person()
        EmployeeWorkInformation.objects.filter(employee_id=self.mgr).update(reporting_manager_id=self.boss)

    def level(self, user, employee=None):
        return manager_access(user, fresh(employee or self.emp), "resignation")


class RuleTests(ChainBase):
    def test_off_by_default_the_manager_of_manager_has_no_access(self):
        self.assertIsNone(self.level(self.boss_user))  # no row at all
        set_setting(False, True)
        self.assertIsNone(self.level(self.boss_user))  # a stored approve switch does nothing while it is off

    def test_enabled_without_approve_they_can_only_view(self):
        set_setting(True, False)
        self.assertEqual(self.level(self.boss_user), VIEW)

    def test_enabled_with_approve_they_can_approve(self):
        set_setting(True, True)
        self.assertEqual(self.level(self.boss_user), APPROVE)

    def test_only_one_level_up_and_nobody_else(self):
        set_setting(True, True)
        top_user, top = _person()
        EmployeeWorkInformation.objects.filter(employee_id=self.boss).update(reporting_manager_id=top)
        self.assertIsNone(self.level(top_user))  # the manager's manager's manager
        self.assertIsNone(self.level(self.other_user))
        self.assertEqual(self.level(self.boss_user, self.mgr), APPROVE)  # the boss is the reporting manager of mgr

    def test_the_reporting_manager_is_not_changed(self):
        set_setting(True, False)
        self.assertEqual(self.level(self.mgr_user), APPROVE)

    def test_a_loop_in_the_hierarchy_gives_nobody_access_to_themselves(self):
        set_setting(True, True)
        EmployeeWorkInformation.objects.filter(employee_id=self.mgr).update(reporting_manager_id=self.emp)
        self.assertIsNone(manager_of_manager(fresh(self.emp)))
        self.assertIsNone(self.level(self.emp_user))

    def test_it_combines_with_second_managers_and_the_stronger_wins(self):
        SecondaryManager.objects.create(employee_id=self.emp, manager_id=self.boss, access={"resignation": APPROVE})
        set_setting(True, False)  # view only as manager of manager, but approve as a second manager
        self.assertEqual(self.level(self.boss_user), APPROVE)
        SecondaryManager.objects.all().update(access={"resignation": VIEW})
        set_setting(True, True)
        self.assertEqual(self.level(self.boss_user), APPROVE)

    def test_second_managers_keep_working_with_the_setting_off(self):
        set_setting(False)
        SecondaryManager.objects.create(employee_id=self.emp, manager_id=self.other, access={"resignation": VIEW})
        self.assertEqual(self.level(self.other_user), VIEW)

    def test_who_is_told(self):
        emp = fresh(self.emp)
        set_setting(False)
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", VIEW)], [self.mgr.pk])
        set_setting(True, False)
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", VIEW)], [self.mgr.pk, self.boss.pk])
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", APPROVE)], [self.mgr.pk])
        set_setting(True, True)
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", APPROVE)], [self.mgr.pk, self.boss.pk])

    def test_the_manager_of_manager_is_not_listed_twice(self):
        set_setting(True, True)
        SecondaryManager.objects.create(employee_id=self.emp, manager_id=self.boss, access={"resignation": VIEW})
        emp = fresh(self.emp)
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", VIEW)].count(self.boss.pk), 1)


class ExitProcessTests(ChainBase):
    def review_tab(self, user):
        return self.tab(user, "review").content.decode()

    def test_they_see_the_request_only_while_the_setting_is_on(self):
        name = self.emp.get_full_name()
        self.assertNotIn(name, self.review_tab(self.boss_user))
        set_setting(True)
        self.assertIn(name, self.review_tab(self.boss_user))
        set_setting(False)
        self.assertNotIn(name, self.review_tab(self.boss_user))

    def test_a_view_only_manager_sees_the_review_button_greyed_out_not_missing(self):
        set_setting(True, False)
        html = self.review_tab(self.boss_user)
        self.assertIn("disabled", html.split(self.emp.get_full_name())[1].split("</tr>")[0])
        self.assertNotIn(reverse("exit-review", args=[self.letter.pk]), html)  # no way to open it
        set_setting(True, True)
        html = self.review_tab(self.boss_user)
        self.assertIn(reverse("exit-review", args=[self.letter.pk]), html)

    def test_view_only_means_no_deciding_and_approve_means_deciding(self):
        set_setting(True, False)
        self.assertFalse(exit_service.can_review(self.boss_user, self.letter))
        set_setting(True, True)
        self.assertTrue(exit_service.can_review(self.boss_user, self.letter))

    def test_the_first_decision_still_stands(self):
        set_setting(True, True)
        exit_service.review_request(self.letter, self.boss, "reject", "no")
        with self.assertRaises(ExitError):
            exit_service.review_request(self.letter, self.mgr, "reject", "again")

    def test_they_are_told_about_a_new_resignation(self):
        set_setting(True)
        sent_to = []
        with mock.patch.object(mailers, "_notify_one_manager", side_effect=lambda l, m: sent_to.append(m.pk) or True):
            mailers.notify_manager_of_resignation(self.letter)
        self.assertEqual(sorted(sent_to), sorted([self.mgr.pk, self.boss.pk]))


@override_settings(STORAGES=PLAIN_STATIC)
class SettingsPageTests(ChainBase):
    def url(self):
        return reverse("manager-of-manager-settings")

    def toggle(self, user, field, on):
        self.client.force_login(user)
        data = {"field": field}
        if on:
            data["value"] = "true"
        return self.client.post(reverse("manager-of-manager-toggle"), data, **HX)

    def test_the_menu_has_a_separate_entry_under_approvals(self):
        labels = [str(item["label"]) for item in ApprovalsSettings.items]
        self.assertIn("Manager of Manager", labels)
        self.assertIn("Multiple Approval Rules", labels)  # the existing entry is still there

    def test_hr_opens_the_page_and_ordinary_people_do_not(self):
        self.client.force_login(self.hr_user)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertIn("Enable manager of manager", response.content.decode())
        self.assertIn("Allow approve / reject", response.content.decode())
        for user in (self.mgr_user, self.emp_user):
            self.client.force_login(user)
            self.assertNotIn("Enable manager of manager", self.client.get(self.url()).content.decode())

    def test_the_two_switches_save_independently(self):
        self.assertEqual(self.toggle(self.hr_user, "enabled", True).status_code, 204)
        row = ManagerOfManagerSetting.objects.get()
        self.assertEqual((row.enabled, row.can_approve), (True, False))
        self.toggle(self.hr_user, "can_approve", True)
        row.refresh_from_db()
        self.assertEqual((row.enabled, row.can_approve), (True, True))
        self.toggle(self.hr_user, "enabled", False)
        row.refresh_from_db()
        self.assertEqual((row.enabled, row.can_approve), (False, True))  # remembered, but inactive

    def test_ordinary_people_cannot_change_it_and_junk_is_refused(self):
        self.toggle(self.mgr_user, "enabled", True)
        self.assertFalse(ManagerOfManagerSetting.objects.filter(enabled=True).exists())
        self.assertEqual(self.toggle(self.hr_user, "something_else", True).status_code, 400)

    def test_the_approve_switch_is_greyed_out_until_it_is_enabled(self):
        self.client.force_login(self.hr_user)
        html = self.client.get(self.url()).content.decode()
        self.assertIn("Turn on Enable manager of manager first.", html)
        set_setting(True)
        html = self.client.get(self.url()).content.decode()
        self.assertNotIn("Turn on Enable manager of manager first.", html)
