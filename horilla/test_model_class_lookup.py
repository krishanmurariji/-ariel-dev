"""
M-6: get_model_class()/generate_choices() (4 copies, in employee/methods/
methods.py, horilla_views/views.py and horilla_automations/methods/
methods.py x2) resolved a "app.models.Model" string stored on
HorillaAutomation.model / DynamicField.model / a bonus-setting field with
__import__(module_name, fromlist=[class_name]) - which imports and returns
*any* Python object reachable by that dotted path, not necessarily a Django
model. Since only automation/dynamic-field edit permission (not necessarily
superuser) is needed to set that string, this let such a user make the
server import and inspect an arbitrary installed module.

All 4 now delegate to horilla.methods.get_model_class_from_path(), which
uses apps.get_model() - it can only ever resolve to a model genuinely
registered with Django.
"""

from django.apps import apps
from django.test import SimpleTestCase

from horilla.methods import get_model_class_from_path


class GetModelClassFromPathTests(SimpleTestCase):
    def test_resolves_a_real_model(self):
        from leave.models import AvailableLeave

        self.assertIs(get_model_class_from_path("leave.models.AvailableLeave"), AvailableLeave)

    def test_resolves_a_model_in_a_nested_module_path(self):
        from payroll.models.models import Contract

        self.assertIs(get_model_class_from_path("payroll.models.models.Contract"), Contract)

    def test_cannot_resolve_to_something_that_is_not_a_model(self):
        # The old __import__-based version would happily return this -
        # os.system, settings, anything importable by dotted path.
        with self.assertRaises(LookupError):
            get_model_class_from_path("django.conf.settings")

    def test_cannot_resolve_an_unregistered_app_label(self):
        with self.assertRaises(LookupError):
            get_model_class_from_path("os.path.PathLike")

    def test_a_nonexistent_model_name_is_refused(self):
        with self.assertRaises(LookupError):
            get_model_class_from_path("leave.models.ThisModelDoesNotExist")


class NoDuplicateUnsafeImportsRemainTests(SimpleTestCase):
    def test_the_four_call_sites_all_use_the_shared_safe_helper(self):
        import employee.methods.methods as employee_methods
        import horilla_automations.methods.methods as automation_methods
        import horilla_views.views as horilla_views

        for real_class in (
            employee_methods.get_model_class,
            horilla_views.get_model_class,
            automation_methods.get_model_class,
        ):
            with self.subTest(real_class.__module__):
                self.assertIs(
                    real_class("leave.models.AvailableLeave"),
                    apps.get_model("leave", "AvailableLeave"),
                )
        # generate_choices() uses the same lookup inline rather than
        # through get_model_class() - just prove it no longer __import__s.
        import inspect

        source = inspect.getsource(automation_methods.generate_choices)
        self.assertNotIn("__import__", source)
