"""
vertex/tests/test_expenses.py

Expenses (raise -> approve / reject -> paid), invoices, the settings screen and the daily / weekly
summaries, ported from ArielVertex with its gaps fixed. Mail and Teams are never contacted.
"""

import shutil
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from vertex import access, expenses
from vertex.models import Expense, ExpenseSetting, ExpenseStatus, VertexSetting
from vertex.tests.test_projects import HX, VertexTestBase, grant, make_employee

DENIED = "You Dont Have Permission"
PDF = b"%PDF-1.4 test"


class ExpenseBase(VertexTestBase):
    media = None

    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls.media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls.media, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        from horilla.horilla_middlewares import _thread_locals, set_selected_company

        _thread_locals.request = None
        set_selected_company(None)
        cls.desk = make_employee("Desk", "desk@e.example", "9900000001")
        cls.desk2 = make_employee("Desk2", "desk2@e.example", "9900000002")
        cls.acct = make_employee("Acct", "acct@e.example", "9900000003")
        cls.hr = make_employee("Hema", "hr@e.example", "9900000004")
        cls.boss = make_employee("Boss", "boss@e.example", "9900000005")
        cls.nobody = make_employee("Nobody", "nobody@e.example", "9900000006")
        for desk in (cls.desk, cls.desk2):
            grant(desk, "view_expense", "add_expense", "change_expense")
        grant(cls.acct, "view_expense", "view_allexpenses", "change_expenseapproval", "view_expensesetting")
        grant(cls.hr, "view_expense", "view_allexpenses", "view_expensesetting")
        grant(cls.boss, "view_expense", "view_allexpenses")

    def setUp(self):
        super().setUp()
        ExpenseSetting.objects.all().delete()  # the default: approval needed

    def data(self, **extra):
        data = {
            "title": "Printer paper", "category": "office_supplies", "amount": "1250.50", "vendor": "Paper House",
            "expense_date": timezone.localdate().isoformat(), "payment_method": "cash", "invoice_number": "INV-1",
            "description": "Ten reams", "approval_required": "on",
        }
        data.update(extra)
        return data

    def raise_it(self, who=None, **extra):
        self.login(who or self.desk).post(reverse("vertex-expense-new"), self.data(**extra), **HX)
        return Expense.objects.order_by("-pk").first()

    def make(self, **extra):
        values = dict(
            title="Made", amount=Decimal("100"), expense_date=timezone.localdate(), raised_by=self.desk,
            category="other", approval_required=True,
        )
        values.update(extra)
        return Expense.objects.create(**values)

    def step(self, who, name, expense, note=""):
        return self.login(who).post(reverse(f"vertex-expense-{name}", args=[expense.pk]), {"note": note}, **HX)

    def count(self, person):
        return Notification.objects.filter(recipient=person.employee_user_id).count()


class RaiseTests(ExpenseBase):
    def test_front_desk_raises_an_expense_and_approvers_are_told(self):
        expense = self.raise_it()
        self.assertEqual((expense.raised_by, expense.status, expense.amount), (self.desk, ExpenseStatus.PAYMENT_REQUESTED, Decimal("1250.50")))
        self.assertTrue(expense.approval_required)
        self.assertEqual(self.count(self.acct), 1)
        self.assertEqual(self.count(self.hr), 0)  # view only: not an approver

    def test_the_company_rule_cannot_be_skipped_by_leaving_the_box_empty(self):
        data = self.data()
        del data["approval_required"]
        self.login(self.desk).post(reverse("vertex-expense-new"), data, **HX)
        self.assertTrue(Expense.objects.get().approval_required)

    def test_when_the_rule_is_off_the_person_chooses_and_no_approver_is_told_for_no(self):
        ExpenseSetting.objects.create(pk=1, approval_required_by_default=False)
        data = self.data()
        del data["approval_required"]
        self.login(self.desk).post(reverse("vertex-expense-new"), data, **HX)
        expense = Expense.objects.get()
        self.assertFalse(expense.approval_required)
        self.assertEqual(self.count(self.acct), 0)
        self.raise_it(approval_required="on")
        self.assertTrue(Expense.objects.order_by("-pk").first().approval_required)
        self.assertEqual(self.count(self.acct), 1)

    def test_the_form_shows_the_company_rule(self):
        page = self.login(self.desk).get(reverse("vertex-expense-new"), **HX)
        self.assertContains(page, "Company rule: every expense needs approval.")

    def test_amount_date_title_and_text_are_checked(self):
        client = self.login(self.desk)
        future = (timezone.localdate() + timedelta(days=2)).isoformat()
        for bad in (dict(amount="0"), dict(amount="-5"), dict(amount="100000000"), dict(expense_date=future),
                    dict(title="   "), dict(title="t" * 121), dict(description="d" * 501), dict(vendor="v" * 121)):
            client.post(reverse("vertex-expense-new"), self.data(**bad), **HX)
        self.assertFalse(Expense.objects.exists())
        page = client.get(reverse("vertex-expense-new"), **HX)
        for limit in ('maxlength="120"', 'maxlength="500"'):
            self.assertContains(page, limit)

    def test_only_people_with_add_expense_can_raise(self):
        self.assertContains(self.login(self.acct).get(reverse("vertex-expense-new"), **HX), DENIED)
        self.assertContains(self.login(self.nobody).get(reverse("vertex-expense-new"), **HX), DENIED)
        self.raise_it(who=self.acct)
        self.assertFalse(Expense.objects.exists())

    def test_the_invoice_can_come_with_the_request_and_is_stored_under_a_random_name(self):
        upload = SimpleUploadedFile("Bill March.pdf", PDF)
        self.login(self.desk).post(reverse("vertex-expense-new"), {**self.data(), "invoice_file": upload}, **HX)
        expense = Expense.objects.get()
        self.assertEqual((expense.invoice_name, expense.invoice_size), ("Bill March.pdf", len(PDF)))
        self.assertRegex(expense.invoice.name, r"^vertex/expense/invoice/[0-9a-f]{32}\.pdf$")

    def test_invoice_type_size_and_emptiness_are_checked(self):
        client = self.login(self.desk)
        for upload in (SimpleUploadedFile("run.exe", b"MZ"), SimpleUploadedFile("page.html", b"<b>x"),
                       SimpleUploadedFile("empty.pdf", b""), SimpleUploadedFile("huge.pdf", b"x" * (10_000_001))):
            client.post(reverse("vertex-expense-new"), {**self.data(), "invoice_file": upload}, **HX)
        self.assertFalse(Expense.objects.exists())
        for good in ("a.png", "b.jpg", "c.JPEG", "d.pdf"):
            client.post(reverse("vertex-expense-new"), {**self.data(title=good), "invoice_file": SimpleUploadedFile(good, b"x")}, **HX)
        self.assertEqual(Expense.objects.count(), 4)


class WorkflowTests(ExpenseBase):
    def test_approve_then_the_raiser_marks_it_paid(self):
        expense = self.raise_it()
        self.step(self.acct, "approve", expense, "OK")
        expense.refresh_from_db()
        self.assertEqual((expense.status, expense.approver, expense.decision_note), (ExpenseStatus.APPROVED, self.acct, "OK"))
        self.assertIsNotNone(expense.decided_at)
        self.assertEqual(self.count(self.desk), 1)
        self.login(self.desk).post(expense.get_pay_url())
        expense.refresh_from_db()
        self.assertEqual((expense.status, expense.paid_by), (ExpenseStatus.PAID, self.desk))
        self.assertIsNotNone(expense.paid_at)

    def test_it_cannot_be_paid_before_approval_or_after_rejection(self):
        expense = self.make()
        self.login(self.desk).post(expense.get_pay_url())
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAYMENT_REQUESTED)
        self.step(self.acct, "reject", expense, "Not allowed")
        self.login(self.desk).post(expense.get_pay_url())
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.REJECTED)

    def test_a_rejection_needs_a_reason_and_the_raiser_is_told_why(self):
        expense = self.make()
        self.step(self.acct, "reject", expense, "   ")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAYMENT_REQUESTED)
        self.step(self.acct, "reject", expense, "Over budget")
        expense.refresh_from_db()
        self.assertEqual((expense.status, expense.decision_note), (ExpenseStatus.REJECTED, "Over budget"))
        self.assertTrue(Notification.objects.filter(recipient=self.desk.employee_user_id, verb__contains="Over budget").exists())

    def test_only_approvers_decide_and_never_their_own_expense(self):
        expense = self.make()
        self.assertContains(self.login(self.desk).get(reverse("vertex-expense-approve", args=[expense.pk]), **HX), DENIED)
        self.assertContains(self.login(self.hr).get(reverse("vertex-expense-approve", args=[expense.pk]), **HX), DENIED)
        grant(self.acct, "add_expense")
        mine = self.make(raised_by=self.acct)
        self.assertContains(self.login(self.acct).get(reverse("vertex-expense-approve", args=[mine.pk]), **HX), DENIED)
        with self.assertRaises(expenses.ExpenseError):
            expenses.approve(mine.pk, self.acct)

    def test_a_step_cannot_happen_twice(self):
        expense = self.make()
        expenses.approve(expense.pk, self.acct)
        with self.assertRaises(expenses.ExpenseError):
            expenses.approve(expense.pk, self.acct)
        with self.assertRaises(expenses.ExpenseError):
            expenses.reject(expense.pk, self.acct, "late")
        expenses.pay(expense.pk, self.desk)
        with self.assertRaises(expenses.ExpenseError):
            expenses.pay(expense.pk, self.desk)

    def test_an_expense_that_needs_no_approval_is_paid_straight_away(self):
        expense = self.make(approval_required=False)
        self.assertContains(self.login(self.acct).get(reverse("vertex-expense-approve", args=[expense.pk]), **HX), DENIED)
        self.login(self.desk).post(expense.get_pay_url())
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAID)

    def test_only_the_raiser_or_a_viewer_with_change_pays(self):
        expense = self.make(approval_required=False)
        self.login(self.desk2).post(expense.get_pay_url())  # another desk person: not theirs
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAYMENT_REQUESTED)
        self.assertEqual(self.login(self.desk2).get(expense.get_absolute_url()).status_code, 404)
        self.login(self.acct).post(expense.get_pay_url())
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAYMENT_REQUESTED)  # accountants approve; they do not pay

    def test_pay_is_post_only(self):
        expense = self.make(approval_required=False)
        self.assertEqual(self.login(self.desk).get(expense.get_pay_url()).status_code, 405)

    def test_row_actions_follow_the_status_and_the_person(self):
        expense = self.make()
        listing = lambda who: self.login(who).get(reverse("vertex-expense-list"), **HX).content.decode()
        self.assertIn("/approve/", listing(self.acct))
        self.assertNotIn("/approve/", listing(self.desk))
        self.assertNotIn("/pay/", listing(self.desk))
        expenses.approve(expense.pk, self.acct)
        self.assertIn("/pay/", listing(self.desk))
        self.assertNotIn("/approve/", listing(self.acct))


class InvoiceTests(ExpenseBase):
    def test_upload_replace_and_the_old_file_is_removed(self):
        expense = self.make()
        client = self.login(self.desk)
        client.post(expense.get_invoice_url(), {"invoice_file": SimpleUploadedFile("one.pdf", PDF)}, **HX)
        expense.refresh_from_db()
        first = expense.invoice.name
        self.assertTrue(expense.invoice.storage.exists(first))
        client.post(expense.get_invoice_url(), {"invoice_file": SimpleUploadedFile("two.png", b"png")}, **HX)
        expense.refresh_from_db()
        self.assertEqual(expense.invoice_name, "two.png")
        self.assertFalse(expense.invoice.storage.exists(first))

    def test_wrong_files_and_wrong_people_are_refused(self):
        expense = self.make()
        self.login(self.desk).post(expense.get_invoice_url(), {"invoice_file": SimpleUploadedFile("x.exe", b"MZ")}, **HX)
        self.assertFalse(Expense.objects.get().invoice)
        self.assertContains(self.login(self.desk2).get(expense.get_invoice_url(), **HX), DENIED)
        self.assertContains(self.login(self.hr).get(expense.get_invoice_url(), **HX), DENIED)
        done = self.make(status=ExpenseStatus.PAID)
        self.assertContains(self.login(self.desk).get(done.get_invoice_url(), **HX), DENIED)

    def test_download_is_for_the_raiser_and_the_people_who_see_everything(self):
        expense = self.make()
        expenses.attach_invoice(expense, SimpleUploadedFile("bill.pdf", PDF))
        response = self.login(self.desk).get(expense.get_download_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("bill.pdf", response["Content-Disposition"])
        for who in (self.acct, self.hr, self.boss):
            self.assertEqual(self.login(who).get(expense.get_download_url()).status_code, 200, who)
        self.assertEqual(self.login(self.desk2).get(expense.get_download_url()).status_code, 404)
        self.assertNotEqual(self.login(self.nobody).get(expense.get_download_url()).status_code, 200)  # no permission at all

    def test_the_stored_file_is_not_open_to_anyone_through_the_media_folder(self):
        from horilla.media_access import may_open

        expense = self.make()
        expenses.attach_invoice(expense, SimpleUploadedFile("bill.pdf", PDF))
        path = expense.invoice.name
        self.assertTrue(may_open(self.desk.employee_user_id, path))
        self.assertTrue(may_open(self.acct.employee_user_id, path))
        self.assertFalse(may_open(self.desk2.employee_user_id, path))
        self.assertFalse(may_open(self.nobody.employee_user_id, path))

    def test_no_invoice_means_no_download(self):
        expense = self.make()
        self.assertEqual(self.login(self.desk).get(expense.get_download_url()).status_code, 404)


class VisibilityTests(ExpenseBase):
    def test_who_sees_which_expenses(self):
        mine = self.make()
        theirs = self.make(raised_by=self.desk2)
        ids = lambda who: set(access.visible_expenses(who.employee_user_id).values_list("pk", flat=True))
        self.assertEqual(ids(self.desk), {mine.pk})
        self.assertEqual(ids(self.desk2), {theirs.pk})
        for who in (self.acct, self.hr, self.boss):
            self.assertEqual(ids(who), {mine.pk, theirs.pk})
        self.assertEqual(ids(self.nobody), set())

    def test_the_summary_strip_counts_what_the_person_sees(self):
        self.make(amount=Decimal("100"))
        self.make(amount=Decimal("50"), status=ExpenseStatus.PAID)
        self.make(amount=Decimal("25"), raised_by=self.desk2)
        mine = access.expense_summary(self.desk.employee_user_id)
        self.assertEqual(
            (mine["expense_count"], mine["pending_count"], mine["paid_count"], mine["total_amount"], mine["paid_amount"]),
            (2, 1, 1, Decimal("150"), Decimal("50")),
        )
        everyone = access.expense_summary(self.acct.employee_user_id)
        self.assertEqual((everyone["expense_count"], everyone["pending_count"]), (3, 2))
        page = self.login(self.desk).get(reverse("vertex-expenses"))
        self.assertContains(page, "Waiting for approval")


class SettingsTests(ExpenseBase):
    def test_who_sees_and_who_changes_the_settings(self):
        self.assertEqual(self.login(self.hr).get(reverse("vertex-expense-settings")).status_code, 200)
        self.assertEqual(self.login(self.acct).get(reverse("vertex-expense-settings")).status_code, 200)
        self.assertNotEqual(self.login(self.desk).get(reverse("vertex-expense-settings")).status_code, 200)
        pk = ExpenseSetting.current().pk
        url = reverse("vertex-expense-settings-edit", args=[pk])
        self.assertContains(self.login(self.hr).get(url, **HX), DENIED)
        page = self.login(self.hr).get(reverse("vertex-expense-settings"))
        self.assertNotContains(page, "Edit settings")
        grant(self.hr, "change_expensesetting")
        self.assertNotContains(self.login(self.hr).get(url, **HX), DENIED)
        self.assertContains(self.login(self.hr).get(reverse("vertex-expense-settings")), "Edit settings")

    def test_saving_settings_keeps_a_single_row(self):
        grant(self.hr, "change_expensesetting")
        pk = ExpenseSetting.current().pk
        self.login(self.hr).post(
            reverse("vertex-expense-settings-edit", args=[pk]),
            {"daily_recipients": "A@x.com; b@x.com, a@x.com", "weekly_recipients": "c@x.com", "teams_webhook_url": "https://example.webhook.office.com/abc"},
            **HX,
        )
        setting = ExpenseSetting.objects.get()
        self.assertEqual(setting.daily_recipients, "A@x.com, b@x.com")
        self.assertFalse(setting.approval_required_by_default)  # the box was left empty
        self.assertEqual(ExpenseSetting.objects.count(), 1)

    def test_bad_recipients_and_webhooks_are_refused(self):
        grant(self.hr, "change_expensesetting")
        pk = ExpenseSetting.current().pk
        url = reverse("vertex-expense-settings-edit", args=[pk])
        for bad in (dict(daily_recipients="not-an-email"), dict(teams_webhook_url="http://example.com/hook"),
                    dict(teams_webhook_url="https://127.0.0.1/hook"), dict(teams_webhook_url="https://localhost/hook"),
                    dict(weekly_recipients=",".join(f"p{i}@x.com" for i in range(21)))):
            self.login(self.hr).post(url, {"approval_required_by_default": "on", **bad}, **HX)
        setting = ExpenseSetting.objects.get()
        self.assertEqual((setting.daily_recipients, setting.teams_webhook_url), ("", ""))

    def test_the_edit_url_only_works_for_the_one_settings_row(self):
        grant(self.hr, "change_expensesetting")
        ExpenseSetting.current()
        self.assertContains(self.login(self.hr).get(reverse("vertex-expense-settings-edit", args=[999]), **HX), DENIED)


class SummaryTests(ExpenseBase):
    def setup_recipients(self, **extra):
        values = dict(pk=1, daily_recipients="d@e.example", weekly_recipients="w@e.example, w2@e.example")
        values.update(extra)
        ExpenseSetting.objects.create(**values)

    def test_the_daily_summary_covers_today_and_the_weekly_the_last_seven_days(self):
        old = self.make(title="Old", amount=Decimal("10"))
        Expense.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        ancient = self.make(title="Ancient", amount=Decimal("5"))
        Expense.objects.filter(pk=ancient.pk).update(created_at=timezone.now() - timedelta(days=20))
        self.make(title="Today", amount=Decimal("100"))
        self.make(title="Paid today", amount=Decimal("50"), status=ExpenseStatus.PAID)
        title, message, body = expenses.build_summary("daily")
        self.assertIn("Daily", title)
        self.assertIn("2 expense(s)", message)
        self.assertIn("Today", body)
        self.assertNotIn("Old", body)
        self.assertIn("1 waiting for approval", message)
        _t, weekly_message, weekly_body = expenses.build_summary("weekly")
        self.assertIn("3 expense(s)", weekly_message)
        self.assertIn("Old", weekly_body)
        self.assertNotIn("Ancient", weekly_body)

    def test_sending_goes_to_the_recipients_and_teams_and_skips_them_when_unset(self):
        self.setup_recipients(teams_webhook_url="https://x.webhook.office.com/a")
        self.make()
        with patch("vertex.expenses.send_plain_email", return_value=1) as mail, patch("vertex.expenses._post_teams") as teams:
            result = expenses.send_summary("weekly")
        self.assertEqual(result, {"recipients": 2, "emailed": 1, "teams": True})
        self.assertEqual(mail.call_args[0][0], ["w@e.example", "w2@e.example"])
        teams.assert_called_once()
        ExpenseSetting.objects.all().update(weekly_recipients="", teams_webhook_url="")
        with patch("vertex.expenses.send_plain_email") as mail, patch("vertex.expenses._post_teams") as teams:
            self.assertEqual(expenses.send_summary("weekly"), {"recipients": 0, "emailed": 0, "teams": False})
        mail.assert_not_called()
        teams.assert_not_called()

    def test_a_teams_failure_never_stops_the_summary(self):
        self.setup_recipients(teams_webhook_url="https://x.webhook.office.com/a")
        with patch("vertex.expenses.send_plain_email", return_value=1), patch("vertex.expenses._post_teams", side_effect=RuntimeError("down")):
            self.assertFalse(expenses.send_summary("daily")["teams"])

    def test_the_scheduler_sends_each_summary_once_after_six_in_the_evening(self):
        friday = timezone.make_aware(datetime(2026, 11, 20, 19, 0))
        with patch("vertex.expenses.send_summary") as send:
            self.assertEqual(expenses.run_due_summaries(friday.replace(hour=17)), [])
            self.assertEqual(expenses.run_due_summaries(friday), ["daily", "weekly"])
            self.assertEqual(expenses.run_due_summaries(friday + timedelta(minutes=15)), [])  # already sent today
            self.assertEqual(send.call_count, 2)
            saturday = friday + timedelta(days=1)
            self.assertEqual(expenses.run_due_summaries(saturday), ["daily"])  # no weekly on a Saturday
        self.assertEqual(VertexSetting.objects.get(key="expenses.last_daily").value, "2026-11-21")

    def test_the_send_now_buttons_report_what_happened(self):
        grant(self.hr, "change_expensesetting")
        self.setup_recipients()
        with patch("vertex.expenses.send_plain_email", return_value=1):
            response = self.login(self.hr).post(reverse("vertex-expense-summary-now", args=["daily"]), follow=True)
        self.assertContains(response, "summary was sent")
        ExpenseSetting.objects.all().update(daily_recipients="")
        response = self.login(self.hr).post(reverse("vertex-expense-summary-now", args=["daily"]), follow=True)
        self.assertContains(response, "Nobody is set to receive")
        self.assertEqual(self.login(self.hr).post(reverse("vertex-expense-summary-now", args=["monthly"])).status_code, 404)
        self.setup_none = ExpenseSetting.objects.all().update(daily_recipients="d@e.example")
        with patch("vertex.expenses.send_plain_email") as mail:
            self.login(self.acct).post(reverse("vertex-expense-summary-now", args=["daily"]))  # view only: not allowed
        mail.assert_not_called()

    def test_the_command_runs(self):
        from django.core.management import call_command

        self.setup_recipients()
        with patch("vertex.expenses.send_plain_email", return_value=1):
            call_command("vertex_expense_summary", "daily")


class PagesTests(ExpenseBase):
    def test_admin_opens_every_page(self):
        admin = make_employee("Admin", "admin@e.example", "9900000009")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        expense = self.make()
        client = self.login(admin)
        for url in (reverse("vertex-expenses"), reverse("vertex-expense-settings"), expense.get_absolute_url()):
            self.assertEqual(client.get(url).status_code, 200, url)
        for url in (
            reverse("vertex-expense-nav"), reverse("vertex-expense-list"), reverse("vertex-expense-new"),
            expense.get_invoice_url(), reverse("vertex-expense-settings-edit", args=[ExpenseSetting.current().pk]),
        ):
            page = client.get(url, **HX)
            self.assertEqual(page.status_code, 200, url)
            self.assertNotContains(page, DENIED, msg_prefix=url)

    def test_detail_page_shows_the_buttons_each_person_may_use(self):
        expense = self.make()
        self.assertContains(self.login(self.acct).get(expense.get_absolute_url()), "Approve")
        page = self.login(self.desk).get(expense.get_absolute_url())
        self.assertNotContains(page, ">Approve<")
        self.assertContains(page, "Upload invoice")
        self.assertEqual(self.login(self.desk2).get(expense.get_absolute_url()).status_code, 404)
        self.assertNotEqual(self.login(self.nobody).get(expense.get_absolute_url()).status_code, 200)

    def test_no_permission_no_pages(self):
        client = self.login(self.nobody)
        for name in ("vertex-expenses", "vertex-expense-settings"):
            self.assertNotEqual(client.get(reverse(name)).status_code, 200, name)
