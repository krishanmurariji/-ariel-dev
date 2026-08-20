"""
recruitment/intake_providers/gmail.py
"""

import base64
from datetime import datetime, timezone

from django.conf import settings

from gmail_auth import views as gmail_views
from recruitment.intake_providers.base import (
    AttachmentTooLargeError,
    IntakeAttachment,
    IntakeListResult,
    IntakeMessage,
    IntakeProvider,
)


def _header(headers: list, name: str) -> str:
    name = name.lower()
    for header in headers:
        if header.get("name", "").lower() == name:
            return header.get("value", "")
    return ""


def _urlsafe_b64decode(data: str) -> bytes:
    """
    Gmail uses URL-safe base64 without padding - decode with
    base64.urlsafe_b64decode(), NOT base64.b64decode() (Microsoft Graph's
    encoding), an easy copy-paste trap. Pad out to a multiple of 4 first,
    since Gmail's API omits the trailing "=" padding.
    """
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk_parts(payload: dict, body_chunks: list, attachments: list):
    """
    Recursively walks Gmail's MIME-tree `payload`, collecting text/plain
    body chunks and PDF attachment metadata (without fetching attachment
    bytes - just size/filename/attachmentId, which Gmail returns inline
    even for large parts).
    """
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    filename = payload.get("filename", "")

    if filename and mime_type == "application/pdf":
        attachment_id = body.get("attachmentId")
        attachments.append(
            IntakeAttachment(
                id=attachment_id or filename,
                filename=filename,
                content_type=mime_type,
                size=body.get("size", 0),
                raw={"attachmentId": attachment_id, "inline_data": body.get("data")},
            )
        )
    elif mime_type == "text/plain" and body.get("data"):
        body_chunks.append(
            _urlsafe_b64decode(body["data"]).decode("utf-8", errors="replace")
        )

    for part in payload.get("parts", []) or []:
        _walk_parts(part, body_chunks, attachments)


def _to_intake_message(raw: dict) -> IntakeMessage:
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])
    body_chunks = []
    attachments = []
    _walk_parts(payload, body_chunks, attachments)

    internal_date_ms = int(raw.get("internalDate", "0"))
    received_at = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)

    return IntakeMessage(
        id=raw["id"],
        subject=_header(headers, "Subject"),
        from_email=_header(headers, "From"),
        received_at=received_at,
        body_text="\n".join(body_chunks),
        attachments=attachments,
    )


class GmailIntakeProvider(IntakeProvider):
    def list_new_messages(self, account, since, max_messages: int) -> IntakeListResult:
        messages = []
        page_token = None
        truncated = False
        while True:
            stubs, page_token = gmail_views.list_inbox_messages(
                account, since=since, page_token=page_token, max_results=100
            )
            for stub in stubs:
                if len(messages) >= max_messages:
                    truncated = True
                    break
                full = gmail_views.get_message(account, stub["id"])
                messages.append(_to_intake_message(full))
            if len(messages) >= max_messages and page_token:
                truncated = True
                break
            if not page_token:
                break

        messages.sort(key=lambda m: m.received_at)
        next_cursor = messages[-1].received_at if messages else since
        return IntakeListResult(
            messages=messages, truncated=truncated, next_cursor=next_cursor
        )

    def get_message(self, account, message_id: str) -> IntakeMessage:
        raw = gmail_views.get_message(account, message_id)
        return _to_intake_message(raw)

    def get_attachment_bytes(self, account, message, attachment: IntakeAttachment) -> bytes:
        if attachment.size and attachment.size > settings.INTAKE_MAX_ATTACHMENT_BYTES:
            raise AttachmentTooLargeError(
                f"{attachment.filename} is {attachment.size} bytes, "
                f"over the {settings.INTAKE_MAX_ATTACHMENT_BYTES}-byte limit."
            )

        inline_data = attachment.raw.get("inline_data")
        if inline_data:
            # Small attachments Gmail already inlined into the message
            # payload - no separate fetch needed.
            data = inline_data
        else:
            attachment_id = attachment.raw.get("attachmentId")
            raw_attachment = gmail_views.get_attachment_bytes_raw(
                account, message.id, attachment_id
            )
            if raw_attachment.get("size", 0) > settings.INTAKE_MAX_ATTACHMENT_BYTES:
                raise AttachmentTooLargeError(
                    f"{attachment.filename} is {raw_attachment.get('size')} bytes, "
                    f"over the {settings.INTAKE_MAX_ATTACHMENT_BYTES}-byte limit."
                )
            data = raw_attachment.get("data", "")

        decoded = _urlsafe_b64decode(data)
        if len(decoded) > settings.INTAKE_MAX_ATTACHMENT_BYTES:
            raise AttachmentTooLargeError(
                f"{attachment.filename} decoded to {len(decoded)} bytes, "
                f"over the {settings.INTAKE_MAX_ATTACHMENT_BYTES}-byte limit."
            )
        return decoded
