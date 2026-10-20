"""
Values a model builds for a detail window or table cell (e.g. a coloured status span)
must show as formatting, not as the literal tag text - including translated text that
is built lazily with format_lazy.
"""

from django.test import SimpleTestCase
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from horilla_views.templatetags.generic_template_filters import cell_value, safe_cell


class Record:
    name = "<script>alert(1)</script>"  # stored text, typed by someone

    def status(self):
        return format_lazy('<span class="link-primary">{}</span>', _("Requested to return"))

    def plain_status(self):
        return '<span class="ok">Done</span>'


class CellValueTests(SimpleTestCase):
    def render(self, attr):
        return str(safe_cell(cell_value(Record(), attr)))

    def test_a_lazy_status_built_by_a_method_keeps_its_formatting(self):
        self.assertEqual(self.render("status"), '<span class="link-primary">Requested to return</span>')

    def test_a_plain_string_status_still_works(self):
        self.assertEqual(self.render("plain_status"), '<span class="ok">Done</span>')

    def test_stored_text_is_still_cleaned(self):
        self.assertNotIn("<script", self.render("name"))
