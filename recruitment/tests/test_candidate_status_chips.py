"""
Candidate list: the Canceled / Not Hired / Hired chips each set their own filters and reset the
others, so clicking Canceled and then Not Hired does not leave both applied. Not Hired matches the
grey strip of a row: not hired and not canceled.
"""

from django.test import SimpleTestCase

from recruitment.cbv.candidates import ListCandidates


class StatusChipTests(SimpleTestCase):
    def chips(self):
        return {label: attrs for _dot, label, attrs in ListCandidates.row_status_indications}

    def test_each_chip_sets_one_field_and_resets_the_other(self):
        chips = self.chips()
        cases = {
            "Canceled": ("canceled", "true", "hired"),
            "Not Hired": ("hired", "false", None),
            "Hired": ("hired", "true", "canceled"),
        }
        for label, (field, value, other) in cases.items():
            attrs = chips[label]
            self.assertIn(f"find('[name={field}]').val('{value}')", attrs, label)
            if other:
                self.assertIn(f"find('[name={other}]').val('unknown')", attrs, label)
            self.assertIn("$('#applyFilter').click()", attrs, label)

    def test_not_hired_excludes_canceled_candidates(self):
        attrs = self.chips()["Not Hired"]
        self.assertIn("find('[name=canceled]').val('false')", attrs)
        self.assertIn("find('[name=hired]').val('false')", attrs)
