"""
A GET request must never change data.

Django does not CSRF-check GET, so a view that writes on GET can be triggered by a
single link a logged-in person clicks. Every view listed below used to delete,
archive, approve, reject or block on a plain GET; each now accepts POST only, which
the CSRF middleware protects. The tests pin, for each one:

* GET is refused (405) even for a superuser, before anything is looked up or changed
* POST is not refused by the method check (the pages that call it still work)
* a POST without a CSRF token is refused (403)
* no template or script calls it with a GET any more (hx-get, link, GET form)

and that the shared helper which submits the converted links is on every layout.
"""

import os
import re

from django.conf import settings
from django.test import Client
from django.urls import get_resolver, reverse
from django.urls.converters import IntConverter, UUIDConverter

from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee

HX = {"HTTP_HX_REQUEST": "true"}

POST_ONLY_URL_NAMES = [
    'action-type-delete',
    'allocation-request-delete-comment',
    'approve-claim-request',
    'approve-compensatory-leave',
    'approve-overtime',
    'approve-reimbursements',
    'approve-validate-attendance-request',
    'archive-anonymous-feedback',
    'archive-employee-objective',
    'archive-key-result',
    'archive-meeting',
    'asset-allocate-return-request',
    'asset-batch-number-delete',
    'asset-category-delete',
    'asset-request-reject',
    'assign-delete',
    'assign-task',
    'assignees-remove',
    'attendance-request-delete-comment',
    'biometric-device-archive',
    'biometric-device-delete',
    'biometric-device-live-capture',
    'biometric-device-unschedule',
    'cancel-validate-attendance-request',
    'candidate-conversion',
    'candidate-delete',
    'candidate-document-approve',
    'candidate-document-delete',
    'candidate-document-under-verification',
    'candidate-intake-mailbox-delete',
    'candidate-note-delete',
    'candidate-notes-mark-read',
    'candidate-portal-mail-setting',
    'candidate-self-tracking',
    'candidate-self-tracking-rating-option',
    'change-employee-objective-status',
    'change-task-status',
    'claim-ticket',
    'clear-notifications',
    'clock-out',
    'comment-delete',
    'company-leave-delete',
    'compensatory-request-delete-comment',
    'create-interview-google-meeting',
    'create-pms-google-meeting',
    'dashboard-components-toggle',
    'default-meeting-provider',
    'default-probation-period',
    'delete-actions',
    'delete-all-notifications',
    'delete-allocation-comment-file',
    'delete-allowance',
    'delete-allowances',
    'delete-allowed-ip',
    'delete-anonymous-feedback',
    'delete-auto-payslip',
    'delete-automation',
    'delete-batch',
    'delete-biometric-user',
    'delete-bonus-point-setting',
    'delete-candidate-note-file',
    'delete-candidate-rejection',
    'delete-comment-file',
    'delete-compensatory-leave',
    'delete-contract',
    'delete-contract-modal',
    'delete-cosec-user',
    'delete-deduction',
    'delete-employee-allowance',
    'delete-employee-bonus-point',
    'delete-employee-keyresult',
    'delete-employee-note-file',
    'delete-employee-objective',
    'delete-gmeet',
    'delete-gmeet-credentials',
    'delete-google-meet',
    'delete-grace-time',
    'delete-individual-note-file',
    'delete-interview',
    'delete-leave-comment-file',
    'delete-linkedin-account',
    'delete-loan',
    'delete-mail-template',
    'delete-note-attachment',
    'delete-notifications',
    'delete-offboarding',
    'delete-offboarding-employee',
    'delete-offboarding-stage',
    'delete-offboarding-task',
    'delete-payslip',
    'delete-penalties',
    'delete-platforms',
    'delete-policies',
    'delete-profile-image',
    'delete-project',
    'delete-project-stage',
    'delete-reimbursement',
    'delete-reimbursement-comment-file',
    'delete-reject-reasons',
    'delete-resignation-request',
    'delete-resume-file',
    'delete-shift-comment-file',
    'delete-skills',
    'delete-stage-note-file',
    'delete-task',
    'delete-ticket-document',
    'delete-time-sheet',
    'delete-work-type-comment-file',
    'department-manager-delete',
    'document-delete',
    'employee-account-block-unblock',
    'employee-archive',
    'employee-note-delete',
    'enable-resignation-request',
    'enable-timerunner',
    'faq-category-delete',
    'faq-delete',
    'feedback-archive',
    'feedback-delete',
    'filing-status-delete',
    'gdrive_delete',
    'gdrive_start_stop',
    'generic-template-creation',
    'get-manager-in',
    'holiday-delete',
    'initial-notice-period',
    'initialize-department-delete',
    'interview-employee-remove',
    'leave-allocation-request-approve',
    'leave-allocation-request-delete',
    'leave-request-delete-comment',
    'mail-server-delete',
    'meeting-employee-remove',
    'meeting-manager-remove',
    'multiple-level-approval-delete',
    'note-delete',
    'note-delete-individual',
    'notification-sound',
    'objective-archive',
    'objective-delete',
    'offboarding-note-delete',
    'offer-letter-bulk-status-update',
    'onboarding-candidate-bulk-delete',
    'payroll-request-delete-comment',
    'period-delete',
    'platform-conversion-widget-setting',
    'pp-auto-settings-delete',
    'pp-component-delete',
    'pp-structure-delete',
    'pp-tax-regime-delete',
    'project-archive',
    'question-delete',
    'question-template-delete',
    'rec-candidate-archive',
    'recruitment-archive',
    'recruitment-close-pipeline',
    'recruitment-reopen-pipeline',
    'recruitment-survey-question-template-delete',
    'remove-attachment-policy',
    'remove-employee-disciplinary-action',
    'request-delete',
    'restrict-delete',
    'revalidate-this-attendance',
    'rotating-shift-assign-archive',
    'rotating-work-type-assign-archive',
    'shift-allocation-request-approve',
    'shift-allocation-request-cancel',
    'shift-request-approve',
    'shift-request-cancel',
    'shift-request-delete-comment',
    'skill-zone-archive',
    'skill-zone-cand-archive',
    'skill-zone-cand-delete',
    'skill-zone-delete',
    'stage-delete',
    'survey-template-delete',
    'task-all-archive',
    'task-delete',
    'tax-bracket-delete',
    'ticket-archive',
    'ticket-delete',
    'type-delete',
    'update-kanban-group-sequence',
    'update-kanban-item-group',
    'update-kanban-sequence',
    'update-offer-letter-status',
    'update-project-task-status',
    'user-request-delete',
    'validate-this-attendance',
    'whatsapp-credential-delete',
    'work-type-request-approve',
    'work-type-request-cancel',
    'worktype-request-delete-comment',
]

SKIP_DIRS = {"venv", "node_modules", ".git", "staticfiles", "migrations"}


def url_for(name):
    """Reverse ``name`` with placeholder arguments (ids that cannot exist)."""
    for possibility, _pattern, _defaults, converters in get_resolver().reverse_dict.getlist(name):
        for fmt, params in possibility:
            kwargs = {}
            for p in params:
                converter = converters.get(p)
                if isinstance(converter, IntConverter):
                    kwargs[p] = 999999
                elif isinstance(converter, UUIDConverter):
                    kwargs[p] = "00000000-0000-0000-0000-000000000001"
                else:
                    kwargs[p] = "x"
            try:
                return reverse(name, kwargs=kwargs)
            except Exception:
                continue
    raise AssertionError("cannot build a URL for %s" % name)


class PostOnlyTests(IsolatedTestCase):
    def setUp(self):
        user, _ = make_employee("postonly_admin")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        self.user = user
        # a POST for an id that does not exist may 404 or even crash inside the view;
        # what matters here is only that the method check lets it through
        self.client = Client(raise_request_exception=False)
        self.client.force_login(user)

    def test_get_is_refused_for_every_locked_view(self):
        for name in POST_ONLY_URL_NAMES:
            with self.subTest(url=name):
                response = self.client.get(url_for(name), **HX)
                self.assertEqual(response.status_code, 405, name)

    def test_get_without_the_htmx_header_is_refused_too(self):
        for name in POST_ONLY_URL_NAMES:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(url_for(name)).status_code, 405, name)

    def test_post_gets_past_the_method_check(self):
        for name in POST_ONLY_URL_NAMES:
            with self.subTest(url=name):
                response = self.client.post(url_for(name), **HX)
                self.assertNotEqual(response.status_code, 405, name)

    def test_a_post_without_a_csrf_token_is_refused(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        for name in POST_ONLY_URL_NAMES:
            with self.subTest(url=name):
                self.assertEqual(browser.post(url_for(name), **HX).status_code, 403, name)


def files_with(*suffixes):
    for dirpath, dirnames, filenames in os.walk(settings.BASE_DIR):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(suffixes) and not filename.startswith("test"):
                yield os.path.join(dirpath, filename)


def location(path, text, index):
    return "%s:%d" % (os.path.relpath(path, settings.BASE_DIR), text.count("\n", 0, index) + 1)


class NoGetCallersTests(IsolatedTestCase):
    """The pages must not call a locked view with GET (they would now get a 405)."""

    GET_ATTRS = re.compile(r"(hx-get|data-hx-get|href|src)\s*=\s*[\"']?\s*$", re.IGNORECASE)

    def url_tag(self, name):
        return re.compile(r"\{%\s*url\s+['\"]" + re.escape(name) + r"['\"]")

    def test_no_template_calls_a_locked_view_with_get(self):
        found = []
        wanted = {name: self.url_tag(name) for name in POST_ONLY_URL_NAMES}
        for path in files_with(".html", ".js"):
            text = open(path, encoding="utf-8", errors="replace").read()
            for name, rx in wanted.items():
                for match in rx.finditer(text):
                    before = text[max(0, match.start() - 90): match.start()].replace("\n", " ")
                    tag_start = text.rfind("<", 0, match.start())
                    tag = text[tag_start: text.find(">", match.end()) + 1]
                    attr = self.GET_ATTRS.search(before)
                    if attr and attr.group(1).lower() == "href":
                        # a link is fine only if it is marked to be sent as a POST
                        if "data-post-link" in tag or "hx-post" in tag:
                            continue
                    elif not attr:
                        continue
                    pre = text[: match.start()]
                    if pre.rfind("{% comment %}") > pre.rfind("{% endcomment %}"):
                        continue  # commented-out markup
                    found.append("%s %s" % (location(path, text, match.start()), name))
        self.assertEqual(found, [], "these still call a POST-only view with GET")

    def test_no_form_submits_to_a_locked_view_with_get(self):
        found = []
        for path in files_with(".html"):
            text = open(path, encoding="utf-8", errors="replace").read()
            for name in POST_ONLY_URL_NAMES:
                rx = re.compile(
                    r"<form\b[^>]*?action\s*=\s*[\"']\s*\{%\s*url\s+['\"]"
                    + re.escape(name)
                    + r"['\"][^>]*>",
                    re.S,
                )
                for match in rx.finditer(text):
                    tag = match.group(0)
                    if not re.search(r"method\s*=\s*[\"']?post", tag, re.I) and "hx-post" not in tag:
                        found.append("%s %s" % (location(path, text, match.start()), name))
        self.assertEqual(found, [], "these GET forms still target a POST-only view")

    def test_hard_coded_links_to_the_request_actions_post_too(self):
        rx = re.compile(
            r'<a\b[^>]*?href="/(?:shift-request-approve|shift-request-cancel|'
            r"shift-allocation-request-approve|shift-allocation-request-cancel|"
            r'work-type-request-approve|work-type-request-cancel)/[^>]*>',
            re.S,
        )
        found = []
        for path in files_with(".html"):
            text = open(path, encoding="utf-8", errors="replace").read()
            for match in rx.finditer(text):
                if "data-post-link" not in match.group(0) and "hx-post" not in match.group(0):
                    found.append(location(path, text, match.start()))
        self.assertEqual(found, [])

    def test_model_url_helpers_are_not_used_with_get_for_archive(self):
        """get_archive_url and friends point at POST-only views; nothing may hx-get them."""
        rx = re.compile(
            r"hx-get\s*=\s*[\"']\s*\{+\s*[\w.]*?"
            r"(get_archive_url|detail_archive_url|get_archive_action_url)"
        )
        found = []
        for path in files_with(".html", ".py"):
            text = open(path, encoding="utf-8", errors="replace").read()
            for match in rx.finditer(text):
                found.append(location(path, text, match.start()))
        self.assertEqual(found, [])


class SharedPostHelperTests(IsolatedTestCase):
    """The layouts that render the converted links must carry the helper that submits them."""

    LAYOUTS = (
        "templates/index.html",
        "horilla_theme/templates/horilla_theme/components/footer_scripts.html",
    )

    def test_both_layouts_define_the_post_link_handler_and_post_to(self):
        for rel in self.LAYOUTS:
            text = open(os.path.join(settings.BASE_DIR, rel), encoding="utf-8").read()
            self.assertIn("a[data-post-link]", text, rel)
            self.assertIn("window.postTo", text, rel)
            self.assertIn("csrfmiddlewaretoken", text, rel)
            # the confirm() in the link's own onclick must still be able to cancel it
            self.assertIn("link.onclick", text, rel)

    def test_the_row_action_scripts_post_with_a_token(self):
        for rel in (
            "base/static/cbv/shift_request/shift_request_bulk_actions.js",
            "base/static/cbv/work_type_request/work_request_bulk_action.js",
        ):
            text = open(os.path.join(settings.BASE_DIR, rel), encoding="utf-8").read()
            start = text.index("RowApprove(url, confirmText)")
            body = text[start: text.index("\nfunction ", start)]
            self.assertIn('type: "POST"', body, rel)
            self.assertIn("csrfmiddlewaretoken", body, rel)
            self.assertNotIn('type: "GET"', body, rel)
