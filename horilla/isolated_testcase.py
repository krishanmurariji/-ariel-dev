"""
horilla/isolated_testcase.py

A TestCase base class for tests that save HorillaModel rows or make requests
through the middleware stack.

CompanyMiddleware stores the current request in a context variable that is
never cleared, so in a full-suite run the *previous* test's request (and its
user, which no longer exists) is still there when the next test saves a
model; HorillaModel.save() then stamps modified_by with that stale user and
SQLite rejects the dangling foreign key when the test ends - tests that pass
alone fail in the full run. This clears it before each test, and again
afterwards so this test's own requests don't poison the next one either.
"""

from django.test import TestCase

from horilla.horilla_middlewares import _thread_locals


class IsolatedTestCase(TestCase):
    def _pre_setup(self):
        super()._pre_setup()
        _thread_locals.request = None

    def _post_teardown(self):
        _thread_locals.request = None
        super()._post_teardown()
