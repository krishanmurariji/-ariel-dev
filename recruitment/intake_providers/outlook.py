"""
recruitment/intake_providers/outlook.py
"""

import base64
from datetime import datetime

from django.conf import settings

from outlook_auth import views as outlook_views
from recruitment.intake_providers.base import (
    AttachmentTooLargeError,
    IntakeAttachment,
    IntakeListResult,
    IntakeMessage,
    IntakeProvider,
)


def _to_intake_message(message: dict) -> IntakeMessage:
    attachments = [
        IntakeAttachment(
            id=attachment.get("id") or attachment.get("name") or "",
            filename=attachment.get("name") or "attachment",
            content_type=attachment.get("contentType") or "",
            size=attachment.get("size") or 0,
            raw=attachment,
        )
        for attachment in message.get("attachments", [])
    ]
    received_at = datetime.fromisoformat(
        message["receivedDateTime"].replace("Z", "+00:00")
    )
    return IntakeMessage(
        id=message["id"],
        subject=message.get("subject", ""),
        from_email=message.get("from", {}).get("emailAddress", {}).get("address", ""),
        received_at=received_at,
        body_text=message.get("body", {}).get("content", ""),
        attachments=attachments,
    )


class OutlookIntakeProvider(IntakeProvider):
    def list_new_messages(self, account, since, max_messages: int) -> IntakeListResult:
        messages = []
        next_link = None
        truncated = False
        while True:
            page, next_link = outlook_views.list_inbox_messages(
                account, since=since, next_link=next_link
            )
            for raw in page:
                if len(messages) >= max_messages:
                    truncated = True
                    break
                messages.append(_to_intake_message(raw))
            if len(messages) >= max_messages and next_link:
                truncated = True
                break
            if not next_link:
                break

        next_cursor = messages[-1].received_at if messages else since
        return IntakeListResult(
            messages=messages, truncated=truncated, next_cursor=next_cursor
        )

    def get_message(self, account, message_id: str) -> IntakeMessage:
        raw = outlook_views.get_message(account, message_id)
        return _to_intake_message(raw)

    def get_attachment_bytes(self, account, message, attachment: IntakeAttachment) -> bytes:
        if attachment.size and attachment.size > settings.INTAKE_MAX_ATTACHMENT_BYTES:
            raise AttachmentTooLargeError(
                f"{attachment.filename} is {attachment.size} bytes, "
                f"over the {settings.INTAKE_MAX_ATTACHMENT_BYTES}-byte limit."
            )
        content_bytes = attachment.raw.get("contentBytes")
        decoded = base64.b64decode(content_bytes) if content_bytes else b""
        if len(decoded) > settings.INTAKE_MAX_ATTACHMENT_BYTES:
            raise AttachmentTooLargeError(
                f"{attachment.filename} decoded to {len(decoded)} bytes, "
                f"over the {settings.INTAKE_MAX_ATTACHMENT_BYTES}-byte limit."
            )
        return decoded
