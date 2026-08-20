"""
recruitment/intake_providers/base.py

Normalizes the wildly different message/attachment shapes Microsoft Graph
and the Gmail API return into one internal representation, so
recruitment/intake.py's core pipeline (LLM parse/match, Candidate
creation, CandidateIntakeLog writing) never has to know which provider a
CandidateIntakeMailbox is connected to.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


class AttachmentTooLargeError(Exception):
    """Raised when an attachment exceeds settings.INTAKE_MAX_ATTACHMENT_BYTES
    - callers must NOT have downloaded/decoded the full attachment body
    before this is raised, only inspected size metadata."""


@dataclass
class IntakeAttachment:
    """
    One email attachment, normalized. `raw` keeps whatever provider-native
    reference is needed to later fetch the actual bytes (e.g. the Graph
    attachment dict itself, or a Gmail attachmentId string) - opaque to
    everything outside the provider's own adapter.
    """

    id: str
    filename: str
    content_type: str
    size: int
    raw: Any = None


@dataclass
class IntakeMessage:
    """One inbox email, normalized."""

    id: str
    subject: str
    from_email: str
    received_at: datetime
    body_text: str
    attachments: list = field(default_factory=list)


@dataclass
class IntakeListResult:
    """
    Result of listing new messages since a cursor.

    truncated=True means more messages existed beyond max_messages (or a
    time budget was hit) - the caller must NOT advance its polling cursor
    to "now" in that case, only to next_cursor, so the remainder is picked
    up on a later poll instead of being silently skipped.
    """

    messages: list
    truncated: bool
    next_cursor: Optional[datetime]


class IntakeProvider:
    """
    Interface every email-intake provider (Outlook, Gmail, ...) implements.
    This is the ONLY place provider-specific behavior should live - nothing
    in recruitment/intake.py should ever branch on provider type directly.
    """

    def list_new_messages(self, account, since, max_messages: int) -> IntakeListResult:
        """
        List new messages received after `since` (None means "all"),
        paginating internally up to `max_messages` total.
        """
        raise NotImplementedError

    def get_message(self, account, message_id: str) -> IntakeMessage:
        """
        Fetch a single message by id, for retry_message().
        """
        raise NotImplementedError

    def get_attachment_bytes(
        self, account, message: IntakeMessage, attachment: IntakeAttachment
    ) -> bytes:
        """
        Fetch and decode one attachment's raw bytes. Must check
        attachment.size against settings.INTAKE_MAX_ATTACHMENT_BYTES and
        raise AttachmentTooLargeError before any network fetch of the
        actual bytes where the provider's API allows that (i.e. before
        calling out for the body, not after).
        """
        raise NotImplementedError
