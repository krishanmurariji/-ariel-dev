"""
vertex/tests/test_minutes.py

Minutes: generator (ported from ArielVertex MinutesGenerator) and the
capture -> generate -> preview -> approve -> send workflow, owner-only.
"""

from datetime import datetime
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from vertex.minutes import generate_minutes, polish, split_emails
from vertex.models import MeetingMinute, MinuteStatus
from vertex.tests.test_projects import HX, VertexTestBase, grant, make_employee


class GeneratorTests(SimpleTestCase):
    def test_polish_fixes_spacing_case_and_full_stop(self):
        self.assertEqual(polish("  we  agreed ,that i will go "), "We agreed, that I will go.")
        self.assertEqual(polish("already ends."), "Already ends.")
        self.assertEqual(polish("Title", force_stop=False), "Title")
        self.assertEqual(polish("   "), "")

    def test_split_emails_normalises_and_removes_duplicates(self):
        self.assertEqual(
            split_emails(" A@x.com; b@x.com,\n a@X.com ,,"), ["a@x.com", "b@x.com"]
        )
        self.assertEqual(split_emails(None), [])

    def test_notes_are_sorted_into_the_three_sections(self):
        text = generate_minutes(
            "weekly sync", datetime(2026, 11, 5, 10, 30), "Room 1", ["a@x.com"],
            "- we discussed the roadmap\n* team agreed to ship friday\nbob will fix the bug by monday\n\n",
        )
        self.assertIn("# Minutes of Meeting — Weekly sync", text)
        self.assertIn("**Date:** Thursday, 05 Nov 2026, 10:30", text)
        self.assertIn("**Location:** Room 1", text)
        self.assertIn("**Attendees:** a@x.com", text)
        discussion, rest = text.split("## Decisions")
        decisions, actions = rest.split("## Action Items")
        self.assertIn("- We discussed the roadmap.", discussion)
        self.assertIn("- Team agreed to ship friday.", decisions)
        self.assertIn("- Bob will fix the bug by monday.", actions)

    def test_empty_sections_say_so(self):
        text = generate_minutes("x", datetime(2026, 1, 1), "", [], "just talking")
        self.assertIn("No decisions were recorded.", text)
        self.assertIn("No action items were recorded.", text)
        self.assertNotIn("**Location:**", text)


class MinutesWorkflowTests(VertexTestBase):
    NOTES = "we agreed to ship\nann will test it by friday\nlots of talk"

    def setUp(self):
        super().setUp()
        self.owner = make_employee("Owner", "owner@example.com", "9200000001")
        self.ann = make_employee("Ann", "ann@example.com", "9200000002")
        self.other = make_employee("Other", "other@example.com", "9200000003")
        grant(
            self.owner, "view_meetingminute", "add_meetingminute", "change_meetingminute",
            "delete_meetingminute",
        )
        self.client_owner = self.login(self.owner)

    def create(self, **extra):
        data = {
            "title": "Weekly sync", "meeting_date": "2026-11-05T10:30", "location": "Room 1",
            "attendees": "Ann@Example.com; outsider@example.org\nann@example.com",
            "raw_notes": self.NOTES,
        }
        data.update(extra)
        self.client_owner.post(reverse("vertex-minute-new"), data, **HX)
        return MeetingMinute.objects.order_by("-pk").first()

    def act(self, name, minute):
        return self.client_owner.post(reverse(name, args=[minute.pk]))

    def test_no_group_means_no_access(self):
        client = self.login(self.other)
        self.assertNotEqual(client.get(reverse("vertex-minutes")).status_code, 200)
        client.post(
            reverse("vertex-minute-new"),
            {"title": "x", "meeting_date": "2026-11-05T10:30", "raw_notes": "n"},
            **HX,
        )
        self.assertFalse(MeetingMinute.objects.exists())

    def test_creating_normalises_attendees_and_starts_as_draft(self):
        minute = self.create()
        self.assertEqual(minute.created_by, self.owner)
        self.assertEqual(minute.status, MinuteStatus.DRAFT)
        self.assertEqual(minute.attendees, "ann@example.com, outsider@example.org")
        self.assertEqual(minute.minutes_text, "")

    def test_notes_are_required(self):
        self.create(raw_notes="")
        self.assertFalse(MeetingMinute.objects.exists())

    def test_generate_moves_to_preview(self):
        minute = self.create()
        self.act("vertex-minute-generate", minute)
        minute.refresh_from_db()
        self.assertEqual(minute.status, MinuteStatus.PREVIEW)
        self.assertIn("## Action Items", minute.minutes_text)
        page = self.client_owner.get(reverse("vertex-minute", args=[minute.pk]))
        self.assertContains(page, "Minutes of Meeting")

    def test_cannot_approve_or_send_before_generating(self):
        minute = self.create()
        self.act("vertex-minute-approve", minute)
        self.act("vertex-minute-send", minute)
        minute.refresh_from_db()
        self.assertEqual(minute.status, MinuteStatus.DRAFT)

    def test_cannot_send_before_approval(self):
        minute = self.create()
        self.act("vertex-minute-generate", minute)
        with mock.patch("vertex.views_minutes.send_plain_email") as mail:
            self.act("vertex-minute-send", minute)
        mail.assert_not_called()
        minute.refresh_from_db()
        self.assertEqual(minute.status, MinuteStatus.PREVIEW)

    def test_full_flow_notifies_internal_and_mails_everyone(self):
        minute = self.create()
        self.act("vertex-minute-generate", minute)
        self.act("vertex-minute-approve", minute)
        minute.refresh_from_db()
        self.assertEqual((minute.status, minute.approved_by), (MinuteStatus.APPROVED, self.owner))
        with mock.patch("vertex.views_minutes.send_plain_email", return_value=2) as mail:
            self.act("vertex-minute-send", minute)
        emails, subject, body = mail.call_args.args
        self.assertEqual(emails, ["ann@example.com", "outsider@example.org"])
        self.assertIn("Weekly sync", subject)
        self.assertIn("## Action Items", body)
        minute.refresh_from_db()
        self.assertEqual((minute.status, minute.sent_count), (MinuteStatus.SENT, 1))
        self.assertIsNotNone(minute.sent_at)
        # only the colleague with an account gets a bell notification
        self.assertEqual(Notification.objects.filter(recipient=self.ann.employee_user_id).count(), 1)

    def test_resend_counts_again(self):
        minute = self.create()
        for name in ("generate", "approve", "send", "send"):
            with mock.patch("vertex.views_minutes.send_plain_email", return_value=0):
                self.act(f"vertex-minute-{name}", minute)
        minute.refresh_from_db()
        self.assertEqual(minute.sent_count, 2)

    def test_send_needs_an_attendee(self):
        minute = self.create(attendees="")
        for name in ("generate", "approve"):
            self.act(f"vertex-minute-{name}", minute)
        with mock.patch("vertex.views_minutes.send_plain_email") as mail:
            self.act("vertex-minute-send", minute)
        mail.assert_not_called()
        minute.refresh_from_db()
        self.assertEqual(minute.status, MinuteStatus.APPROVED)

    def test_editing_sent_minutes_returns_them_to_preview(self):
        minute = self.create()
        for name in ("generate", "approve"):
            self.act(f"vertex-minute-{name}", minute)
        with mock.patch("vertex.views_minutes.send_plain_email", return_value=0):
            self.act("vertex-minute-send", minute)
        minute.refresh_from_db()
        self.client_owner.post(
            reverse("vertex-minute-edit", args=[minute.pk]),
            {
                "title": minute.title, "meeting_date": "2026-11-05T10:30", "attendees": minute.attendees,
                "raw_notes": minute.raw_notes, "minutes_text": minute.minutes_text + "\n- Corrected.",
            },
            **HX,
        )
        minute.refresh_from_db()
        self.assertEqual(minute.status, MinuteStatus.PREVIEW)
        self.assertIsNone(minute.approved_by)
        self.assertIsNone(minute.approved_at)
        self.assertTrue(minute.minutes_text.endswith("- Corrected."))

    def test_generate_is_blocked_once_sent(self):
        minute = self.create()
        for name in ("generate", "approve"):
            self.act(f"vertex-minute-{name}", minute)
        with mock.patch("vertex.views_minutes.send_plain_email", return_value=0):
            self.act("vertex-minute-send", minute)
        before = MeetingMinute.objects.get(pk=minute.pk).minutes_text
        MeetingMinute.objects.filter(pk=minute.pk).update(minutes_text=before + "\nEdited")
        self.act("vertex-minute-generate", minute)
        self.assertTrue(MeetingMinute.objects.get(pk=minute.pk).minutes_text.endswith("Edited"))

    def test_only_the_owner_can_touch_minutes_even_with_permissions(self):
        minute = self.create()
        grant(
            self.other, "view_meetingminute", "change_meetingminute", "delete_meetingminute",
        )
        client = self.login(self.other)
        self.assertEqual(client.get(reverse("vertex-minute", args=[minute.pk])).status_code, 404)
        for name in ("generate", "approve", "send", "delete"):
            self.assertEqual(client.post(reverse(f"vertex-minute-{name}", args=[minute.pk])).status_code, 404, name)
        self.assertNotContains(client.get(reverse("vertex-minutes-list"), **HX), "Weekly sync")
        self.assertContains(self.client_owner.get(reverse("vertex-minutes-list"), **HX), "Weekly sync")
        # nor can they open or save the owner's minutes through the edit modal
        edit = reverse("vertex-minute-edit", args=[minute.pk])
        self.assertNotContains(client.get(edit, **HX), "raw_notes")
        client.post(edit, {"title": "Hijacked", "meeting_date": "2026-11-05T10:30", "raw_notes": "x"}, **HX)
        self.assertEqual(MeetingMinute.objects.get(pk=minute.pk).title, "Weekly sync")
        self.assertTrue(MeetingMinute.objects.filter(pk=minute.pk).exists())

    def test_admin_is_not_the_owner_either(self):
        minute = self.create()
        user = self.other.employee_user_id
        user.is_superuser = True
        user.save()
        self.assertEqual(
            self.login(self.other).get(reverse("vertex-minute", args=[minute.pk])).status_code, 404
        )

    def test_delete_needs_the_delete_permission(self):
        minute = self.create()
        from django.contrib.auth.models import Permission

        for group in self.owner.employee_user_id.groups.all():
            group.permissions.remove(Permission.objects.get(codename="delete_meetingminute"))
        self.act("vertex-minute-delete", minute)
        self.assertTrue(MeetingMinute.objects.filter(pk=minute.pk).exists())

    def test_delete_removes_them(self):
        minute = self.create()
        self.act("vertex-minute-delete", minute)
        self.assertFalse(MeetingMinute.objects.filter(pk=minute.pk).exists())

    def test_writes_by_get_are_refused(self):
        minute = self.create()
        for name in ("generate", "approve", "send", "delete"):
            self.assertEqual(
                self.client_owner.get(reverse(f"vertex-minute-{name}", args=[minute.pk])).status_code, 405, name
            )
