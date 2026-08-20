"""
recruitment/intake_providers/__init__.py

Single place where "which email provider does this mailbox use" is
resolved to actual code - recruitment/intake.py never branches on
provider directly, it just calls get_provider(mailbox.provider).
"""

from recruitment.intake_providers.base import (
    AttachmentTooLargeError,
    IntakeAttachment,
    IntakeListResult,
    IntakeMessage,
    IntakeProvider,
)
from recruitment.intake_providers.gmail import GmailIntakeProvider
from recruitment.intake_providers.outlook import OutlookIntakeProvider

_PROVIDERS = {
    "outlook": OutlookIntakeProvider(),
    "gmail": GmailIntakeProvider(),
}


def get_provider(name: str) -> IntakeProvider:
    return _PROVIDERS[name]


__all__ = [
    "AttachmentTooLargeError",
    "IntakeAttachment",
    "IntakeListResult",
    "IntakeMessage",
    "IntakeProvider",
    "get_provider",
]
