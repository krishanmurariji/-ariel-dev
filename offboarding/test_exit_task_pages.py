"""
Exit tasks on screen: the Tasks section of the Manage panel, the task action
windows, My Exit Tasks, the Exit Tasks tracking tab, the Exit Tasks settings
builder - and that onboarding never sees any of it.
"""

import datetime
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from base.models import Department
from onboarding.forms import ChecklistTemplateSelectForm, TaskTemplateForm
from onboarding.models import TaskTemplate
from offboarding import exit_service, exit_tasks, sidebar
from offboarding.models import ExitTask, ExitTaskConfig, ExitTaskGroupRule
from offboarding.test_exit_pages import HX, PLAIN_STATIC, TODAY, _full_page, _person
from offboarding.test_exit_tasks import TaskBase, make_group

DAY = datetime.timedelta


class TaskPageBase(TaskBase):
    def fire(self, title):
        task = self.task(title)
        exit_tasks.trigger_due(today=task.trigger_date)
        return self.task(title)

    def act(self, user, task, action, data=None, next_page="panel", **extra):
        self.client.force_login(user)
        payload = {"next": next_page}
        payload.update(data or {})
        return self.client.post(
            reverse("exit-task-action", args=[task.pk, action]), payload, **HX, **extra
        )


class PanelTasksTests(TaskPageBase):
    def panel(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("exit-manage", args=[self.case.pk, "tasks"]), **HX)

    def test_hr_sees_the_tasks_grouped_with_owners_and_dates(self):
        html = self.panel(self.hr_user).content.decode()
        for text in ("IT clearance", "Return laptop", "Exit interview", "Revoke access"):
            self.assertIn(text, html)
        self.assertIn("0 / 3", html.replace("<strong>0 / 3</strong>", "0 / 3"))
        self.assertIn("Skip", html)

    def test_the_menu_has_a_tasks_button_with_a_dot(self):
        self.client.force_login(self.hr_user)
        html = self.client.get(reverse("exit-manage", args=[self.case.pk, "summary"]), **HX).content.decode()
        self.assertIn('exit-process/manage/%d/tasks/' % self.case.pk, html)

    def test_a_manager_sees_the_list_but_only_their_own_buttons(self):
        html = self.panel(self.mgr_user).content.decode()
        self.assertIn("Return laptop", html)
        self.assertIn("Complete", html)  # they own the exit interview
        self.assertNotIn("Skip", html)
        self.assertNotIn("Change owner", html)

    def test_the_owner_completes_a_task_from_the_panel(self):
        task = self.task("Exit interview")
        self.client.force_login(self.mgr_user)
        form = self.client.get(
            reverse("exit-task-action", args=[task.pk, "complete"]) + "?next=panel", **HX
        )
        self.assertContains(form, "Exit interview")
        response = self.act(self.mgr_user, task, "complete", {"note": "went well"})
        self.assertIn("The task has been completed.", response.content.decode())
        task.refresh_from_db()
        self.assertEqual(task.status, "review")  # HR approves it
        self.assertEqual(task.note, "went well")

    def test_a_task_owner_without_panel_access_just_gets_a_refresh(self):
        task = self.fire("Return laptop")
        response = self.act(self.it_user, task, "complete", {"note": "back"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Refresh"], "true")
        task.refresh_from_db()
        self.assertEqual(task.status, "done")

    def test_a_required_file_is_enforced_on_the_form(self):
        task = self.fire("Revoke access")
        refused = self.act(self.emp_user, task, "complete", {"note": "done"}, next_page="mine")
        self.assertIn("attach the required file", refused.content.decode())
        task.refresh_from_db()
        self.assertEqual(task.status, "pending")
        upload = SimpleUploadedFile("p.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        ok = self.act(self.emp_user, task, "complete", {"note": "done", "attachment": upload}, next_page="mine")
        self.assertEqual(ok.status_code, 204)
        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        self.assertTrue(task.attachment)

    def test_script_in_a_note_shows_an_error_not_a_crash(self):
        task = self.task("Exit interview")
        response = self.act(self.mgr_user, task, "complete", {"note": "<script>x()</script>"})
        self.assertIn("not allowed", response.content.decode())
        task.refresh_from_db()
        self.assertEqual(task.status, "pending")

    def test_people_who_are_not_on_the_task_cannot_act_on_it(self):
        task = self.task("Exit interview")
        for user in (self.emp_user, self.other_user, self.it_user):
            for action in ("complete", "review", "skip", "owner"):
                with self.subTest(user=user.username, action=action):
                    self.act(user, task, action, {"note": "x", "reason": "x", "new_owner": self.emp.pk})
        task.refresh_from_db()
        self.assertEqual((task.status, task.owner_id), ("pending", self.mgr))

    def test_the_approver_approves_or_sends_back(self):
        task = self.task("Exit interview")
        exit_tasks.complete_task(task, self.mgr, "held")
        back = self.act(self.hr_user, task, "review", {"decision": "send_back", "comment": ""})
        self.assertIn("A comment is required", back.content.decode())
        self.act(self.hr_user, task, "review", {"decision": "send_back", "comment": "add notes"})
        task.refresh_from_db()
        self.assertEqual((task.status, task.review_comment), ("pending", "add notes"))
        exit_tasks.complete_task(task, self.mgr, "again")
        self.act(self.hr_user, task, "review", {"decision": "approve", "comment": ""})
        task.refresh_from_db()
        self.assertEqual(task.status, "done")

    def test_hr_skips_and_reassigns(self):
        task = self.task("Exit interview")
        empty = self.act(self.hr_user, task, "skip", {"reason": ""})
        self.assertIn("A reason is required", empty.content.decode())
        self.act(self.hr_user, task, "owner", {"new_owner": self.other.pk})
        task.refresh_from_db()
        self.assertEqual(task.owner_id, self.other)
        self.act(self.hr_user, task, "skip", {"reason": "not needed"})
        task.refresh_from_db()
        self.assertEqual(task.status, "skipped")

    def test_remind_from_the_panel_and_its_rate_limit(self):
        task = self.task("Exit interview")
        self.client.force_login(self.hr_user)
        first = self.client.post(reverse("exit-task-remind", args=[task.pk]), **HX)
        self.assertIn("Reminder sent", first.content.decode())
        second = self.client.post(reverse("exit-task-remind", args=[task.pk]), **HX)
        self.assertIn("less than an hour ago", second.content.decode())

    def test_a_manager_cannot_remind(self):
        task = self.task("Exit interview")
        self.client.force_login(self.mgr_user)
        self.client.post(reverse("exit-task-remind", args=[task.pk]), **HX)
        task.refresh_from_db()
        self.assertIsNone(task.last_reminded_at)

    def test_the_tasks_icon_appears_on_the_progress_tab(self):
        html = self.tab(self.hr_user, "progress").content.decode()
        self.assertIn('title="Tasks - ', html)


class MyTasksTests(TaskPageBase):
    def test_the_page_lists_what_is_waiting_on_me(self):
        self.fire("Return laptop")
        self.client.force_login(self.it_user)
        html = _full_page(self, reverse("exit-my-tasks")).content.decode()
        self.assertIn("Return laptop", html)
        self.assertNotIn("Exit interview", html)

    def test_an_approver_sees_it_under_to_approve(self):
        exit_tasks.complete_task(self.task("Exit interview"), self.mgr, "")
        self.client.force_login(self.hr_user)
        html = _full_page(self, reverse("exit-my-tasks")).content.decode()
        self.assertIn("To approve (1)", html)

    def test_the_menu_entry_only_shows_for_people_with_work(self):
        def visible(user):
            return sidebar.my_exit_tasks_accessibility(SimpleNamespace(user=user), None, None)

        self.fire("Return laptop")
        self.it_user = type(self.it_user).objects.get(pk=self.it_user.pk)
        self.assertTrue(visible(self.it_user))
        self.assertFalse(visible(type(self.other_user).objects.get(pk=self.other_user.pk)))


class MenuSafetyTests(TaskPageBase):
    def test_a_failing_lookup_hides_the_menu_entry_instead_of_breaking_pages(self):
        from unittest import mock

        request = SimpleNamespace(user=self.hr_user)
        with mock.patch("offboarding.exit_tasks.has_work", side_effect=RuntimeError("no table")):
            with self.assertLogs("offboarding.sidebar", level="WARNING"):
                self.assertFalse(sidebar.my_exit_tasks_accessibility(request, None, None))
                self.assertTrue(sidebar.offboarding_accessibility(request, None, None))  # HR still sees the menu


class TrackingTabTests(TaskPageBase):
    def get(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("exit-process-tab", args=["tasks"]), params, **HX)

    def test_by_task_lists_open_tasks_by_default(self):
        html = self.get(self.hr_user).content.decode()
        self.assertIn("Exit interview", html)
        self.assertNotIn("Return laptop", html)  # still scheduled
        scheduled = self.get(self.hr_user, status="scheduled").content.decode()
        self.assertIn("Return laptop", scheduled)

    def test_search_by_employee(self):
        hit = self.get(self.hr_user, q=self.emp.employee_last_name).content.decode()
        self.assertIn("Exit interview", hit)
        miss = self.get(self.hr_user, q="nobodyatall").content.decode()
        self.assertNotIn("Exit interview", miss)

    def test_by_employee_shows_progress(self):
        exit_tasks.skip_task(self.task("Return laptop"), self.hr, "n/a")
        html = self.get(self.hr_user, view="employee").content.decode()
        self.assertIn(self.emp.get_full_name(), html)
        self.assertIn("1 / 3", html)

    def test_a_manager_sees_only_their_reports_tasks(self):
        other_case = self.accepted_case(self.other)
        mine = self.get(self.mgr_user, status="all").content.decode()
        self.assertIn(self.emp.get_full_name(), mine)
        self.assertNotIn(self.other.get_full_name(), mine)
        self.assertTrue(ExitTask.objects.entire().filter(case_id=other_case).exists())

    def test_an_ordinary_employee_is_turned_away(self):
        self.assertEqual(self.get(self.emp_user).status_code, 302)

    def test_the_page_shows_the_tab_and_its_count(self):
        self.client.force_login(self.hr_user)
        html = _full_page(self, reverse("exit-process")).content.decode()
        self.assertIn("Exit Tasks", html)


# ---------------------------------------------------------------- settings


def _formset(rows, initial=0):
    data = {
        "tasks-TOTAL_FORMS": str(len(rows)),
        "tasks-INITIAL_FORMS": str(initial),
        "tasks-MIN_NUM_FORMS": "0",
        "tasks-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        row = dict(row)
        row.setdefault("owner_type", "reporting_manager")
        row.setdefault("approver_type", "none")
        row.setdefault("trigger_when", "before")
        row.setdefault("trigger_days", "7")
        row.setdefault("due_after_days", "3")
        for key, value in row.items():
            if value is True:
                value = "on"
            if value is False or value is None:
                continue
            data[f"tasks-{i}-{key}"] = value
    return data


def _group_post(title="New group", rows=(), initial=0, **extra):
    data = {"title": title, "exit_type": "all", "is_active": "on"}
    data.update(extra)
    data.update(_formset(list(rows), initial))
    return data


class SettingsBuilderTests(TaskPageBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.hr_user)

    def test_the_list_and_form_pages_open(self):
        listing = _full_page(self, reverse("exit-task-groups")).content.decode()
        self.assertIn("IT clearance", listing)
        self.assertIn("Return laptop", listing)
        form = _full_page(self, reverse("exit-task-group-new")).content.decode()
        self.assertIn("Add task", form)
        edit = _full_page(self, reverse("exit-task-group-edit", args=[self.group.pk])).content.decode()
        self.assertIn("Return laptop", edit)

    def test_only_people_with_the_settings_permission_get_in(self):
        self.client.force_login(self.emp_user)
        response = self.client.post(reverse("exit-task-group-new"), _group_post(rows=[{"title": "x"}]))
        self.assertFalse(TaskTemplate.objects.entire().filter(title="New group").exists())
        self.assertNotEqual(response.status_code, 200)

    def test_creating_a_group_saves_its_tasks_rule_and_settings(self):
        sales = Department.objects.create(department="Sales")
        response = self.client.post(
            reverse("exit-task-group-new"),
            _group_post(
                "Finance clearance",
                rows=[
                    {"title": "Clear advances", "description": "Check payroll", "owner_type": "specific",
                     "owner_person": self.it.pk, "approver_type": "reporting_manager",
                     "trigger_when": "after", "trigger_days": "2", "due_after_days": "5",
                     "mandatory_attachment": True},
                    {"title": "Final handover"},
                ],
                exit_type="termination",
                include_departments=[sales.pk],
            ),
        )
        self.assertEqual(response.status_code, 302)
        group = TaskTemplate.objects.entire().get(title="Finance clearance")
        self.assertEqual(group.phase, "offboarding")
        rule = group.exit_rule
        self.assertEqual((rule.exit_type, rule.is_active), ("termination", True))
        self.assertEqual(list(rule.include_departments.all()), [sales])
        first, second = list(group.tasks.order_by("sequence"))
        self.assertEqual((first.title, first.sequence, second.sequence), ("Clear advances", 0, 1))
        config = first.exit_config
        self.assertEqual(config.owner_person, self.it)
        self.assertEqual(config.approver_type, "reporting_manager")
        self.assertEqual((config.trigger_offset_days, config.due_after_days), (2, 5))
        self.assertTrue(config.mandatory_attachment)
        self.assertEqual(second.exit_config.trigger_offset_days, -7)

    def test_validation_messages(self):
        url = reverse("exit-task-group-new")
        cases = {
            "This field is required": _group_post(" ", rows=[{"title": "x"}]),
            "Add at least one task": _group_post("G1", rows=[]),
            "Please choose the person": _group_post("G2", rows=[{"title": "x", "owner_type": "specific"}]),
            "already exists": _group_post("IT clearance", rows=[{"title": "x"}]),
            "not allowed": _group_post("G3", rows=[{"title": "<script>x()</script>"}]),
        }
        for message, data in cases.items():
            with self.subTest(message):
                response = _full_page_post(self, url, data)
                self.assertIn(message, response.content.decode())
        self.assertFalse(TaskTemplate.objects.entire().filter(title__in=["G1", "G2", "G3"]).exists())

    def test_a_blank_extra_row_is_ignored(self):
        self.client.post(
            reverse("exit-task-group-new"),
            _group_post("With blank", rows=[{"title": "Real"}, {"title": ""}]),
        )
        self.assertEqual(TaskTemplate.objects.entire().get(title="With blank").tasks.count(), 1)

    def test_editing_keeps_task_identity_and_can_remove_and_add(self):
        laptop, interview, access = list(self.group.tasks.order_by("sequence"))
        rows = [
            {"task_pk": laptop.pk, "title": "Return laptop + charger", "owner_type": "specific", "owner_person": self.it.pk},
            {"task_pk": interview.pk, "title": interview.title, "DELETE": True},
            {"task_pk": access.pk, "title": access.title, "owner_type": "employee", "mandatory_attachment": True},
            {"title": "Brand new"},
        ]
        response = self.client.post(
            reverse("exit-task-group-edit", args=[self.group.pk]),
            _group_post("IT clearance", rows=rows, initial=3),
        )
        self.assertEqual(response.status_code, 302)
        titles = list(self.group.tasks.order_by("sequence").values_list("title", flat=True))
        self.assertEqual(titles, ["Return laptop + charger", "Revoke access", "Brand new"])
        laptop.refresh_from_db()
        self.assertEqual(laptop.pk, self.group.tasks.get(title="Return laptop + charger").pk)
        self.assertFalse(TaskTemplate.objects.entire().get(pk=self.group.pk).tasks.filter(pk=interview.pk).exists())

    def test_turning_a_group_off_stops_it_joining_new_exits(self):
        self.client.post(reverse("exit-task-group-toggle", args=[self.group.pk]))
        self.assertFalse(ExitTaskGroupRule.objects.entire().get(template_id=self.group).is_active)
        third_user, third = _person(manager=self.mgr)
        case = self.accepted_case(third)
        self.assertEqual(ExitTask.objects.entire().filter(case_id=case).count(), 0)
        self.client.post(reverse("exit-task-group-toggle", args=[self.group.pk]))
        self.assertTrue(ExitTaskGroupRule.objects.entire().get(template_id=self.group).is_active)

    def test_deleting_a_group_leaves_running_exits_alone(self):
        before = ExitTask.objects.entire().filter(case_id=self.case).count()
        self.client.post(reverse("exit-task-group-delete", args=[self.group.pk]))
        self.assertFalse(TaskTemplate.objects.entire().filter(pk=self.group.pk).exists())
        rows = ExitTask.objects.entire().filter(case_id=self.case)
        self.assertEqual(rows.count(), before)
        self.assertTrue(all(r.template_task_id is None for r in rows))

    def test_company_scope_protects_organisation_wide_groups(self):
        from base.models import Company

        company = Company.objects.create(company="Acme", address="x", country="IN", state="x", city="x", zip="1")
        session = self.client.session
        session["selected_company"] = str(company.pk)
        session.save()
        self.client.post(reverse("exit-task-group-delete", args=[self.group.pk]))
        self.assertTrue(TaskTemplate.objects.entire().filter(pk=self.group.pk).exists())
        self.client.post(
            reverse("exit-task-group-new"), _group_post("Acme only", rows=[{"title": "x"}])
        )
        self.assertEqual(TaskTemplate.objects.entire().get(title="Acme only").company_id, company)


def _full_page_post(test, url, data):
    with test.settings(DEBUG=True):
        return test.client.post(url, data)


class OnboardingIsolationTests(TaskPageBase):
    def test_the_onboarding_group_list_does_not_show_exit_groups(self):
        TaskTemplate.objects.create(title="Welcome kit", phase="post_joining")
        self.client.force_login(self.hr_user)
        html = _full_page(self, reverse("task-template-list")).content.decode()
        self.assertIn("Welcome kit", html)
        self.assertNotIn("IT clearance", html)

    def test_onboarding_cannot_open_edit_or_delete_an_exit_group(self):
        self.client.force_login(self.hr_user)
        self.client.post(reverse("task-template-delete", args=[self.group.pk]))
        self.assertTrue(TaskTemplate.objects.entire().filter(pk=self.group.pk).exists())
        with override_settings(STORAGES=PLAIN_STATIC):  # the themed 404 page
            modal = self.client.get(reverse("task-template-view-modal", args=[self.group.pk]))
        self.assertEqual(modal.status_code, 404)

    def test_checklist_steps_and_the_group_form_do_not_offer_exit_groups(self):
        self.assertNotIn(self.group, ChecklistTemplateSelectForm().fields["templates"].queryset)
        for phase in ("pre_joining", "post_joining", "employee_checklist"):
            queryset = ChecklistTemplateSelectForm(phase=phase).fields["templates"].queryset
            self.assertNotIn(self.group, queryset)
        phases = [key for key, _ in TaskTemplateForm().fields["phase"].choices]
        self.assertNotIn("offboarding", phases)


class TaskWindowLayoutTests(TaskPageBase):
    """Every task window shows its fields the way the rest of the app does."""

    def window(self, user, task, action):
        self.client.force_login(user)
        return self.client.get(
            reverse("exit-task-action", args=[task.pk, action]) + "?next=panel", **HX
        ).content.decode()

    def test_the_approval_choice_is_two_small_radio_buttons_with_a_label(self):
        task = self.task("Exit interview")
        exit_tasks.complete_task(task, self.mgr, "held")
        html = self.window(self.hr_user, task, "review")
        self.assertIn("Decision", html)
        self.assertIn('class="xt-radios"', html)
        self.assertEqual(html.count('type="radio"'), 2)
        self.assertNotIn('oh-input w-100" type="radio"', html)
        self.assertNotIn('type="radio" name="decision" class="oh-input', html)
        for text in ("Approve", "Send back"):
            self.assertIn(text, html)

    def test_text_fields_use_the_app_input_style_and_every_window_opens(self):
        task = self.task("Exit interview")
        for user, action, expected in (
            (self.mgr_user, "complete", "oh-input w-100"),
            (self.hr_user, "skip", "oh-input w-100"),
            (self.hr_user, "owner", "oh-select w-100"),
        ):
            with self.subTest(action=action):
                self.assertIn(expected, self.window(user, task, action))
