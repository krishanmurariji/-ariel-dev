"""
recruitment/tests/test_intake.py

Full coverage of the Candidate Email Intake pipeline (recruitment/intake.py)
using a FakeProvider test double instead of real Outlook/Gmail/Ollama
calls - mocking happens at the recruitment.intake_providers adapter
boundary (recruitment.intake.get_provider), since that's the one place
provider-specific behavior is allowed to live. This suite never touches
Microsoft Graph or Gmail API shapes directly.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

import fitz
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from base.models import Company, Department, JobPosition
from gmail_auth.models import GoogleApi
from outlook_auth.models import AzureApi
from recruitment import intake
from recruitment.intake_providers.base import (
    IntakeAttachment,
    IntakeListResult,
    IntakeMessage,
)
from recruitment.models import (
    Candidate,
    CandidateIntakeLog,
    CandidateIntakeMailbox,
    IntakePollLock,
    Recruitment,
)


def _build_tiny_pdf() -> bytes:
    """A minimal single-page PDF that actually contains extractable text
    (unlike a hand-rolled empty-page PDF, which fitz correctly reads back
    as blank text - exercising the wrong code path in every test). Long
    enough (well over ai_scoring.MIN_RESUME_WORDS) to also exercise the
    real "successful scoring" path in test_ai_scoring.py, not just the
    "too short to score" branch."""
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "Jane Doe",
        "Backend Engineer Resume",
        "5 years of experience building scalable Python and Django backend systems.",
        "Skills: Python, Django, REST APIs, PostgreSQL, Docker, AWS, Git, CI/CD, "
        "unit testing, agile development.",
        "Experience: Senior Backend Engineer at Example Corp, 2020-present, "
        "building and maintaining microservices.",
        "Backend Developer at Sample Inc, 2018-2020, developing internal tooling.",
        "Education: B.Sc. Computer Science, State University, 2018.",
        "Contact: jane.doe@example.com, +1-555-0100.",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + i * 20), line)
    data = doc.tobytes()
    doc.close()
    return data


TINY_PDF = _build_tiny_pdf()

# PDF with a page but no text-drawing operators - used to test the
# "no extractable text" (scanned-image) branch specifically. Starts with
# %PDF- so it passes the magic-byte check and reaches PyMuPDF.
BLANK_PDF = (
    b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF"
)

DEFAULT_RECEIVED_AT = datetime(2026, 7, 23, 10, 0, 0, tzinfo=dt_timezone.utc)


def make_message(msg_id="msg-1", subject="Application", attachments=None, received=None) -> IntakeMessage:
    return IntakeMessage(
        id=msg_id,
        subject=subject,
        from_email="candidate@example.com",
        received_at=received or DEFAULT_RECEIVED_AT,
        body_text="Please find my resume attached.",
        attachments=attachments or [],
    )


def pdf_attachment(name="resume.pdf", content=TINY_PDF, att_id="att-1") -> IntakeAttachment:
    return IntakeAttachment(
        id=att_id,
        filename=name,
        content_type="application/pdf",
        size=len(content),
        raw=content,
    )


def non_pdf_attachment() -> IntakeAttachment:
    content = b"not a pdf"
    return IntakeAttachment(
        id="att-photo",
        filename="photo.png",
        content_type="image/png",
        size=len(content),
        raw=content,
    )


class FakeProvider:
    """
    Test double implementing the IntakeProvider interface. Tests configure
    `list_result`/`messages_by_id` (or swap `get_attachment_bytes`
    entirely) instead of mocking Microsoft Graph/Gmail API calls -
    exercising the pipeline exactly the way both real providers do,
    without caring which one "this" stands in for.
    """

    def __init__(self):
        self.messages_by_id = {}
        self.list_result = IntakeListResult(messages=[], truncated=False, next_cursor=None)

    def get_attachment_bytes(self, account, message, attachment):
        return attachment.raw

    def get_message(self, account, message_id):
        return self.messages_by_id[message_id]

    def list_new_messages(self, account, since, max_messages):
        return self.list_result


class IntakeTestCase(TestCase):
    """Base class: sets up a company, an open recruitment, an Outlook
    mailbox, and patches recruitment.intake.get_provider with a
    FakeProvider so no test in this file touches real provider code."""

    def setUp(self):
        cache.clear()
        self.company = Company.objects.create(
            company="Test Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        self.department = Department.objects.create(department="Engineering")
        self.job_position = JobPosition.objects.create(
            job_position="Backend Engineer", department_id=self.department
        )
        self.recruitment = Recruitment.objects.create(
            title="Backend Engineer",
            closed=False,
            is_active=True,
            company_id=self.company,
            job_position_id=self.job_position,
        )
        self.recruitment.open_positions.add(self.job_position)

        self.api = AzureApi.objects.create(
            outlook_client_id="x",
            outlook_client_secret="x",
            outlook_tenant_id="x",
            outlook_email="intake@example.com",
            outlook_display_name="Intake",
            outlook_redirect_uri="https://example.com/cb",
            company=self.company,
            token={"access_token": "fake", "expires_in": 3600},
        )
        self.mailbox = CandidateIntakeMailbox.objects.create(
            provider="outlook", azure_api=self.api, company_id=self.company
        )

        self.fake_provider = FakeProvider()
        self.get_provider_patcher = patch(
            "recruitment.intake.get_provider", return_value=self.fake_provider
        )
        self.get_provider_patcher.start()
        self.addCleanup(self.get_provider_patcher.stop)

        # Normally seeded by a data migration - create defensively so this
        # suite doesn't depend on migration history.
        IntakePollLock.objects.get_or_create(pk=1)

    def open_recruitments_payload(self):
        return intake._open_recruitment_options()

    def good_result(self, email="jane@example.com"):
        return {
            "name": "Jane Doe",
            "email": email,
            "mobile": "123",
            "matched_recruitment_id": self.recruitment.id,
            "confidence": 0.9,
        }


class NoResumeTests(IntakeTestCase):
    def test_no_attachments_logs_no_resume(self):
        message = make_message(attachments=[])
        status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "no_resume")
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertEqual(log.status, "no_resume")
        self.assertFalse(log.resume)

    def test_non_pdf_attachment_logs_invalid_format(self):
        message = make_message(attachments=[non_pdf_attachment()])
        status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "invalid_resume_format")
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertFalse(log.resume)


class AttachmentSizeCapTests(IntakeTestCase):
    def test_oversized_attachment_rejected_without_parsing(self):
        big_attachment = pdf_attachment(
            name="huge.pdf", content=b"x", att_id="att-big"
        )
        big_attachment.size = settings.INTAKE_MAX_ATTACHMENT_BYTES + 1
        message = make_message(attachments=[big_attachment])
        self.fake_provider.get_attachment_bytes = MagicMock(
            side_effect=intake.AttachmentTooLargeError("too big")
        )
        with patch("recruitment.intake.extract_resume_text") as mocked_extract:
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "attachment_too_large")
        mocked_extract.assert_not_called()
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertIn("too big", log.error_message)


class MagicByteCheckTests(IntakeTestCase):
    def test_spoofed_content_type_rejected_before_parsing(self):
        """contentType said application/pdf but the bytes aren't a PDF -
        must be caught before PyMuPDF ever sees them."""
        fake_pdf = pdf_attachment(content=b"not actually a pdf, just labeled as one")
        message = make_message(attachments=[fake_pdf])
        with patch("recruitment.intake.extract_resume_text") as mocked_extract:
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "invalid_resume_format")
        mocked_extract.assert_not_called()
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertIn("not a valid PDF", log.error_message)


class ParseFailedTests(IntakeTestCase):
    def test_empty_extracted_text_logs_parse_failed_with_reason(self):
        message = make_message(attachments=[pdf_attachment(content=BLANK_PDF)])
        with patch("recruitment.intake.extract_resume_text", return_value="   "):
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "parse_failed")
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertIn("No extractable text", log.error_message)
        # resume should still be saved even though parsing failed
        self.assertTrue(log.resume)

    def test_llm_unreachable_logs_parse_failed(self):
        message = make_message(attachments=[pdf_attachment()])
        with patch(
            "recruitment.intake.parse_and_match",
            side_effect=intake.IntakeParseError("connection refused"),
        ):
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "parse_failed")
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertIn("connection refused", log.error_message)
        self.assertTrue(log.resume)

    def test_parse_failed_is_retried_on_next_poll(self):
        """parse_failed/error are transient - a later call for the same
        message_id (force=False) must actually re-run, not skip."""
        message = make_message(attachments=[pdf_attachment()])
        with patch(
            "recruitment.intake.parse_and_match",
            side_effect=intake.IntakeParseError("timeout"),
        ):
            status1 = intake.process_message(self.mailbox, message)
        self.assertEqual(status1, "parse_failed")

        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status2 = intake.process_message(self.mailbox, message)
        self.assertEqual(status2, "created")
        # exactly one log row for this message_id, not two
        self.assertEqual(
            CandidateIntakeLog.objects.filter(message_id="msg-1").count(), 1
        )


class NoJobMatchTests(IntakeTestCase):
    def test_null_match_id_logs_no_job_match_with_reason(self):
        message = make_message(attachments=[pdf_attachment()])
        result = dict(self.good_result(), matched_recruitment_id=None, confidence=0.4)
        with patch("recruitment.intake.parse_and_match", return_value=result) as mocked:
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "no_job_match")
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertIn("Jane Doe", log.error_message)
        self.assertTrue(log.resume)
        # self-consistency retry: called twice since no fallback recruitment set
        self.assertEqual(mocked.call_count, 2)

    def test_matched_id_for_closed_recruitment_logs_no_job_match(self):
        self.recruitment.closed = True
        self.recruitment.save()
        message = make_message(attachments=[pdf_attachment()])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "no_job_match")
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertIn("isn't open/active", log.error_message)

    def test_self_consistency_retry_succeeds_on_second_call(self):
        message = make_message(attachments=[pdf_attachment()])
        null_result = dict(self.good_result(), matched_recruitment_id=None, confidence=0.3)
        with patch(
            "recruitment.intake.parse_and_match",
            side_effect=[null_result, self.good_result()],
        ) as mocked:
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "created")
        self.assertEqual(mocked.call_count, 2)

    def test_self_consistency_both_calls_null(self):
        message = make_message(attachments=[pdf_attachment()])
        null_result = dict(self.good_result(), matched_recruitment_id=None, confidence=0.3)
        with patch(
            "recruitment.intake.parse_and_match", side_effect=[null_result, null_result]
        ) as mocked:
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "no_job_match")
        self.assertEqual(mocked.call_count, 2)

    def test_fallback_recruitment_skips_retry(self):
        """When a fallback recruitment is configured, a null match should
        use it directly without spending a second LLM call."""
        fallback_position = JobPosition.objects.create(
            job_position="General Hire", department_id=self.department
        )
        fallback = Recruitment.objects.create(
            title="General",
            closed=False,
            is_active=True,
            company_id=self.company,
            job_position_id=fallback_position,
        )
        fallback.open_positions.add(fallback_position)
        self.mailbox.fallback_recruitment_id = fallback
        self.mailbox.save()
        message = make_message(attachments=[pdf_attachment()])
        null_result = dict(self.good_result(), matched_recruitment_id=None, confidence=0.3)
        with patch(
            "recruitment.intake.parse_and_match", return_value=null_result
        ) as mocked:
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "created")
        self.assertEqual(mocked.call_count, 1)
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertEqual(log.matched_recruitment_id, fallback.id)


class DuplicateTests(IntakeTestCase):
    def test_duplicate_via_validation_error(self):
        """Second application with the same email to the same recruitment
        must be logged as 'duplicate', not fall through to 'error'."""
        msg1 = make_message(msg_id="msg-1", attachments=[pdf_attachment(att_id="a1")])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status1 = intake.process_message(self.mailbox, msg1)
        self.assertEqual(status1, "created")
        self.assertEqual(Candidate.objects.filter(email="jane@example.com").count(), 1)

        msg2 = make_message(msg_id="msg-2", attachments=[pdf_attachment(att_id="a2")])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status2 = intake.process_message(self.mailbox, msg2)
        self.assertEqual(status2, "duplicate")
        # still only one candidate - the duplicate wasn't created
        self.assertEqual(Candidate.objects.filter(email="jane@example.com").count(), 1)

    def test_duplicate_via_integrity_error(self):
        """Simulates a race where full_clean() doesn't catch the collision
        but the DB unique constraint does."""
        Candidate.objects.create(
            name="Existing",
            email="jane@example.com",
            recruitment_id=self.recruitment,
            job_position_id=self.job_position,
        )
        msg = make_message(msg_id="msg-3", attachments=[pdf_attachment(att_id="a3")])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()), \
             patch("recruitment.models.Candidate.full_clean", return_value=None):
            status = intake.process_message(self.mailbox, msg)
        self.assertEqual(status, "duplicate")


class CreatedTests(IntakeTestCase):
    def test_created_non_event_based_missing_job_position_id_falls_back(self):
        """Real bug hit during manual testing: recruitment.job_position_id
        unset but open_positions has an entry - must still succeed."""
        self.recruitment.job_position_id = None
        self.recruitment.save()
        message = make_message(attachments=[pdf_attachment()])
        result = dict(self.good_result(email="ajay@example.com"), name="Ajay Kumar", mobile="999")
        with patch("recruitment.intake.parse_and_match", return_value=result):
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "created")
        candidate = Candidate.objects.get(email="ajay@example.com")
        self.assertEqual(candidate.job_position_id, self.job_position)
        self.assertEqual(candidate.platform.title, "Email Intake")
        self.assertTrue(candidate.stage_id)
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertEqual(log.candidate, candidate)
        self.assertTrue(log.resume)

    def test_created_event_based_recruitment(self):
        second_position = JobPosition.objects.create(
            job_position="Frontend Engineer", department_id=self.department
        )
        event_recruitment = Recruitment.objects.create(
            title="Multi Hire Event",
            closed=False,
            is_active=True,
            is_event_based=True,
            company_id=self.company,
        )
        event_recruitment.open_positions.add(self.job_position, second_position)

        message = make_message(attachments=[pdf_attachment()])
        result = dict(
            self.good_result(email="event@example.com"),
            name="Event Candidate",
            mobile="111",
            matched_recruitment_id=event_recruitment.id,
        )
        with patch("recruitment.intake.parse_and_match", return_value=result):
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "created")
        candidate = Candidate.objects.get(email="event@example.com")
        self.assertIn(candidate.job_position_id, [self.job_position, second_position])

    def test_no_valid_job_position_logs_no_job_match(self):
        """Recruitment with no job_position_id and no open_positions at
        all - creation should fail gracefully as no_job_match, not crash."""
        bare_recruitment = Recruitment.objects.create(
            title="Bare Recruitment", closed=False, is_active=True, company_id=self.company,
        )
        message = make_message(attachments=[pdf_attachment()])
        result = dict(
            self.good_result(email="nobody@example.com"),
            name="Nobody",
            mobile="000",
            matched_recruitment_id=bare_recruitment.id,
        )
        with patch("recruitment.intake.parse_and_match", return_value=result):
            status = intake.process_message(self.mailbox, message)
        self.assertEqual(status, "no_job_match")
        self.assertFalse(Candidate.objects.filter(email="nobody@example.com").exists())


class DailyCandidateCapTests(IntakeTestCase):
    def test_cap_exceeded_blocks_further_creation_and_notifies_once(self):
        self.mailbox.daily_candidate_cap = 1
        self.mailbox.save()
        msg1 = make_message(msg_id="m1", attachments=[pdf_attachment(att_id="a1")])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status1 = intake.process_message(self.mailbox, msg1)
        self.assertEqual(status1, "created")

        msg2 = make_message(msg_id="m2", attachments=[pdf_attachment(att_id="a2")])
        result2 = self.good_result(email="jane2@example.com")
        with patch("recruitment.intake.parse_and_match", return_value=result2) as mocked, \
             patch("recruitment.intake.notify") as mocked_notify:
            status2 = intake.process_message(self.mailbox, msg2)
        self.assertEqual(status2, "daily_cap_exceeded")
        mocked.assert_not_called()  # cap check happens before any LLM/attachment work
        mocked_notify.send.assert_called_once()

        msg3 = make_message(msg_id="m3", attachments=[pdf_attachment(att_id="a3")])
        result3 = self.good_result(email="jane3@example.com")
        with patch("recruitment.intake.parse_and_match", return_value=result3), \
             patch("recruitment.intake.notify") as mocked_notify2:
            status3 = intake.process_message(self.mailbox, msg3)
        self.assertEqual(status3, "daily_cap_exceeded")
        mocked_notify2.send.assert_not_called()  # already notified today

    def test_daily_cap_exceeded_is_retried_once_cap_clears(self):
        """daily_cap_exceeded must be treated as transient, not permanently
        finalized like no_job_match/duplicate."""
        self.mailbox.daily_candidate_cap = 0
        self.mailbox.save()
        message = make_message(attachments=[pdf_attachment()])
        status1 = intake.process_message(self.mailbox, message)
        self.assertEqual(status1, "daily_cap_exceeded")

        self.mailbox.daily_candidate_cap = None  # falls back to site default
        self.mailbox.save()
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status2 = intake.process_message(self.mailbox, message)
        self.assertEqual(status2, "created")
        self.assertEqual(
            CandidateIntakeLog.objects.filter(message_id="msg-1").count(), 1
        )


class PollGuardTests(IntakeTestCase):
    def test_concurrent_poll_is_skipped(self):
        IntakePollLock.objects.filter(pk=1).update(
            locked_at=timezone.now(), locked_by="other-worker"
        )
        with patch("recruitment.intake.poll_all_mailboxes") as mocked:
            ran = intake.run_guarded_poll()
        self.assertFalse(ran)
        mocked.assert_not_called()

    def test_poll_runs_and_releases_lock_when_not_running(self):
        with patch("recruitment.intake.poll_all_mailboxes") as mocked:
            ran = intake.run_guarded_poll()
        self.assertTrue(ran)
        mocked.assert_called_once()
        lock = IntakePollLock.objects.get(pk=1)
        self.assertIsNone(lock.locked_at)
        self.assertEqual(lock.locked_by, "")
        self.assertIsNotNone(cache.get(intake.POLL_LAST_RESULT_CACHE_KEY))

    def test_poll_lock_released_even_if_poll_raises(self):
        with patch(
            "recruitment.intake.poll_all_mailboxes", side_effect=RuntimeError("boom")
        ):
            ran = intake.run_guarded_poll()
        self.assertTrue(ran)
        lock = IntakePollLock.objects.get(pk=1)
        self.assertIsNone(lock.locked_at)
        last_result = cache.get(intake.POLL_LAST_RESULT_CACHE_KEY)
        self.assertIn("boom", last_result["error"])

    def test_stale_lock_is_reclaimed(self):
        """A lock abandoned by a crashed worker must not wedge intake
        forever - it's reclaimed once older than
        INTAKE_POLL_LOCK_STALE_SECONDS."""
        stale_time = timezone.now() - timedelta(
            seconds=settings.INTAKE_POLL_LOCK_STALE_SECONDS + 60
        )
        IntakePollLock.objects.filter(pk=1).update(
            locked_at=stale_time, locked_by="dead-worker"
        )
        with patch("recruitment.intake.poll_all_mailboxes") as mocked:
            ran = intake.run_guarded_poll()
        self.assertTrue(ran)
        mocked.assert_called_once()


class PaginationTruncationTests(IntakeTestCase):
    def test_truncated_result_advances_cursor_to_last_processed_not_now(self):
        msg1 = make_message(
            msg_id="m1",
            attachments=[pdf_attachment(att_id="a1")],
            received=DEFAULT_RECEIVED_AT,
        )
        self.fake_provider.list_result = IntakeListResult(
            messages=[msg1], truncated=True, next_cursor=msg1.received_at
        )
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            intake.process_mailbox(self.mailbox)
        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.last_polled_at, msg1.received_at)
        # confirms it did NOT jump to "now" - a burst beyond the ceiling
        # must be picked up on a later poll, not silently skipped.
        self.assertLess(
            self.mailbox.last_polled_at, timezone.now() - timedelta(days=1)
        )

    def test_untruncated_result_advances_cursor_to_now(self):
        msg1 = make_message(
            msg_id="m1",
            attachments=[pdf_attachment(att_id="a1")],
            received=DEFAULT_RECEIVED_AT,
        )
        self.fake_provider.list_result = IntakeListResult(
            messages=[msg1], truncated=False, next_cursor=msg1.received_at
        )
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            intake.process_mailbox(self.mailbox)
        self.mailbox.refresh_from_db()
        self.assertGreater(
            self.mailbox.last_polled_at, timezone.now() - timedelta(minutes=1)
        )


class MailboxErrorSurfacingTests(IntakeTestCase):
    def test_listing_failure_sets_last_error_and_notifies_once(self):
        self.fake_provider.list_new_messages = MagicMock(
            side_effect=RuntimeError("token expired")
        )
        with patch("recruitment.intake.notify") as mocked_notify:
            intake.poll_all_mailboxes()
        self.mailbox.refresh_from_db()
        self.assertIn("token expired", self.mailbox.last_error)
        mocked_notify.send.assert_called_once()

        # a second consecutive failure of the same kind must NOT re-notify
        with patch("recruitment.intake.notify") as mocked_notify2:
            intake.poll_all_mailboxes()
        mocked_notify2.send.assert_not_called()

    def test_successful_poll_clears_previous_error(self):
        self.mailbox.last_error = "some old error"
        self.mailbox.save()
        self.fake_provider.list_result = IntakeListResult(
            messages=[], truncated=False, next_cursor=None
        )
        intake.poll_all_mailboxes()
        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.last_error, "")


class RetrySelectedTests(IntakeTestCase):
    def test_retry_reprocesses_regardless_of_prior_status(self):
        # First pass: finalize as no_job_match (a non-transient status).
        message = make_message(attachments=[pdf_attachment()])
        null_result = dict(self.good_result(), matched_recruitment_id=None, confidence=0.3)
        with patch("recruitment.intake.parse_and_match", return_value=null_result):
            intake.process_message(self.mailbox, message)
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertEqual(log.status, "no_job_match")

        # A plain re-poll (force=False) must NOT reprocess a finalized
        # no_job_match - confirms retry_message is doing something the
        # automatic path deliberately doesn't.
        with patch("recruitment.intake.parse_and_match") as mocked_auto:
            intake.process_message(self.mailbox, message)
        mocked_auto.assert_not_called()

        # Explicit retry: force reprocessing, this time with a match.
        self.fake_provider.messages_by_id["msg-1"] = message
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status = intake.retry_message(log)
        self.assertEqual(status, "created")
        log.refresh_from_db()
        self.assertEqual(log.status, "created")
        self.assertIsNotNone(log.candidate)
        # still exactly one log row for this message_id
        self.assertEqual(
            CandidateIntakeLog.objects.filter(message_id="msg-1").count(), 1
        )


class ResumePersistenceTests(IntakeTestCase):
    def test_resume_saved_for_parse_failed(self):
        message = make_message(attachments=[pdf_attachment()])
        with patch(
            "recruitment.intake.parse_and_match",
            side_effect=intake.IntakeParseError("x"),
        ):
            intake.process_message(self.mailbox, message)
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertTrue(log.resume)

    def test_resume_not_saved_when_no_attachment(self):
        message = make_message(attachments=[])
        intake.process_message(self.mailbox, message)
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertFalse(log.resume)

    def test_resume_saved_on_created(self):
        message = make_message(attachments=[pdf_attachment()])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            intake.process_message(self.mailbox, message)
        log = CandidateIntakeLog.objects.get(message_id="msg-1")
        self.assertTrue(log.resume)
        # both the log and the candidate should have their own resume copy
        self.assertTrue(log.candidate.resume)


class GmailMailboxSmokeTests(IntakeTestCase):
    """
    Confirms a Gmail-provider mailbox flows through the exact same
    provider-agnostic pipeline as Outlook - only get_provider(mailbox.provider)
    differs, everything from process_message() onward is identical code.
    """

    def setUp(self):
        super().setUp()
        self.google_api = GoogleApi.objects.create(
            google_client_id="x",
            google_client_secret="x",
            google_email="intake@gmail.example.com",
            google_display_name="Gmail Intake",
            google_redirect_uri="https://example.com/cb",
            company=self.company,
            token={"token": "fake", "refresh_token": "fake"},
        )
        self.gmail_mailbox = CandidateIntakeMailbox.objects.create(
            provider="gmail", google_api=self.google_api, company_id=self.company
        )

    def test_created_via_gmail_mailbox(self):
        message = make_message(msg_id="gmail-msg-1", attachments=[pdf_attachment()])
        with patch("recruitment.intake.parse_and_match", return_value=self.good_result()):
            status = intake.process_message(self.gmail_mailbox, message)
        self.assertEqual(status, "created")
        log = CandidateIntakeLog.objects.get(message_id="gmail-msg-1")
        self.assertEqual(log.mailbox, self.gmail_mailbox)
        self.assertIsNotNone(log.candidate)
