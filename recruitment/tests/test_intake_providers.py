"""
recruitment/tests/test_intake_providers.py

Unit tests for the Outlook/Gmail adapters in recruitment/intake_providers/:
normalizing each provider's raw API shape into IntakeMessage/IntakeAttachment,
and the pagination/size-cap behavior at the adapter boundary. No real
Microsoft Graph or Gmail API calls - outlook_auth.views/gmail_auth.views
functions are mocked directly.
"""

import base64
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase, override_settings

from recruitment.intake_providers.base import AttachmentTooLargeError
from recruitment.intake_providers.gmail import GmailIntakeProvider
from recruitment.intake_providers.outlook import OutlookIntakeProvider


def graph_message(msg_id="g-1", subject="Application", attachments=None):
    return {
        "id": msg_id,
        "subject": subject,
        "from": {"emailAddress": {"address": "candidate@example.com"}},
        "receivedDateTime": "2026-07-23T10:00:00Z",
        "body": {"content": "Please find my resume attached."},
        "attachments": attachments or [],
    }


def graph_pdf_attachment(content=b"%PDF-1.4 fake"):
    encoded = base64.b64encode(content).decode()
    return {
        "id": "att-1",
        "name": "resume.pdf",
        "contentType": "application/pdf",
        "size": len(content),
        "contentBytes": encoded,
    }


class OutlookProviderTests(TestCase):
    def setUp(self):
        self.provider = OutlookIntakeProvider()
        self.account = object()  # opaque to the provider until it calls outlook_auth.views

    @patch("recruitment.intake_providers.outlook.outlook_views.list_inbox_messages")
    def test_list_new_messages_normalizes_shape(self, mocked_list):
        mocked_list.return_value = ([graph_message(attachments=[graph_pdf_attachment()])], None)
        result = self.provider.list_new_messages(self.account, since=None, max_messages=50)
        self.assertFalse(result.truncated)
        self.assertEqual(len(result.messages), 1)
        message = result.messages[0]
        self.assertEqual(message.id, "g-1")
        self.assertEqual(message.from_email, "candidate@example.com")
        self.assertEqual(message.received_at, datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(message.attachments[0].content_type, "application/pdf")

    @patch("recruitment.intake_providers.outlook.outlook_views.list_inbox_messages")
    def test_pagination_follows_next_link_up_to_ceiling(self, mocked_list):
        page1 = ([graph_message(msg_id=f"g-{i}") for i in range(3)], "next-link-token")
        page2 = ([graph_message(msg_id=f"g-{i}") for i in range(3, 6)], None)
        mocked_list.side_effect = [page1, page2]
        result = self.provider.list_new_messages(self.account, since=None, max_messages=50)
        self.assertEqual(len(result.messages), 6)
        self.assertFalse(result.truncated)
        self.assertEqual(mocked_list.call_count, 2)

    @patch("recruitment.intake_providers.outlook.outlook_views.list_inbox_messages")
    def test_pagination_stops_and_marks_truncated_at_ceiling(self, mocked_list):
        page1 = ([graph_message(msg_id=f"g-{i}") for i in range(3)], "next-link-token")
        mocked_list.return_value = page1
        result = self.provider.list_new_messages(self.account, since=None, max_messages=2)
        self.assertEqual(len(result.messages), 2)
        self.assertTrue(result.truncated)

    @override_settings(INTAKE_MAX_ATTACHMENT_BYTES=10)
    def test_get_attachment_bytes_rejects_oversized_by_metadata(self):
        message = graph_message(attachments=[graph_pdf_attachment(content=b"x" * 100)])
        from recruitment.intake_providers.outlook import _to_intake_message

        intake_message = _to_intake_message(message)
        attachment = intake_message.attachments[0]
        with self.assertRaises(AttachmentTooLargeError):
            self.provider.get_attachment_bytes(self.account, intake_message, attachment)

    def test_get_attachment_bytes_decodes_standard_base64(self):
        content = b"%PDF-1.4 real content here"
        message = graph_message(attachments=[graph_pdf_attachment(content=content)])
        from recruitment.intake_providers.outlook import _to_intake_message

        intake_message = _to_intake_message(message)
        attachment = intake_message.attachments[0]
        decoded = self.provider.get_attachment_bytes(self.account, intake_message, attachment)
        self.assertEqual(decoded, content)


def gmail_message(msg_id="gm-1", subject="Application", parts=None, internal_date_ms=1784880000000):
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": "candidate@example.com"},
    ]
    return {
        "id": msg_id,
        "internalDate": str(internal_date_ms),
        "payload": {
            "headers": headers,
            "mimeType": "multipart/mixed",
            "parts": parts or [],
        },
    }


def gmail_text_part(text="Please find my resume attached."):
    data = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    return {"mimeType": "text/plain", "body": {"data": data, "size": len(text)}}


def gmail_pdf_part(content=b"%PDF-1.4 fake", inline=True, filename="resume.pdf"):
    body = {"size": len(content)}
    if inline:
        body["data"] = base64.urlsafe_b64encode(content).decode().rstrip("=")
    else:
        body["attachmentId"] = "gmail-att-1"
    return {"mimeType": "application/pdf", "filename": filename, "body": body}


class GmailProviderTests(TestCase):
    def setUp(self):
        self.provider = GmailIntakeProvider()
        self.account = object()

    def test_normalizes_headers_and_body_and_attachment_metadata(self):
        from recruitment.intake_providers.gmail import _to_intake_message

        raw = gmail_message(parts=[gmail_text_part(), gmail_pdf_part()])
        message = _to_intake_message(raw)
        self.assertEqual(message.id, "gm-1")
        self.assertEqual(message.from_email, "candidate@example.com")
        self.assertEqual(message.subject, "Application")
        self.assertIn("Please find my resume attached.", message.body_text)
        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(message.attachments[0].content_type, "application/pdf")
        self.assertEqual(message.attachments[0].filename, "resume.pdf")

    def test_attachment_size_available_without_fetching_body(self):
        """Gmail exposes part.body.size in the message payload itself -
        the size cap must be checkable before ever calling
        attachments().get() for a non-inlined attachment."""
        from recruitment.intake_providers.gmail import _to_intake_message

        big_content_size = 999999
        part = gmail_pdf_part(inline=False)
        part["body"]["size"] = big_content_size
        raw = gmail_message(parts=[part])
        message = _to_intake_message(raw)
        self.assertEqual(message.attachments[0].size, big_content_size)

    @override_settings(INTAKE_MAX_ATTACHMENT_BYTES=10)
    def test_get_attachment_bytes_rejects_oversized_inline_attachment(self):
        from recruitment.intake_providers.gmail import _to_intake_message

        raw = gmail_message(parts=[gmail_pdf_part(content=b"x" * 100)])
        message = _to_intake_message(raw)
        attachment = message.attachments[0]
        with self.assertRaises(AttachmentTooLargeError):
            self.provider.get_attachment_bytes(self.account, message, attachment)

    @override_settings(INTAKE_MAX_ATTACHMENT_BYTES=10)
    @patch("recruitment.intake_providers.gmail.gmail_views.get_attachment_bytes_raw")
    def test_get_attachment_bytes_rejects_oversized_separate_fetch_by_metadata_before_fetch(
        self, mocked_fetch
    ):
        """Size cap check on the message-payload metadata must happen
        BEFORE the separate attachments().get() network call for
        non-inlined attachments."""
        from recruitment.intake_providers.gmail import _to_intake_message

        part = gmail_pdf_part(inline=False)
        part["body"]["size"] = 999999
        raw = gmail_message(parts=[part])
        message = _to_intake_message(raw)
        attachment = message.attachments[0]
        with self.assertRaises(AttachmentTooLargeError):
            self.provider.get_attachment_bytes(self.account, message, attachment)
        mocked_fetch.assert_not_called()

    def test_urlsafe_base64_decode_not_standard_base64(self):
        """Regression test: Gmail uses URL-safe base64 (- and _ instead of
        + and /), unlike Microsoft Graph's standard base64. Using
        base64.b64decode() here would silently corrupt or error on any
        content containing those characters - this is the exact
        copy-paste trap the implementation must avoid."""
        from recruitment.intake_providers.gmail import _urlsafe_b64decode

        # Bytes chosen so the base64 encoding is guaranteed to contain
        # url-safe-only characters ('-' and/or '_') that standard base64
        # would render as '+'/'/' instead.
        content = bytes(range(256))
        urlsafe_encoded = base64.urlsafe_b64encode(content).decode().rstrip("=")
        self.assertTrue(
            "-" in urlsafe_encoded or "_" in urlsafe_encoded,
            "test fixture doesn't actually exercise the url-safe alphabet",
        )
        # Using standard base64.b64decode() on this string must fail or
        # produce different bytes - proving the two alphabets really do
        # diverge for this input, not just in theory.
        with self.assertRaises(Exception):
            base64.b64decode(urlsafe_encoded, validate=True)
        decoded = _urlsafe_b64decode(urlsafe_encoded)
        self.assertEqual(decoded, content)

    @patch("recruitment.intake_providers.gmail.gmail_views.get_attachment_bytes_raw")
    def test_get_attachment_bytes_fetches_and_decodes_non_inline_attachment(self, mocked_fetch):
        from recruitment.intake_providers.gmail import _to_intake_message

        content = b"%PDF-1.4 real content here"
        encoded = base64.urlsafe_b64encode(content).decode().rstrip("=")
        mocked_fetch.return_value = {"size": len(content), "data": encoded}

        part = gmail_pdf_part(inline=False)
        part["body"]["size"] = len(content)
        raw = gmail_message(parts=[part])
        message = _to_intake_message(raw)
        attachment = message.attachments[0]
        decoded = self.provider.get_attachment_bytes(self.account, message, attachment)
        self.assertEqual(decoded, content)
        mocked_fetch.assert_called_once()

    @patch("recruitment.intake_providers.gmail.gmail_views.get_message")
    @patch("recruitment.intake_providers.gmail.gmail_views.list_inbox_messages")
    def test_list_new_messages_paginates_and_sorts_by_received_at(
        self, mocked_list, mocked_get
    ):
        mocked_list.side_effect = [
            ([{"id": "gm-2"}], "token-1"),
            ([{"id": "gm-1"}], None),
        ]
        mocked_get.side_effect = [
            gmail_message(msg_id="gm-2", internal_date_ms=2000000),
            gmail_message(msg_id="gm-1", internal_date_ms=1000000),
        ]
        result = self.provider.list_new_messages(self.account, since=None, max_messages=50)
        self.assertEqual(len(result.messages), 2)
        # sorted ascending by received_at, regardless of API return order
        self.assertEqual([m.id for m in result.messages], ["gm-1", "gm-2"])
        self.assertFalse(result.truncated)
