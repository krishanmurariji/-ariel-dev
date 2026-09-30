"""
M-7: mail templates (HorillaMailTemplate.body, MailAutomation.title, the
bulk-feedback review-cycle title, ...) are admin/HR-editable free text that
gets run through Django's template engine with real model instances (and,
in several places, the live `request`) in its Context. Since Django template
variable resolution supports dotted attribute traversal, an editable body
like `{{ instance.employee_user_id.password }}` or `{{ request.session }}`
could leak password hashes or session/request internals to anyone who can
see the rendered mail/preview - not just the admins who can edit templates.

base.methods already had the fix for this (sanitize_mail_template_body/
sanitize_mail_template_placeholders strip any `{{ ... }}`/`{% ... %}`
referencing a forbidden attribute or tag; build_safe_template_request swaps
the raw `request` for a safe id/username/method/path-only proxy) but only 3
of the 11 live `template.Template(...)` call sites across the codebase
actually used it. This file tests the sanitizer functions directly, tests
the fix end-to-end through a real call site (recruitment/scheduler.py's
closing-reminder mail), and pins down (via source inspection, so a future
edit can't silently drop the wrapper) that every live call site in the 8
files touched for this fix still wraps its body in the sanitizer.
"""

import inspect
import re
from unittest.mock import patch

from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, TestCase

from base.methods import (
    build_safe_template_request,
    sanitize_mail_template_body,
    sanitize_mail_template_placeholders,
)
from base.test_security_regressions import make_user


class SanitizeMailTemplateBodyTests(SimpleTestCase):
    def test_strips_password_attribute_access(self):
        body = "Hi {{ instance.employee_user_id.password }} bye"
        self.assertEqual(sanitize_mail_template_body(body), "Hi  bye")

    def test_strips_username_attribute_access(self):
        body = "{{ request.user.username }}"
        self.assertEqual(sanitize_mail_template_body(body), "")

    def test_strips_meta_attribute_access(self):
        body = "{{ request.META.HTTP_COOKIE }}"
        self.assertEqual(sanitize_mail_template_body(body), "")

    def test_strips_session_attribute_access(self):
        body = "{{ request.session.items }}"
        self.assertEqual(sanitize_mail_template_body(body), "")

    def test_strips_private_state_attribute(self):
        body = "{{ instance._state.db }}"
        self.assertEqual(sanitize_mail_template_body(body), "")

    def test_whitespace_bypass_is_still_caught(self):
        body = "{{ instance . employee_user_id . password }}"
        self.assertEqual(sanitize_mail_template_body(body), "")

    def test_filter_bypass_is_still_caught(self):
        body = "{{ instance.employee_user_id.password|upper }}"
        self.assertEqual(sanitize_mail_template_body(body), "")

    def test_strips_debug_and_load_tags(self):
        self.assertEqual(sanitize_mail_template_body("{% debug %}"), "")
        self.assertEqual(sanitize_mail_template_body("{% load whatever %}"), "")

    def test_preserves_legitimate_placeholder(self):
        body = "Hello {{ instance.get_full_name }},"
        self.assertEqual(sanitize_mail_template_body(body), body)

    def test_preserves_legitimate_placeholder_with_filter(self):
        body = "{{ instance.title|upper }}"
        self.assertEqual(sanitize_mail_template_body(body), body)

    def test_preserves_ordinary_tags(self):
        body = "{% if instance %}Hi{% endif %}"
        self.assertEqual(sanitize_mail_template_body(body), body)

    def test_none_and_empty_body_pass_through(self):
        self.assertIsNone(sanitize_mail_template_body(None))
        self.assertEqual(sanitize_mail_template_body(""), "")

    def test_end_to_end_render_never_leaks_the_forbidden_value(self):
        """
        Proves the fix at the level every call site actually uses it:
        wrap the body, build a real django.template.Template from it, and
        render it against an object that really does have a `password`
        attribute reachable by that dotted path.
        """

        class FakeUser:
            password = "pbkdf2_sha256$super-secret-hash"

        class FakeEmployee:
            employee_user_id = FakeUser()

            def get_full_name(self):
                return "Ada Lovelace"

        hostile_body = (
            "Dear {{ instance.get_full_name }}, "
            "{{ instance.employee_user_id.password }} "
            "also {{ instance . employee_user_id . password }}"
        )
        rendered = Template(sanitize_mail_template_body(hostile_body)).render(
            Context({"instance": FakeEmployee()})
        )
        self.assertIn("Ada Lovelace", rendered)
        self.assertNotIn("super-secret-hash", rendered)
        self.assertNotIn("password", rendered)


class SanitizeMailTemplatePlaceholdersTests(SimpleTestCase):
    def test_keeps_only_allowlisted_variables(self):
        body = "{{ instance.get_full_name }} {{ instance.employee_user_id.password }}"
        result = sanitize_mail_template_placeholders(body, {"instance.get_full_name"})
        self.assertIn("{{ instance.get_full_name }}", result)
        self.assertNotIn("password", result)

    def test_strips_unknown_variable_even_without_forbidden_words(self):
        body = "{{ instance.some_unlisted_method }}"
        result = sanitize_mail_template_placeholders(body, {"instance.get_full_name"})
        self.assertNotIn("some_unlisted_method", result)

    def test_whitespace_bypass_is_matched_against_the_allowlist(self):
        body = "{{ instance . get_full_name }}"
        result = sanitize_mail_template_placeholders(body, {"instance.get_full_name"})
        self.assertIn("instance . get_full_name", result)

    def test_none_and_empty_body_pass_through(self):
        self.assertIsNone(sanitize_mail_template_placeholders(None, {"x"}))
        self.assertEqual(sanitize_mail_template_placeholders("", {"x"}), "")


class BuildSafeTemplateRequestTests(TestCase):
    def setUp(self):
        self.user = make_user("safe_req_user", is_staff=True)
        self.request = RequestFactory().post("/some/mail/preview/")
        self.request.user = self.user

    def test_exposes_only_the_safe_subset(self):
        proxy = build_safe_template_request(self.request)
        self.assertEqual(proxy.method, "POST")
        self.assertEqual(proxy.path, "/some/mail/preview/")
        self.assertEqual(proxy.user.username, "safe_req_user")
        self.assertTrue(proxy.user.is_authenticated)
        self.assertTrue(proxy.user.is_staff)
        self.assertFalse(proxy.user.is_superuser)

    def test_session_and_meta_are_not_reachable(self):
        proxy = build_safe_template_request(self.request)
        self.assertFalse(hasattr(proxy, "session"))
        self.assertFalse(hasattr(proxy, "META"))
        self.assertFalse(hasattr(proxy, "COOKIES"))

    def test_user_password_is_not_reachable(self):
        proxy = build_safe_template_request(self.request)
        self.assertFalse(hasattr(proxy.user, "password"))

    def test_template_traversal_through_the_proxy_still_yields_nothing_sensitive(self):
        proxy = build_safe_template_request(self.request)
        rendered = Template(
            sanitize_mail_template_body("{{ request.session }}{{ request.META }}")
        ).render(Context({"request": proxy}))
        self.assertEqual(rendered.strip(), "")


class RecruitmentClosingReminderSanitizationTests(TestCase):
    """
    Integration test for the one fixed call site that is a plain function
    (recruitment/scheduler.py::_send_closing_reminder_mail) and so can be
    exercised directly without spinning up a full view/request stack -
    proves the fix works through the real render pipeline (letterhead
    wrapping included), not just against the sanitizer in isolation.
    """

    def setUp(self):
        from employee.models import Employee
        from recruitment.models import Recruitment

        head_user = make_user("closing_reminder_head")
        head_employee = Employee.objects.get(employee_user_id=head_user)
        self.recruitment = Recruitment.objects.create(title="Backend Engineer")
        self.recruitment.recruitment_heads.add(head_employee)

    def test_hostile_template_body_never_leaks_the_password_hash(self):
        from recruitment.scheduler import _send_closing_reminder_mail

        hostile_body = (
            "{{ instance.title }} closes soon. "
            "{{ instance.recruitment_heads.all.0.employee_user_id.password }}"
        )
        fake_backend = type(
            "FakeBackend", (), {"dynamic_from_email_with_display_name": "hr@example.com"}
        )()

        # _send_closing_reminder_mail imports EmailMultiAlternatives locally
        # (`from django.core.mail import EmailMultiAlternatives`) inside its
        # own body, so the patch target is the real source module, not a
        # `recruitment.scheduler` module attribute.
        with patch("django.core.mail.EmailMultiAlternatives") as email_cls:
            _send_closing_reminder_mail(self.recruitment, hostile_body, fake_backend)

        self.assertTrue(email_cls.called)
        sent_body = email_cls.call_args.kwargs["body"]
        self.assertIn("Backend Engineer", sent_body)
        self.assertNotIn("password", sent_body)
        self.assertNotIn("recruitment_heads", sent_body)


class TemplateSanitizationWiringTests(SimpleTestCase):
    """
    Source-level regression guard: every one of the 8 files touched for
    this fix has every *live, rendered* `template.Template(...)` call
    wrapped in `sanitize_mail_template_body(...)`. A future edit that adds
    a new call site or accidentally unwraps an existing one (e.g. during
    a refactor) will fail this test even though nothing about run-time
    behavior would otherwise flag it in normal use.
    """

    @staticmethod
    def _live_source(module):
        """Source with commented-out lines dropped, so a dead
        `# template.Template(...)` reference doesn't get counted as a
        live call site."""
        return "\n".join(
            line
            for line in inspect.getsource(module).splitlines()
            if not line.strip().startswith("#")
        )

    def _assert_every_template_call_is_sanitized(self, module, expected_call_count):
        source = self._live_source(module)
        calls = re.findall(r"template\.Template\(\s*(?:\n\s*)?([^\n]*)", source)
        self.assertEqual(
            len(calls),
            expected_call_count,
            f"expected {expected_call_count} template.Template( sites in "
            f"{module.__name__}, found {len(calls)}: {calls}",
        )
        for call in calls:
            self.assertIn(
                "sanitize_mail_template_body(",
                call,
                f"unsanitized template.Template( call in {module.__name__}: {call!r}",
            )

    def test_horilla_automations_signals(self):
        import horilla_automations.signals as mod

        self._assert_every_template_call_is_sanitized(mod, 4)
        self.assertNotIn('"request": request,', inspect.getsource(mod))
        self.assertNotIn('"request": request}', inspect.getsource(mod))

    def test_horilla_api_attendance_views(self):
        import horilla_api.api_views.attendance.views as mod

        self._assert_every_template_call_is_sanitized(mod, 3)

    def test_employee_not_in_out_dashboard(self):
        import employee.not_in_out_dashboard as mod

        source = inspect.getsource(mod)
        # get_mail_preview's `template.Template(body)` at the render call
        # renders `body` that was already sanitized (and placeholder-
        # allowlisted) a few lines above, unlike every other site here
        # which sanitizes inline - so it's excluded from the generic
        # "sanitize_mail_template_body(" -in-the-same-call check and
        # asserted separately instead.
        calls = re.findall(r"template\.Template\(\s*(?:\n\s*)?([^\n]*)", source)
        self.assertEqual(len(calls), 3)
        inline_sanitized = [c for c in calls if "sanitize_mail_template_body(" in c]
        self.assertEqual(len(inline_sanitized), 2)
        self.assertIn("template.Template(body)", source)
        self.assertIn("body = sanitize_mail_template_body(body)", source)

    def test_recruitment_views_views(self):
        import recruitment.views.views as mod

        self._assert_every_template_call_is_sanitized(mod, 2)
        self.assertIn("build_safe_template_request(request)", inspect.getsource(mod))

    def test_onboarding_views(self):
        import onboarding.views as mod

        self._assert_every_template_call_is_sanitized(mod, 1)

    def test_recruitment_scheduler(self):
        import recruitment.scheduler as mod

        source = self._live_source(mod)
        calls = re.findall(r"django_template\.Template\(\s*(?:\n\s*)?([^\n]*)", source)
        self.assertEqual(len(calls), 1)
        self.assertIn("sanitize_mail_template_body(", calls[0])

    def test_pms_cbvs(self):
        import pms.cbvs as mod

        self._assert_every_template_call_is_sanitized(mod, 1)

    def test_recruitment_views_actions(self):
        import recruitment.views.actions as mod

        source = self._live_source(mod)
        calls = re.findall(r"template\.Template\(\s*(?:\n\s*)?([^\n]*)", source)
        # 5 sites total:
        #   553, 578 - body sanitized inline
        #   556      - a `{{ word }}` snippet built from a hint keyword,
        #              never rendered (its .render() call is commented out)
        #   585      - `sanitized_word_template`, pre-sanitized on the
        #              previous line rather than inline
        #   639      - `body`, pre-sanitized (and placeholder-allowlisted)
        #              earlier in the same view, same pattern as
        #              employee/not_in_out_dashboard.py's get_mail_preview
        self.assertEqual(len(calls), 5)
        sanitized_inline = [c for c in calls if "sanitize_mail_template_body(" in c]
        self.assertEqual(len(sanitized_inline), 2)
        self.assertIn('template.Template("{{" + word + "}}")', source)
        self.assertIn("template.Template(sanitized_word_template)", source)
        self.assertIn(
            'sanitized_word_template = sanitize_mail_template_body("{{" + word + "}}")',
            source,
        )
        self.assertIn("template.Template(body)", source)
        self.assertIn("body = sanitize_mail_template_body(body)", source)
        # The one unsanitized-looking hint-word Template is provably inert:
        # nothing ever calls .render() on it in this function.
        get_template_fn = source[source.index("def get_template("):]
        get_template_fn = get_template_fn[: get_template_fn.index("\ndef ", 1)]
        self.assertNotIn(".render(", get_template_fn)
