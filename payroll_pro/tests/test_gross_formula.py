"""
The gross-pay formula box accepts arithmetic on CTC. It used to be run with
eval(), and "**" was allowed, so a formula like 9**9**9**9 never finished and
froze the worker. It is now parsed, not run, and "**" is not supported.
"""

import time

from django.test import SimpleTestCase

from payroll_pro.services import safe_eval_gross_formula as evaluate


class NormalFormulasKeepWorking(SimpleTestCase):
    def test_plain_arithmetic_on_ctc(self):
        cases = {
            "CTC": 100000.0,
            "ctc": 100000.0,
            "CTC*0.9": 90000.0,
            "ctc / 1.0367": 100000 / 1.0367,
            "CTC - 1800": 98200.0,
            "(CTC-2000)*0.95": 93100.0,
            "CTC*90/100": 90000.0,
            "-CTC+CTC*2": 100000.0,
            "0.4*CTC + 0.6 * CTC": 100000.0,
            "((CTC))": 100000.0,
        }
        for formula, expected in cases.items():
            with self.subTest(formula):
                self.assertAlmostEqual(evaluate(formula, 100000), expected)

    def test_invalid_input_is_none_as_before(self):
        for formula in ["", None, 5, "CTC*", "1.2.3", "CTC CTC", "5 5", "ctc/0", "abc"]:
            with self.subTest(formula):
                self.assertIsNone(evaluate(formula, 100000))


class DangerousFormulasAreRefused(SimpleTestCase):
    def test_powers_are_refused_and_return_instantly(self):
        for formula in ["9**9**9**9", "CTC**2", "2**10", "CTC**CTC"]:
            with self.subTest(formula):
                started = time.time()
                self.assertIsNone(evaluate(formula, 100000))
                self.assertLess(time.time() - started, 1)

    def test_other_operators_and_names_are_refused(self):
        for formula in ["CTC//2", "CTC%3", "__import__('os')", "CTC.real", "[1]", "CTC if 1 else 2"]:
            with self.subTest(formula):
                self.assertIsNone(evaluate(formula, 100000))

    def test_absurdly_long_or_deep_input_is_refused(self):
        self.assertIsNone(evaluate("9" * 300, 100000))
        self.assertIsNone(evaluate("(" * 500 + "1" + ")" * 500, 100000))
