"""
Row locks on company-scoped models.

With a company selected, the default manager adds DISTINCT, and PostgreSQL
refuses FOR UPDATE together with DISTINCT. SQLite ignores FOR UPDATE, so the
bug only shows on the production database. Every lock on such a model must go
through .entire().
"""

import re
from pathlib import Path

from django.conf import settings
from django.db import transaction

from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee

LOCK_SITES = [
    "onboarding/stepper.py",
    "leave/views.py",
    "attendance/views/clock_in_out.py",
    "payroll/methods/methods.py",
]


class RowLockTests(IsolatedTestCase):
    def test_locking_works_while_a_company_is_selected(self):
        from base.models import Company
        from horilla.horilla_middlewares import set_selected_company
        from employee.models import Employee

        _, employee = make_employee("lock_user")
        company = Company.objects.create(company="Lock Co", address="a", country="c", state="s", city="c", zip="1")
        set_selected_company(company.pk)
        with transaction.atomic():
            found = Employee.objects.entire().select_for_update().get(pk=employee.pk)
        self.assertEqual(found.pk, employee.pk)

    def test_no_lock_site_uses_the_company_manager_directly(self):
        pattern = re.compile(r"\b(Employee|Candidate|AvailableLeave)\.objects\.select_for_update\(")
        for relative in LOCK_SITES:
            text = (Path(settings.BASE_DIR) / relative).read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), relative)
