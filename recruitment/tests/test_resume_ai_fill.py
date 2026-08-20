"""
recruitment/tests/test_resume_ai_fill.py

Coverage for the date-parsing and total-experience math in
recruitment/resume_ai_fill.py. These are pure functions (no LLM call, no
DB), covering resume date formats/quirks seen in practice: month/year-only
dates (no day), ordinal suffixes, "Present"/"Current" markers, and the
whole-months-of-service rounding used for the experience_years/months
summary.
"""

from django.test import TestCase

from recruitment.resume_ai_fill import (
    _clean_experience_list,
    _estimate_total_experience,
    _parse_date_loose,
)


class ParseDateLooseTests(TestCase):
    def test_full_date_formats(self):
        self.assertEqual(_parse_date_loose("2019-06-15"), "2019-06-15")
        self.assertEqual(_parse_date_loose("15/06/2019"), "2019-06-15")
        self.assertEqual(_parse_date_loose("15 Jun 2019"), "2019-06-15")
        self.assertEqual(_parse_date_loose("June 15, 2019"), "2019-06-15")

    def test_month_year_only_defaults_to_first_of_month(self):
        self.assertEqual(_parse_date_loose("2019-06"), "2019-06-01")
        self.assertEqual(_parse_date_loose("06/2019"), "2019-06-01")
        self.assertEqual(_parse_date_loose("06-2019"), "2019-06-01")
        self.assertEqual(_parse_date_loose("Jun 2019"), "2019-06-01")
        self.assertEqual(_parse_date_loose("June 2019"), "2019-06-01")
        self.assertEqual(_parse_date_loose("Jun, 2019"), "2019-06-01")

    def test_year_only(self):
        self.assertEqual(_parse_date_loose("2019"), "2019-01-01")

    def test_ordinal_suffixes_are_stripped(self):
        self.assertEqual(_parse_date_loose("15th June 2019"), "2019-06-15")
        self.assertEqual(_parse_date_loose("1st Jan 2020"), "2020-01-01")
        self.assertEqual(_parse_date_loose("3rd March 2021"), "2021-03-03")

    def test_trailing_note_and_time_component_are_stripped(self):
        self.assertEqual(_parse_date_loose("June 2019 (Expected)"), "2019-06-01")
        self.assertEqual(_parse_date_loose("2019-06-01T00:00:00"), "2019-06-01")
        self.assertEqual(_parse_date_loose("2019-06-01 00:00:00"), "2019-06-01")

    def test_ongoing_markers_return_none_not_a_date(self):
        for word in ("Present", "Current", "current", "Ongoing", "Now", "Till Date"):
            self.assertIsNone(_parse_date_loose(word))

    def test_blank_and_garbage_return_none(self):
        self.assertIsNone(_parse_date_loose(""))
        self.assertIsNone(_parse_date_loose(None))
        self.assertIsNone(_parse_date_loose("not a date"))


class EstimateTotalExperienceTests(TestCase):
    def _experience(self, start_date, end_date, is_current=False):
        return _clean_experience_list(
            [
                {
                    "designation": "Engineer",
                    "company_name": "Acme",
                    "start_date": start_date,
                    "end_date": end_date,
                    "is_current": is_current,
                }
            ]
        )

    def test_month_year_only_dates_are_measurable(self):
        entries = self._experience("2018-01", "2020-06")
        years, months = _estimate_total_experience(entries)
        self.assertEqual((years, months), (2, 5))

    def test_partial_month_not_counted(self):
        # Started the 15th, left on the 10th of the following month - less
        # than a full month of service.
        entries = self._experience("15th Jan 2020", "10th Feb 2020")
        self.assertEqual(_estimate_total_experience(entries), (None, None))

    def test_completed_month_is_counted(self):
        # Started the 15th, left on/after the 15th of the following month -
        # one complete month.
        entries = self._experience("15th Jan 2020", "20th Feb 2020")
        self.assertEqual(_estimate_total_experience(entries), (0, 1))

    def test_entry_missing_start_date_is_skipped_not_crashed(self):
        entries = self._experience("", "2020-06")
        self.assertEqual(_estimate_total_experience(entries), (None, None))

    def test_no_experience_entries_returns_none(self):
        self.assertEqual(_estimate_total_experience([]), (None, None))

    def test_multiple_jobs_sum_together(self):
        entries = self._experience("2018-01", "2019-01")  # 1 year
        entries += self._experience("2019-02", "2020-02")  # 1 year
        self.assertEqual(_estimate_total_experience(entries), (2, 0))
