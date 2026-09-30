"""
M-8: the only two link_callback(uri, rel) functions in the codebase (base/
methods.py, horilla_views/views.py) let a `file://`/`http(s)://` URI through
as-is, and had a fallback that resolved any relative path against
BASE_DIR - a local-file-read/SSRF surface if ever reachable from user
input. Neither was actually reachable: base/methods.py's copy was only used
by a generate_pdf() that was already fully commented out; horilla_views/
views.py's copy was only ever passed to pisa.CreatePDF() inside
export_data(), which is fully disabled (see horilla_views/views.py's
"DISABLED - Critical security finding C-1" docstring). The one real,
live PDF path (leave/views.py's pisa.CreatePDF call) never used either.
Removed rather than hardened, since nothing calls them.
"""

from django.test import SimpleTestCase


class DeadLinkCallbacksAreGoneTests(SimpleTestCase):
    def test_neither_copy_exists_any_more(self):
        import base.methods as base_methods
        import horilla_views.views as horilla_views

        self.assertFalse(hasattr(base_methods, "link_callback"))
        self.assertFalse(hasattr(horilla_views, "link_callback"))

    def test_the_one_real_pdf_path_never_used_them(self):
        import inspect

        import leave.views as leave_views

        source = inspect.getsource(leave_views)
        self.assertNotIn("link_callback", source)
