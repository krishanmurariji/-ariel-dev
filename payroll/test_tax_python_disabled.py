"""
Python tax formulas are disabled: nothing may execute stored code, the save
endpoint must refuse, the form must not offer the switch, and a filing status
that still has use_py switched on must be taxed from its brackets (with a
warning) rather than run code or silently produce nothing.
"""

import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.urls import reverse

from employee.models import Employee
from horilla_auth.models import HorillaUser
from payroll.forms.tax_forms import FilingStatusForm
from payroll.methods.safe_tax_code import TaxCodeValidationError, run_tax_code
from payroll.methods.tax_calc import calculate_taxable_amount
from payroll.models.models import FilingStatus


class RunTaxCodeIsDisabledTest(TestCase):
    def test_a_harmless_formula_is_not_executed(self):
        code = "def calculate_federal_tax(yearly_income):\n    return 123.0\n"
        with self.assertRaises(TaxCodeValidationError):
            run_tax_code(code, 100000)

    def test_a_hostile_formula_is_not_executed(self):
        # Would have escaped the old denylist sandbox and/or hung a worker.
        code = (
            "def calculate_federal_tax(yearly_income):\n"
            "    while True:\n"
            "        pass\n"
        )
        with self.assertRaises(TaxCodeValidationError):
            run_tax_code(code, 100000)


class FilingStatusFormTest(TestCase):
    def test_new_form_does_not_offer_the_python_switch_or_editor(self):
        form = FilingStatusForm()
        self.assertNotIn("use_py", form.fields)
        self.assertNotIn("python_code", form.fields)

    def test_edit_form_does_not_offer_them_either(self):
        filing = FilingStatus.objects.create(filing_status="Single")
        form = FilingStatusForm(instance=filing)
        self.assertNotIn("use_py", form.fields)
        self.assertNotIn("python_code", form.fields)

    def test_posting_use_py_and_code_is_ignored(self):
        form = FilingStatusForm(
            data={
                "filing_status": "Sneaky",
                "based_on": "taxable_gross_pay",
                "description": "",
                "use_py": "on",
                "python_code": "def calculate_federal_tax(y):\n    return 0\n",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        filing = form.save()
        filing.refresh_from_db()
        self.assertFalse(filing.use_py)
        self.assertFalse(filing.python_code)


class UpdatePyCodeEndpointTest(TestCase):
    def setUp(self):
        self.user = HorillaUser.objects.create(
            username="tax_admin",
            password=make_password("pw-not-used"),
            is_active=True,
            is_superuser=True,
            is_staff=True,
        )
        Employee.objects.create(
            employee_first_name="Tax",
            employee_last_name="Admin",
            employee_user_id=self.user,
            email="tax_admin@example.com",
        )
        self.filing = FilingStatus.objects.create(
            filing_status="Single", python_code="ORIGINAL"
        )
        self.client.force_login(self.user)

    def test_saving_code_is_refused_and_nothing_is_stored(self):
        url = reverse("update-py-code", kwargs={"pk": self.filing.pk})
        response = self.client.post(
            url, {"code": "def calculate_federal_tax(y):\n    return 1\n"}
        )
        self.assertEqual(response.status_code, 403)
        self.filing.refresh_from_db()
        self.assertEqual(self.filing.python_code, "ORIGINAL")


class UsePyFilingStatusUsesBracketsTest(TestCase):
    """calculate_taxable_amount for a filing status with use_py still on."""

    def _run(self, use_py):
        filing = MagicMock(use_py=use_py, based_on="basic_pay")
        filing.__str__ = lambda self: "Legacy status"
        filing.pk = 7
        contract = MagicMock(filing_status=filing)

        brackets = MagicMock()
        brackets.values.return_value = [
            {"tax_rate": 10, "min_income": 0, "max_income": 10000},
            {"tax_rate": 20, "min_income": 10000, "max_income": 10**9},
        ]
        brackets.exists.return_value = True
        brackets.count.return_value = 2

        with patch("payroll.methods.tax_calc.Contract") as contract_model, patch(
            "payroll.methods.tax_calc.TaxBracket"
        ) as bracket_model, patch(
            "payroll.methods.tax_calc.compute_yearly_taxable_amount",
            side_effect=lambda income, yearly: yearly,
        ), patch(
            "payroll.methods.tax_calc.convert_year_tax_to_period",
            side_effect=lambda **kw: kw["federal_tax_for_period"],
        ):
            contract_model.objects.filter.return_value.first.return_value = contract
            bracket_model.objects.filter.return_value.order_by.return_value = brackets
            return calculate_taxable_amount(
                employee=1,
                start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2026, 12, 31),
                basic_pay=100000,
            )

    def test_taxed_from_brackets_with_a_warning(self):
        with self.assertLogs("payroll.methods.tax_calc", level="WARNING") as logs:
            tax = self._run(use_py=True)
        # 10% of the first 10,000 + 20% of the remaining 90,000
        self.assertAlmostEqual(tax, 1000 + 18000, places=2)
        self.assertTrue(any("Python tax formulas" in line for line in logs.output))

    def test_normal_filing_status_is_unchanged_and_does_not_warn(self):
        with self.assertNoLogs("payroll.methods.tax_calc", level="WARNING"):
            tax = self._run(use_py=False)
        self.assertAlmostEqual(tax, 1000 + 18000, places=2)
