"""
base/email_log_safety.py

The mail log stores every outgoing message so HR can see what was sent. A
one-time code or a password-reset link is a live key to somebody's account,
so those messages must never be stored in readable form: anyone who can read
the log (or a database backup) could otherwise use them.

A message is treated as sensitive when the code that builds it says so
(``message.sensitive = True``), or - as a safety net for any sender we forgot -
when it looks like a one-time-code mail or contains a password-reset link.

Imports nothing from the project so backends and migrations can both use it.
"""

import re

HIDDEN_BODY = "[Hidden: this message contains a one-time code or a password reset link.]"

# Django's reset link: /reset/<uidb64>/<token>/
_RESET_LINK = re.compile(r"/reset/[A-Za-z0-9_\-]+/[A-Za-z0-9\-]+/?")
_SENSITIVE_SUBJECT = re.compile(
    r"\botp\b|one[- ]time|verification code|password reset|reset (?:your )?password",
    re.I,
)


def looks_sensitive(subject, body):
    return bool(
        _SENSITIVE_SUBJECT.search(str(subject or ""))
        or _RESET_LINK.search(str(body or ""))
    )


def is_sensitive(message):
    if getattr(message, "sensitive", False):
        return True
    return looks_sensitive(getattr(message, "subject", ""), getattr(message, "body", ""))


def body_for_log(message):
    """What to store in the mail log for this message's body."""
    return HIDDEN_BODY if is_sensitive(message) else message.body
