"""
Saving a rejection from the Update Status window ("Add to Reject"). It used update_or_create,
which locks the row (FOR UPDATE); while a company is selected the company manager adds DISTINCT,
and PostgreSQL refuses the pair - a 500. SQLite ignores row locks, so these tests make a row
lock itself an error.
"""

from unittest import mock

from django.db.models.query import QuerySet
from django.test import TestCase

from base.models import Company, Department, JobPosition
from recruitment.cbv.pipeline import _save_rejection
from recruitment.models import Candidate, RejectedCandidate, RejectReason, Recruitment


class SaveRejectionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            company="Test Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        department = Department.objects.create(department="Engineering")
        job = JobPosition.objects.create(job_position="Backend Engineer", department_id=department)
        recruitment = Recruitment.objects.create(
            title="Backend Engineer", closed=False, is_active=True, company_id=self.company, job_position_id=job
        )
        recruitment.open_positions.add(job)
        self.candidate = Candidate.objects.create(
            name="Jane", email="j@example.com", recruitment_id=recruitment, job_position_id=job
        )
        self.reasons = [RejectReason.objects.create(title=t, description="x") for t in ("Not a fit", "Salary")]

    def no_row_lock(self):
        return mock.patch.object(QuerySet, "select_for_update", side_effect=AssertionError("row lock"))

    def selected_company(self):
        return mock.patch("base.horilla_company_manager.get_selected_company", return_value=self.company.pk)

    def test_it_saves_with_a_company_selected_and_never_takes_a_row_lock(self):
        with self.selected_company(), self.no_row_lock():
            rejected = _save_rejection(self.candidate, "no", self.reasons[:1])
        self.assertEqual(rejected.description, "no")
        self.assertEqual([r.pk for r in rejected.reject_reason_id.all()], [self.reasons[0].pk])

    def test_rejecting_again_updates_the_same_record(self):
        with self.selected_company(), self.no_row_lock():
            first = _save_rejection(self.candidate, "first", self.reasons[:1])
            second = _save_rejection(self.candidate, "second", self.reasons)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(RejectedCandidate.objects.entire().filter(candidate_id=self.candidate).count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.description, "second")
        self.assertEqual(second.reject_reason_id.count(), 2)

    def test_the_company_scoped_lookup_really_is_distinct(self):
        # why the old call failed: with a company selected, .objects adds DISTINCT
        with self.selected_company():
            self.assertTrue(RejectedCandidate.objects.all().query.distinct)
            self.assertFalse(RejectedCandidate.objects.entire().query.distinct)
