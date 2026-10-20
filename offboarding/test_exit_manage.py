"""
The Manage panel of an exit in progress: recovering assets, handing the team
over, extending notice for leave, and the panel windows themselves.
"""

import datetime
import itertools

from django.contrib.auth.models import Permission
from django.urls import reverse

from asset.models import Asset, AssetAssignment, AssetCategory
from leave.models import LeaveRequest, LeaveType
from offboarding import exit_manage, exit_service
from offboarding.exit_service import ExitError
from offboarding.models import ExitActivity, OffboardingEmployee
from offboarding.test_exit_pages import HX, TODAY, PageBase, _letter, _person

_n = itertools.count()
DAY = datetime.timedelta


def _asset(name="Laptop", quantity=1):
    category = AssetCategory.objects.create(asset_category_name=f"cat{next(_n)}")
    return Asset.objects.create(
        asset_name=name,
        asset_tracking_id=f"TRK{next(_n):05d}",
        asset_purchase_date=TODAY - DAY(days=200),
        asset_purchase_cost=1000,
        asset_category_id=category,
        asset_status="In use",
        quantity=quantity,
    )


def _give(asset, employee, by):
    return AssetAssignment.objects.create(
        asset_id=asset, assigned_to_employee_id=employee, assigned_by_employee_id=by
    )


def _leave(employee, start, end, days, status="approved"):
    # bulk_create skips LeaveType/LeaveRequest.save(), which want a live request
    leave_type = LeaveType.objects.first()
    if leave_type is None:
        leave_type = LeaveType.objects.bulk_create([LeaveType(name="Casual")])[0]
    return LeaveRequest.objects.bulk_create(
        [
            LeaveRequest(
                employee_id=employee,
                leave_type_id=leave_type,
                start_date=start,
                end_date=end,
                requested_days=days,
                description="x",
                status=status,
            )
        ]
    )[0]


class ManageBase(PageBase):
    def setUp(self):
        super().setUp()
        self.case = self.accepted_case()  # emp, notice today, 20 days to go


class AssetTests(ManageBase):
    def setUp(self):
        super().setUp()
        self.asset = _asset()
        self.given = _give(self.asset, self.emp, self.hr)

    def test_lists_what_the_employee_still_holds(self):
        self.assertEqual([a.pk for a in exit_manage.open_assets(self.emp)], [self.given.pk])
        self.assertEqual(exit_manage.assets_state(self.case), "todo")

    def test_no_assets_is_green(self):
        self.assertEqual(exit_manage.assets_state(self.case_for_other()), "done")

    def case_for_other(self):
        return self.accepted_case(self.other)

    def test_asking_for_the_return_marks_it_once(self):
        exit_manage.request_asset_return(self.case, self.given.pk, self.hr)
        self.given.refresh_from_db()
        self.assertTrue(self.given.return_request)
        self.assertEqual(exit_manage.assets_state(self.case), "doing")
        self.assertTrue(ExitActivity.objects.entire().filter(action="asset_ask").exists())
        with self.assertRaises(ExitError):
            exit_manage.request_asset_return(self.case, self.given.pk, self.hr)

    def test_a_healthy_return_puts_it_back_in_stock(self):
        exit_manage.recover_asset(
            self.case, self.given.pk, status="Healthy", returned_on=TODAY, condition="", by=self.hr
        )
        self.given.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertEqual(self.given.return_status, "Healthy")
        self.assertEqual(self.given.return_date, TODAY)
        self.assertEqual(self.asset.asset_status, "Available")
        self.assertEqual(exit_manage.assets_state(self.case), "done")
        self.assertTrue(ExitActivity.objects.entire().filter(action="asset_back").exists())

    def test_a_damaged_single_unit_is_taken_out_of_stock(self):
        exit_manage.recover_asset(
            self.case, self.given.pk, status="Major damage", returned_on=TODAY,
            condition="Screen cracked", by=self.hr,
        )
        self.asset.refresh_from_db()
        self.given.refresh_from_db()
        self.assertEqual(self.asset.asset_status, "Not-Available")
        self.assertEqual(self.given.return_condition, "Screen cracked")

    def test_one_damaged_unit_of_several_shrinks_the_pool(self):
        pool = _asset("Chairs", quantity=3)
        row = _give(pool, self.emp, self.hr)
        exit_manage.recover_asset(
            self.case, row.pk, status="Minor damage", returned_on=TODAY, condition="Scratched", by=self.hr
        )
        pool.refresh_from_db()
        self.assertEqual(pool.quantity, 2)
        self.assertEqual(pool.asset_status, "Available")

    def test_damage_needs_a_description(self):
        with self.assertRaises(ExitError):
            exit_manage.recover_asset(
                self.case, self.given.pk, status="Minor damage", returned_on=TODAY, condition="", by=self.hr
            )

    def test_bad_input_is_refused_and_nothing_changes(self):
        for kwargs in (
            dict(status="Lost", returned_on=TODAY),
            dict(status="Healthy", returned_on=None),
            dict(status="Healthy", returned_on=TODAY - DAY(days=5)),
            dict(status="Healthy", returned_on=TODAY, condition="<script>x()</script>"),
        ):
            with self.subTest(kwargs):
                kwargs.setdefault("condition", "")
                with self.assertRaises(ExitError):
                    exit_manage.recover_asset(self.case, self.given.pk, by=self.hr, **kwargs)
        self.given.refresh_from_db()
        self.assertIsNone(self.given.return_status)

    def test_someone_elses_asset_cannot_be_recovered(self):
        other_row = _give(_asset("Phone"), self.other, self.hr)
        with self.assertRaises(ExitError):
            exit_manage.recover_asset(
                self.case, other_row.pk, status="Healthy", returned_on=TODAY, condition="", by=self.hr
            )
        with self.assertRaises(ExitError):
            exit_manage.request_asset_return(self.case, other_row.pk, self.hr)

    def test_an_already_returned_asset_cannot_be_returned_again(self):
        exit_manage.recover_asset(
            self.case, self.given.pk, status="Healthy", returned_on=TODAY, condition="", by=self.hr
        )
        with self.assertRaises(ExitError):
            exit_manage.recover_asset(
                self.case, self.given.pk, status="Healthy", returned_on=TODAY, condition="", by=self.hr
            )


class TeamTests(ManageBase):
    def setUp(self):
        super().setUp()
        # self.mgr is the leaver here: self.emp reports to them
        self.mgr_case = self.accepted_case(self.mgr)
        self.new_user, self.new = _person()

    def test_lists_direct_reports(self):
        roles = {r["key"]: r["count"] for r in exit_manage.team_roles(self.mgr)}
        self.assertEqual(roles["reports"], 1)
        self.assertEqual(exit_manage.direct_reports(self.mgr), [self.emp])
        self.assertEqual(exit_manage.team_state(self.mgr_case), "todo")

    def test_nothing_to_hand_over_is_green(self):
        self.assertEqual(exit_manage.team_state(self.case), "done")
        with self.assertRaises(ExitError):
            exit_manage.hand_over_team(self.case, self.new, self.hr)

    def test_handing_over_moves_the_reports_and_is_logged(self):
        moved = exit_manage.hand_over_team(self.mgr_case, self.new, self.hr)
        self.assertTrue(moved)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.employee_work_info.reporting_manager_id, self.new)
        self.assertEqual(exit_manage.team_roles(self.mgr), [])
        self.assertEqual(exit_manage.team_state(self.mgr_case), "done")
        log = ExitActivity.objects.entire().get(action="team_moved")
        self.assertEqual(log.to_value, self.new.get_full_name())

    def test_who_can_take_over(self):
        owners = list(exit_manage.possible_new_owners(self.mgr_case))
        self.assertNotIn(self.mgr, owners)
        self.assertNotIn(self.emp, owners)  # emp is in the middle of an exit too
        self.assertIn(self.new, owners)
        for bad in (self.mgr, self.emp):
            with self.subTest(bad):
                with self.assertRaises(ExitError):
                    exit_manage.hand_over_team(self.mgr_case, bad, self.hr)
        self.new.is_active = False
        self.new.save()
        with self.assertRaises(ExitError):
            exit_manage.hand_over_team(self.mgr_case, self.new, self.hr)
        with self.assertRaises(ExitError):
            exit_manage.hand_over_team(self.mgr_case, None, self.hr)


class LeaveTests(ManageBase):
    def test_counts_only_approved_leave_in_the_window(self):
        _leave(self.emp, TODAY + DAY(days=3), TODAY + DAY(days=4), 2)
        _leave(self.emp, TODAY + DAY(days=6), TODAY + DAY(days=6), 1, status="requested")
        _leave(self.emp, TODAY + DAY(days=60), TODAY + DAY(days=61), 2)  # after the notice
        rows, total = exit_manage.leave_in_notice(self.case)
        self.assertEqual(len(rows), 1)
        self.assertEqual(total, 2)
        self.assertEqual(exit_manage.leave_state(self.case), "todo")

    def test_leave_sticking_out_of_the_window_counts_the_part_inside(self):
        # 4-day leave, only the last 2 days fall in the 20-day window
        _leave(self.emp, TODAY + DAY(days=18), TODAY + DAY(days=21), 4)
        _rows, total = exit_manage.leave_in_notice(self.case)
        self.assertEqual(total, 3)  # window ends day 20 -> days 18,19,20 inside

    def test_no_leave_is_green(self):
        self.assertEqual(exit_manage.leave_in_notice(self.case), ([], 0))
        self.assertEqual(exit_manage.leave_state(self.case), "done")
        with self.assertRaises(ExitError):
            exit_manage.extend_notice_for_leave(self.case, self.hr)

    def test_extending_moves_the_day_once_and_logs_it(self):
        _leave(self.emp, TODAY + DAY(days=3), TODAY + DAY(days=5), 3)
        old = self.case.last_working_day
        case, extra = exit_manage.extend_notice_for_leave(self.case, self.hr)
        self.assertEqual(extra, 3)
        self.assertEqual(case.last_working_day, old + DAY(days=3))
        self.assertEqual(case.leave_days_extended, 3)
        self.assertEqual(exit_manage.leave_state(case), "done")
        self.assertTrue(ExitActivity.objects.entire().filter(action="lwd_changed").exists())
        with self.assertRaises(ExitError):
            exit_manage.extend_notice_for_leave(case, self.hr)

    def test_more_leave_later_extends_only_the_difference(self):
        _leave(self.emp, TODAY + DAY(days=3), TODAY + DAY(days=4), 2)
        case, _extra = exit_manage.extend_notice_for_leave(self.case, self.hr)
        _leave(self.emp, TODAY + DAY(days=8), TODAY + DAY(days=8), 1)
        case, extra = exit_manage.extend_notice_for_leave(case, self.hr)
        self.assertEqual(extra, 1)
        self.assertEqual(case.leave_days_extended, 3)


class PanelPageTests(ManageBase):
    def url(self, section="summary", case=None):
        return reverse("exit-manage", args=[(case or self.case).pk, section])

    def get(self, user, section="summary", **params):
        self.client.force_login(user)
        return self.client.get(self.url(section), params, **HX)

    def test_every_section_opens_for_hr(self):
        for section in ("summary", "assets", "team", "leave", "finances"):
            with self.subTest(section):
                response = self.get(self.hr_user, section)
                self.assertEqual(response.status_code, 200)
        html = self.get(self.hr_user, "finances").content.decode()
        self.assertIn("Full & final settlement", html)

    def test_the_old_summary_link_still_works(self):
        self.client.force_login(self.hr_user)
        body = self.client.get(reverse("exit-summary", args=[self.case.pk]), **HX).content.decode()
        self.assertIn("Exit actions", body)
        self.assertIn("History", body)

    def test_unknown_section_falls_back_to_summary(self):
        self.assertIn("History", self.get(self.hr_user, "nonsense").content.decode())

    def test_the_menu_shows_a_dot_per_section(self):
        html = self.get(self.hr_user, "summary").content.decode()
        for label in ("Summary", "Assets", "Finances", "Tasks"):
            self.assertIn(label, html)
        self.assertIn("xm-dot--done", html)
        # the Team and Leave settings tabs are switched off (exit_manage.HIDDEN_SECTIONS)
        nav = html[html.index('role="tablist"'):html.index("</div>", html.index('role="tablist"'))]
        self.assertNotIn("Team", nav)
        self.assertNotIn("Leave settings", nav)

    def test_an_exited_exit_only_shows_the_summary(self):
        exit_service.mark_exited(self.case)
        self.case.refresh_from_db()
        html = self.get(self.hr_user, "assets").content.decode()
        self.assertIn("History", html)
        self.assertNotIn('role="tablist"', html)

    def test_a_manager_sees_their_reports_but_not_others(self):
        self.assertEqual(self.get(self.mgr_user, "assets").status_code, 200)
        other_case = self.accepted_case(self.other)
        self.client.force_login(self.mgr_user)
        denied = self.client.get(self.url("assets", other_case), **HX)
        self.assertNotIn(self.other.get_full_name(), denied.content.decode())

    def test_an_ordinary_employee_is_turned_away(self):
        response = self.get(self.emp_user, "assets")
        self.assertEqual(response.status_code, 302)

    def test_a_manager_cannot_change_anything(self):
        given = _give(_asset(), self.emp, self.hr)
        self.client.force_login(self.mgr_user)
        for url, data in (
            (reverse("exit-asset-ask", args=[self.case.pk, given.pk]), {}),
            (reverse("exit-asset-return", args=[self.case.pk, given.pk]), {"return_status": "Healthy", "return_date": TODAY}),
            (reverse("exit-team-handover", args=[self.case.pk]), {}),
            (reverse("exit-leave-extend", args=[self.case.pk]), {}),
        ):
            with self.subTest(url):
                self.client.post(url, data, **HX)
        given.refresh_from_db()
        self.assertFalse(given.return_request)
        self.assertIsNone(given.return_status)
        self.assertNotIn("Ask to return", self.get(self.mgr_user, "assets").content.decode())

    def test_hr_asks_and_records_a_return_from_the_panel(self):
        given = _give(_asset(), self.emp, self.hr)
        self.client.force_login(self.hr_user)
        ask = self.client.post(reverse("exit-asset-ask", args=[self.case.pk, given.pk]), **HX)
        self.assertEqual(ask.status_code, 200)
        self.assertEqual(ask["HX-Trigger"], "exitPanelChanged")
        form = self.client.get(self.url("assets"), {"return": given.pk}, **HX).content.decode()
        self.assertIn("return_date", form)
        done = self.client.post(
            reverse("exit-asset-return", args=[self.case.pk, given.pk]),
            {"return_status": "Healthy", "return_date": TODAY.isoformat(), "return_condition": ""},
            **HX,
        )
        self.assertEqual(done.status_code, 200)
        self.assertIn("marked as returned", done.content.decode())
        given.refresh_from_db()
        self.assertEqual(given.return_status, "Healthy")

    def test_the_return_form_needs_the_asset_permission(self):
        hr_no_asset = _person(perms=("view_offboardingemployee", "change_offboardingemployee"))[0]
        given = _give(_asset(), self.emp, self.hr)
        self.client.force_login(hr_no_asset)
        response = self.client.post(
            reverse("exit-asset-return", args=[self.case.pk, given.pk]),
            {"return_status": "Healthy", "return_date": TODAY.isoformat()},
            **HX,
        )
        given.refresh_from_db()
        self.assertIsNone(given.return_status)
        self.assertNotIn("marked as returned", response.content.decode())

    def test_a_bad_return_form_shows_the_error_and_keeps_the_form_open(self):
        given = _give(_asset(), self.emp, self.hr)
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("exit-asset-return", args=[self.case.pk, given.pk]),
            {"return_status": "Minor damage", "return_date": TODAY.isoformat(), "return_condition": ""},
            **HX,
        )
        html = response.content.decode()
        self.assertIn("Please describe the damage", html)
        self.assertIn("return_date", html)
        given.refresh_from_db()
        self.assertIsNone(given.return_status)

    def test_hr_hands_the_team_over(self):
        mgr_case = self.accepted_case(self.mgr)
        new_user, new = _person()
        self.client.force_login(self.hr_user)
        empty = self.client.post(reverse("exit-team-handover", args=[mgr_case.pk]), {}, **HX)
        self.assertIn("Please choose who takes over", empty.content.decode())
        response = self.client.post(
            reverse("exit-team-handover", args=[mgr_case.pk]), {"new_owner": new.pk}, **HX
        )
        self.assertIn("Handed over", response.content.decode())
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.employee_work_info.reporting_manager_id, new)

    def test_hr_extends_notice_from_the_panel(self):
        _leave(self.emp, TODAY + DAY(days=2), TODAY + DAY(days=3), 2)
        self.client.force_login(self.hr_user)
        page = self.client.get(self.url("leave"), **HX).content.decode()
        self.assertIn("Extend notice by 2 day(s)", page)
        response = self.client.post(reverse("exit-leave-extend", args=[self.case.pk]), **HX)
        self.assertIn("moved out by 2 day(s)", response.content.decode())
        self.case.refresh_from_db()
        self.assertEqual(self.case.leave_days_extended, 2)
        again = self.client.post(reverse("exit-leave-extend", args=[self.case.pk]), **HX)
        self.assertIn("no leave left to make up", again.content.decode())

    def test_changes_are_refused_once_the_employee_has_exited(self):
        given = _give(_asset(), self.emp, self.hr)
        exit_service.mark_exited(self.case)
        self.client.force_login(self.hr_user)
        self.client.post(reverse("exit-asset-ask", args=[self.case.pk, given.pk]), **HX)
        given.refresh_from_db()
        self.assertFalse(given.return_request)

    def test_progress_tab_shows_a_dot_per_visible_action(self):
        html = self.tab(self.hr_user, "progress").content.decode()
        for label in ("Assets", "Tasks"):
            self.assertIn(f'title="{label} - ', html)  # the icon's name, then its state
        for label in ("Team", "Leave settings"):  # switched off for now
            self.assertNotIn(f'title="{label} - ', html)
