"""
Second managers: HR gives an employee any number of second managers and chooses, for each,
whether that person may only see them ("view") or also decide ("approve"). Either manager
may decide and the first decision stands. Shown here for resignations.
"""

from unittest import mock

from django.urls import reverse

from employee.models import Employee, SecondaryManager
from horilla.access import APPROVE, VIEW, manager_access, managers_for
from offboarding import exit_service, mailers
from offboarding.exit_pages import _has_reports
from offboarding.exit_service import ExitError
from offboarding.test_exit_pages import HX, PageBase, _person


def fresh(employee):
    """The employee again, so a link made after the first load is seen (relations are cached)."""
    return Employee.objects.get(pk=employee.pk)


class SecondManagerBase(PageBase):
    def link(self, manager=None, **access):
        manager = manager or self.other
        return SecondaryManager.objects.create(employee_id=self.emp, manager_id=manager, access=access)


class AccessRuleTests(SecondManagerBase):
    def level(self, user):
        return manager_access(user, fresh(self.emp), "resignation")

    def test_the_reporting_manager_always_has_full_access(self):
        self.assertEqual(self.level(self.mgr_user), APPROVE)

    def test_a_second_manager_gets_exactly_what_was_chosen(self):
        link = self.link(resignation=VIEW)
        self.assertEqual(self.level(self.other_user), VIEW)
        link.access = {"resignation": APPROVE}
        link.save()
        self.assertEqual(self.level(self.other_user), APPROVE)

    def test_no_choice_for_this_kind_means_no_access(self):
        self.link(leave=APPROVE)  # a different kind of request
        self.assertIsNone(self.level(self.other_user))
        SecondaryManager.objects.all().update(access={"resignation": "nonsense"})
        self.assertIsNone(self.level(self.other_user))

    def test_it_is_for_that_employee_only(self):
        self.link(resignation=APPROVE)
        third_user, third = _person()
        self.assertIsNone(manager_access(self.other_user, fresh(third), "resignation"))
        self.assertIsNone(manager_access(third_user, fresh(self.emp), "resignation"))

    def test_a_user_with_no_employee_record_has_no_access(self):
        from horilla_auth.models import HorillaUser

        loner = HorillaUser.objects.create(username="no_employee_record", is_active=True)
        self.assertIsNone(manager_access(loner, fresh(self.emp), "resignation"))

    def test_who_is_told(self):
        self.link(resignation=VIEW)
        emp = fresh(self.emp)
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", VIEW)], [self.mgr.pk, self.other.pk])
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", APPROVE)], [self.mgr.pk])
        SecondaryManager.objects.all().update(access={})
        self.assertEqual([m.pk for m in managers_for(fresh(self.emp), "resignation", VIEW)], [self.mgr.pk])


class SeeingAndDecidingTests(SecondManagerBase):
    def review_tab(self, user):
        return self.tab(user, "review").content.decode()

    def test_a_second_manager_sees_the_request_only_when_given_access(self):
        name = self.emp.get_full_name()
        self.assertNotIn(name, self.review_tab(self.other_user))
        self.link(resignation=VIEW)
        self.assertIn(name, self.review_tab(self.other_user))

    def test_view_only_means_no_decide_buttons_and_no_deciding(self):
        self.link(resignation=VIEW)
        self.assertFalse(exit_service.can_review(self.other_user, self.letter))
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("exit-review", args=[self.letter.pk]), **HX)
        self.assertNotIn('id="xrForm"', response.content.decode())  # no review form

    def test_approve_access_lets_them_decide(self):
        self.link(resignation=APPROVE)
        self.assertTrue(exit_service.can_review(self.other_user, self.letter))
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("exit-review", args=[self.letter.pk]), **HX)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="xrForm"', response.content.decode())  # the review form

    def test_the_first_decision_stands(self):
        self.link(resignation=APPROVE)
        exit_service.review_request(self.letter, self.other, "reject", "not now")
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "rejected")
        with self.assertRaises(ExitError):  # the reporting manager is second to answer
            exit_service.review_request(self.letter, self.mgr, "accept", "ok", last_working_day=None)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.status, "rejected")

    def test_the_other_manager_is_told_who_decided(self):
        self.link(resignation=VIEW)
        told = []
        with mock.patch.object(exit_service, "_tell", side_effect=lambda r, a, v, *x, **k: told.append((r.pk, v))):
            exit_service.review_request(self.letter, self.mgr, "reject", "no")
        recipients = [pk for pk, _ in told]
        self.assertIn(self.other.pk, recipients)  # the second manager hears the outcome
        self.assertNotIn(self.mgr.pk, recipients)  # not the one who decided

    def test_a_second_manager_is_a_manager_for_the_page_and_scope(self):
        self.assertFalse(_has_reports(self.other))
        self.link(resignation=VIEW)
        self.assertTrue(_has_reports(self.other))
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(reverse("exit-process-tab", args=["review"]), **HX).status_code, 200)


class NotificationTests(SecondManagerBase):
    def test_a_new_resignation_is_mailed_to_both_managers(self):
        self.link(resignation=VIEW)
        sent_to = []
        with mock.patch.object(mailers, "_notify_one_manager", side_effect=lambda l, m: sent_to.append(m.pk) or True):
            self.assertTrue(mailers.notify_manager_of_resignation(self.letter))
        self.assertEqual(sorted(sent_to), sorted([self.mgr.pk, self.other.pk]))

    def test_a_second_manager_without_access_is_not_mailed(self):
        self.link()  # linked, but no access to resignations
        sent_to = []
        with mock.patch.object(mailers, "_notify_one_manager", side_effect=lambda l, m: sent_to.append(m.pk) or True):
            mailers.notify_manager_of_resignation(self.letter)
        self.assertEqual(sent_to, [self.mgr.pk])

    def test_a_reminder_goes_to_everyone_who_can_decide(self):
        self.link(resignation=APPROVE)
        reminded = []
        with mock.patch.object(mailers, "remind_reviewer", side_effect=lambda l, m, by=None: reminded.append(m.pk) or True):
            exit_service.send_reminder(self.letter, self.hr)
        self.assertEqual(sorted(reminded), sorted([self.mgr.pk, self.other.pk]))

    def test_a_view_only_second_manager_is_not_chased_to_decide(self):
        self.link(resignation=VIEW)
        reminded = []
        with mock.patch.object(mailers, "remind_reviewer", side_effect=lambda l, m, by=None: reminded.append(m.pk) or True):
            exit_service.send_reminder(self.letter, self.hr)
        self.assertEqual(reminded, [self.mgr.pk])


class SettingTheSecondManagersTests(SecondManagerBase):
    def url(self):
        return reverse("secondary-manager-edit", args=[self.emp.pk])

    def post(self, data):
        self.client.force_login(self.hr_user)
        return self.client.post(self.url(), data, **HX)

    def test_only_hr_can_open_it(self):
        self.client.force_login(self.hr_user)
        self.assertEqual(self.client.get(self.url(), **HX).status_code, 200)
        for user in (self.mgr_user, self.emp_user, self.other_user):
            self.client.force_login(user)
            response = self.client.get(self.url(), **HX)
            self.assertNotIn("Add a manager", response.content.decode(), user)

    def test_hr_adds_a_manager_and_what_they_can_do(self):
        response = self.post({"action": "add", "manager": self.other.pk, "access_resignation": APPROVE})
        self.assertEqual(response.status_code, 200)
        self.assertIn("The manager was added.", response.content.decode())
        link = SecondaryManager.objects.get(employee_id=self.emp)
        self.assertEqual((link.manager_id_id, link.access), (self.other.pk, {"resignation": APPROVE}))

    def test_choosing_no_access_adds_a_manager_with_no_rights(self):
        self.post({"action": "add", "manager": self.other.pk, "access_resignation": ""})
        self.assertEqual(SecondaryManager.objects.get(employee_id=self.emp).access, {})

    def test_hr_can_add_several_managers_each_with_their_own_access(self):
        third_user, third = _person()
        self.post({"action": "add", "manager": self.other.pk, "access_resignation": VIEW})
        self.post({"action": "add", "manager": third.pk, "access_resignation": APPROVE})
        found = {l.manager_id_id: l.access for l in SecondaryManager.objects.filter(employee_id=self.emp)}
        self.assertEqual(found, {self.other.pk: {"resignation": VIEW}, third.pk: {"resignation": APPROVE}})
        html = self.client.get(self.url(), **HX).content.decode()
        self.assertIn(self.other.get_full_name(), html)
        self.assertIn(third.get_full_name(), html)

    def test_the_same_person_is_not_added_twice(self):
        self.post({"action": "add", "manager": self.other.pk, "access_resignation": VIEW})
        response = self.post({"action": "add", "manager": self.other.pk, "access_resignation": APPROVE})
        self.assertEqual(SecondaryManager.objects.filter(employee_id=self.emp).count(), 1)
        self.assertIn("Select a valid choice", response.content.decode())  # no longer on offer

    def test_one_manager_can_be_changed_and_removed_without_touching_the_others(self):
        third_user, third = _person()
        first = self.link(resignation=VIEW)
        second = self.link(manager=third, resignation=VIEW)
        self.post({"action": "save", "link": first.pk, f"l{first.pk}-access_resignation": APPROVE})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.access, second.access), ({"resignation": APPROVE}, {"resignation": VIEW}))
        self.post({"action": "remove", "link": first.pk})
        self.assertEqual(list(SecondaryManager.objects.values_list("pk", flat=True)), [second.pk])

    def test_one_employees_links_cannot_be_changed_through_another(self):
        other_emp_user, other_emp = _person()
        foreign = SecondaryManager.objects.create(employee_id=other_emp, manager_id=self.other, access={})
        response = self.post({"action": "remove", "link": foreign.pk})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SecondaryManager.objects.filter(pk=foreign.pk).exists())

    def test_the_employee_and_the_reporting_manager_cannot_be_chosen(self):
        for bad in (self.emp.pk, self.mgr.pk):
            response = self.post({"action": "add", "manager": bad, "access_resignation": VIEW})
            self.assertEqual(response.status_code, 200)  # the form again, with an error
        self.assertFalse(SecondaryManager.objects.exists())

    def test_the_profile_offers_the_action_to_hr_only(self):
        from employee.cbv.accessibility import second_manager_accessibility

        class Req:
            def __init__(self, user):
                self.user = user

        self.assertTrue(second_manager_accessibility(Req(self.hr_user), self.emp))
        self.assertFalse(second_manager_accessibility(Req(self.mgr_user), self.emp))
        self.assertEqual(self.emp.get_second_manager_url(), self.url())


class SeveralSecondManagersTests(SecondManagerBase):
    def setUp(self):
        super().setUp()
        self.third_user, self.third = _person()

    def test_each_one_gets_exactly_their_own_access(self):
        self.link(resignation=VIEW)
        self.link(manager=self.third, resignation=APPROVE)
        emp = fresh(self.emp)
        self.assertEqual(manager_access(self.other_user, emp, "resignation"), VIEW)
        self.assertEqual(manager_access(self.third_user, emp, "resignation"), APPROVE)

    def test_everyone_with_access_is_told_and_only_deciders_are_reminded(self):
        self.link(resignation=VIEW)
        self.link(manager=self.third, resignation=APPROVE)
        emp = fresh(self.emp)
        self.assertEqual(
            [m.pk for m in managers_for(emp, "resignation", VIEW)], [self.mgr.pk, self.other.pk, self.third.pk]
        )
        self.assertEqual([m.pk for m in managers_for(emp, "resignation", APPROVE)], [self.mgr.pk, self.third.pk])

    def test_any_of_them_may_decide_and_the_first_decision_stands(self):
        self.link(resignation=APPROVE)
        self.link(manager=self.third, resignation=APPROVE)
        exit_service.review_request(self.letter, self.third, "reject", "no")
        for later in (self.other, self.mgr):
            with self.assertRaises(ExitError):
                exit_service.review_request(self.letter, later, "reject", "again")

    def test_the_review_list_shows_the_request_to_each_of_them(self):
        self.link(resignation=VIEW)
        self.link(manager=self.third, resignation=VIEW)
        for user in (self.other_user, self.third_user):
            html = self.tab(user, "review").content.decode()
            self.assertEqual(html.count(self.emp.get_full_name()), 1, user)  # once, not once per manager

    def test_all_of_them_hear_about_a_new_resignation(self):
        self.link(resignation=VIEW)
        self.link(manager=self.third, resignation=VIEW)
        sent_to = []
        with mock.patch.object(mailers, "_notify_one_manager", side_effect=lambda l, m: sent_to.append(m.pk) or True):
            mailers.notify_manager_of_resignation(self.letter)
        self.assertEqual(sorted(sent_to), sorted([self.mgr.pk, self.other.pk, self.third.pk]))
