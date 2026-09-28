"""
Help-desk ticket text, ticket comments and OKR comments are typed by one user
and shown to others. These tests pin down that script in them can no longer run.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from django import forms
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template

from employee.models import Employee
from helpdesk.forms import CommentForm, TicketForm
from helpdesk.models import Comment, Ticket, TicketType
from horilla.horilla_middlewares import _thread_locals
from horilla.isolated_testcase import IsolatedTestCase

DANGEROUS = re.compile(
    r"<\s*script|onerror|onload|onclick|javascript:|<\s*iframe|<\s*svg", re.I
)


def _make_ticket(description):
    # TicketType.save() reads the current request's session; there is no real
    # request here. IsolatedTestCase clears this again after each test.
    _thread_locals.request = SimpleNamespace(session={}, user=AnonymousUser())
    employee = Employee.objects.create(
        employee_first_name="Help",
        employee_last_name="Desk",
        email="helpdesk-test@example.com",
    )
    ticket_type = TicketType.objects.create(title="IT", type="suggestion", prefix="IT")
    ticket = Ticket.objects.create(
        title="Printer",
        employee_id=employee,
        ticket_type=ticket_type,
        description=description,
        assigning_type="individual",
        raised_on=str(employee.pk),
    )
    return employee, ticket


class TicketFormTests(IsolatedTestCase):
    def _clean_description(self, value):
        # TicketForm() itself needs a logged-in request; the cleaning method
        # only reads cleaned_data.
        form = SimpleNamespace(cleaned_data={"description": value})
        return TicketForm.clean_description(form)

    def test_script_is_removed_but_the_text_stays(self):
        out = self._clean_description(
            "<p>Printer is broken</p><script>alert(1)</script><img src=x onerror=alert(2)>"
        )
        self.assertIn("Printer is broken", out)
        self.assertNotRegex(out, DANGEROUS)

    def test_a_description_that_is_only_script_counts_as_empty(self):
        with self.assertRaises(forms.ValidationError):
            self._clean_description("<script>alert(1)</script>")

    def test_formatting_survives(self):
        self.assertIn("<b>urgent</b>", self._clean_description("<b>urgent</b> please"))


class CommentFormTests(IsolatedTestCase):
    def test_script_is_removed_from_a_comment(self):
        form = CommentForm()
        form.cleaned_data = {"comment": "<p>ok</p><script>alert(1)</script>"}
        out = form.clean_comment()
        self.assertIn("ok", out)
        self.assertNotRegex(out, DANGEROUS)


class TicketModelTests(IsolatedTestCase):
    def test_saving_a_ticket_cleans_the_description(self):
        # Covers paths that never use TicketForm (API, imports, shell).
        _, ticket = _make_ticket(
            "<p>Hello</p><script>alert(1)</script><a href='javascript:x()'>go</a>"
        )
        ticket.refresh_from_db()
        self.assertIn("Hello", ticket.description)
        self.assertNotRegex(ticket.description, DANGEROUS)

    def test_saving_a_comment_cleans_it(self):
        employee, ticket = _make_ticket("<p>fine</p>")
        comment = Comment.objects.create(
            comment="<p>hi</p><img src=x onerror=alert(1)>",
            ticket=ticket,
            employee_id=employee,
        )
        comment.refresh_from_db()
        self.assertIn("hi", comment.comment)
        self.assertNotRegex(comment.comment, DANGEROUS)

    def test_a_comment_with_no_text_still_saves(self):
        employee, ticket = _make_ticket("<p>fine</p>")
        comment = Comment.objects.create(
            comment=None, ticket=ticket, employee_id=employee
        )
        comment.refresh_from_db()
        self.assertIsNone(comment.comment)


class DisplayTests(IsolatedTestCase):
    def _render(self, value):
        return Template("{% load basefilters %}{{ x|sanitize_html }}").render(
            Context({"x": value})
        )

    def test_text_stored_before_the_fix_is_cleaned_when_shown(self):
        employee, ticket = _make_ticket("<p>ok</p>")
        Ticket.objects.filter(pk=ticket.pk).update(
            description="<p>ok</p><script>alert(1)</script><img src=x onerror=alert(1)>"
        )
        Comment.objects.create(comment="fine", ticket=ticket, employee_id=employee)
        Comment.objects.filter(ticket=ticket).update(
            comment="<p>hey</p><iframe src='javascript:alert(1)'></iframe>"
        )
        ticket.refresh_from_db()
        self.assertIn("<script", ticket.description)  # really stored raw
        out = self._render(ticket.description)
        self.assertIn("<p>ok</p>", out)
        self.assertNotRegex(out, DANGEROUS)
        out = self._render(Comment.objects.get(ticket=ticket).comment)
        self.assertIn("hey", out)
        self.assertNotRegex(out, DANGEROUS)

    def test_change_history_values_are_cleaned_too(self):
        out = self._render("<p>old</p><script>alert(1)</script>")
        self.assertNotRegex(out, DANGEROUS)

    def test_a_faq_question_cannot_break_out_of_the_script_block(self):
        questions = ["How do I reset?", "</script><script>alert(1)</script>"]
        html = Template('{{ questions|json_script:"faq-questions-data" }}').render(
            Context({"questions": questions})
        )
        inner = re.search(r"<script[^>]*>(.*)</script>", html, re.S).group(1)
        self.assertNotIn("</script", inner.lower())
        self.assertNotIn("<script", inner.lower())
        self.assertEqual(json.loads(inner), questions)  # data arrives intact


class RealTicketPageTests(IsolatedTestCase):
    """Open the real ticket page, as a logged-in admin, with hostile text stored."""

    def test_ticket_page_renders_and_hostile_text_is_inert(self):
        from django.test import Client
        from django.urls import reverse

        from horilla_auth.models import HorillaUser

        employee, ticket = _make_ticket("<p>ok</p>")
        user = HorillaUser.objects.create(
            username="helpdesk_admin", is_active=True, is_staff=True, is_superuser=True
        )
        Employee.objects.filter(pk=employee.pk).update(employee_user_id=user)
        Comment.objects.create(comment="fine", ticket=ticket, employee_id=employee)
        # written around save(), like rows that already existed before the fix
        Ticket.objects.filter(pk=ticket.pk).update(
            description="<p>visible-description</p><script>window.PWNED_DESC=1</script>"
            "<img src=x onerror=window.PWNED_IMG=1>"
        )
        Comment.objects.filter(ticket=ticket).update(
            comment="<p>visible-comment</p><script>window.PWNED_COMMENT=1</script>"
        )

        client = Client()
        client.force_login(user)
        response = client.get(reverse("ticket-detail", kwargs={"ticket_id": ticket.pk}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("visible-description", html)
        self.assertIn("visible-comment", html)
        # No stored script marker may appear at all, not even as escaped text
        # inside the comment edit box (the editor loads that box as HTML).
        for marker in ("PWNED_DESC", "PWNED_IMG", "PWNED_COMMENT", "onerror"):
            self.assertNotIn(marker, html)


class TemplatesNoLongerUseTheUnsafeFilterTests(IsolatedTestCase):
    def _source(self, relative):
        return (Path(settings.BASE_DIR) / relative).read_text(encoding="utf-8")

    def test_ticket_and_okr_pages_do_not_use_safe_for_user_text(self):
        pages = [
            "helpdesk/templates/helpdesk/ticket/ticket_detail.html",
            "helpdesk/templates/helpdesk/ticket/ticket_individual_view.html",
            "horilla_theme/templates/helpdesk/ticket/ticket_detail.html",
            "pms/templates/okr/objective_detailed_view_activity.html",
            "horilla_theme/templates/okr/objective_detailed_view_activity.html",
        ]
        for relative in pages:
            with self.subTest(relative):
                source = self._source(relative)
                self.assertNotRegex(
                    source,
                    r"(description|comment|history\.[\w.]*|\.old|\.new|\.field)\s*(:[\w.]+)?\s*\|\s*safe",
                )
                self.assertIn("sanitize_html", source)

    def test_faq_pages_do_not_paste_questions_into_script_with_safe(self):
        for relative in (
            "helpdesk/templates/helpdesk/faq/faq_view.html",
            "horilla_theme/templates/helpdesk/faq/faq_view.html",
        ):
            with self.subTest(relative):
                source = self._source(relative)
                self.assertNotRegex(source, r"questions\s*\|\s*safe")
                self.assertIn("json_script", source)
