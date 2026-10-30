"""
Mail templates build their links from the site's own address: {{ request.scheme }}://{{ request.get_host }}.
The trimmed-down request that templates get (build_safe_template_request) used to leave those two out, so
the "Open Candidate Portal" button in the application-received mail had no usable address.
"""

import importlib

from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, override_settings

from base.methods import build_safe_template_request, sanitize_mail_template_body

LINK = "{{ request.scheme }}://{{ request.get_host }}/recruitment/candidate-login/"


def render(body, request):
    return Template(sanitize_mail_template_body(body)).render(
        Context({"request": build_safe_template_request(request)})
    )


class MailLinkAddress(SimpleTestCase):
    def test_a_plain_request_gives_its_own_address(self):
        request = RequestFactory().get("/", HTTP_HOST="erp.example.com")
        with override_settings(ALLOWED_HOSTS=["erp.example.com"]):
            self.assertEqual(render(LINK, request), "http://erp.example.com/recruitment/candidate-login/")

    def test_a_secure_request_gives_https(self):
        request = RequestFactory().get("/", secure=True, HTTP_HOST="erp.example.com")
        with override_settings(ALLOWED_HOSTS=["erp.example.com"]):
            self.assertEqual(render(LINK, request), "https://erp.example.com/recruitment/candidate-login/")

    @override_settings(CSRF_TRUSTED_ORIGINS=["not-a-url", "https://erp.example.com"])
    def test_with_no_request_the_configured_origin_is_used(self):
        self.assertEqual(render(LINK, None), "https://erp.example.com/recruitment/candidate-login/")

    @override_settings(ALLOWED_HOSTS=["erp.example.com"], CSRF_TRUSTED_ORIGINS=["https://erp.example.com"])
    def test_a_host_that_is_not_allowed_never_reaches_the_link(self):
        request = RequestFactory().get("/", HTTP_HOST="evil.example.net")
        link = render(LINK, request)
        self.assertNotIn("evil", link)
        self.assertEqual(link, "https://erp.example.com/recruitment/candidate-login/")

    @override_settings(CSRF_TRUSTED_ORIGINS=[])
    def test_with_nothing_configured_it_is_empty_rather_than_failing(self):
        self.assertEqual(render(LINK, None), "://" + "/recruitment/candidate-login/")

    def test_nothing_sensitive_came_along(self):
        request = RequestFactory().get("/")
        proxy = build_safe_template_request(request)
        for name in ("session", "META", "COOKIES", "headers", "body"):
            self.assertFalse(hasattr(proxy, name), name)


class ApplicationReceivedMail(SimpleTestCase):
    """The stored "Application Received" mail, rendered the way the automation renders it."""

    def body(self):
        module = importlib.import_module("recruitment.migrations.0029_restyle_recruitment_mail_templates")
        return module.APPLICATION_RECEIVED_NEW_BODY

    def test_the_portal_button_has_a_real_address(self):
        request = RequestFactory().get("/", secure=True, HTTP_HOST="erp.example.com")
        with override_settings(ALLOWED_HOSTS=["erp.example.com"]):
            html = Template(sanitize_mail_template_body(self.body())).render(
                Context({"instance": None, "request": build_safe_template_request(request)})
            )
        self.assertIn('href="https://erp.example.com/recruitment/candidate-login/"', html)
        self.assertNotIn('href="://', html)
        self.assertNotIn('href=":///', html)
