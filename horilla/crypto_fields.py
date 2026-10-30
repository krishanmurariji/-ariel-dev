"""
horilla/crypto_fields.py

Shared encrypted-at-rest model fields, used by every app that stores a
third-party credential (OAuth client secrets/tokens, API keys, bind
passwords, ...) so a database leak or an over-privileged DB/admin user
can't read them directly. See outlook_auth/crypto_fields.py for the
original of this pair - kept there unchanged (it's already migrated in
production) while every other app imports this shared copy instead.
"""

import base64
import hashlib
import json
import logging
import secrets

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

# What a field reads as when its stored value is encrypted but no key we hold opens it. It is not the
# ciphertext (that would be sent on as if it were the secret) and not an empty string (code that treats
# "no secret" as "nothing to check", such as the WhatsApp webhook signature check, would then switch
# its protection off): it is a non-empty value nobody can guess, so a signature made with it never
# matches and a login with it is refused. It differs for every process, so it cannot be learned.
UNDECRYPTABLE = "undecryptable-" + secrets.token_urlsafe(24)


def _legacy_key() -> str:
    """
    The single key every EncryptedTextField value has always implicitly
    been encrypted with: SHA256(SECRET_KEY), base64-encoded. Kept as its
    own function (rather than inlined) so a deployment that wants to
    start rotating keys can print this once to get the exact value to
    include as its fallback entry in FIELD_ENCRYPTION_KEYS - see that
    setting's docstring in horilla/settings/base.py.
    """
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


def _get_fernet() -> MultiFernet:
    # MultiFernet encrypts with the FIRST key in the list but can decrypt
    # with ANY key in it - that's what makes rotation possible: add a new
    # key to the front, let old ciphertext keep decrypting under the keys
    # after it, re-encrypt everything at your own pace, then drop the old
    # key once nothing depends on it any more. FIELD_ENCRYPTION_KEYS is
    # unset by default, which keeps current behavior unchanged (the one
    # key every row has always been encrypted with) until a deployment
    # opts into an explicit key list.
    keys = settings.FIELD_ENCRYPTION_KEYS or [_legacy_key()]
    return MultiFernet([Fernet(key) for key in keys])


def _looks_like_fernet_token(value) -> bool:
    """
    True for a Fernet token (urlsafe base64 of version byte 0x80, timestamp, IV, at least one
    cipher block and a 32-byte HMAC), whether or not any key we hold can open it.
    """
    try:
        raw = base64.urlsafe_b64decode(value.encode() + b"=" * (-len(value) % 4))
    except (ValueError, TypeError):
        return False
    return len(raw) >= 73 and raw[0] == 0x80


class EncryptedTextField(models.TextField):
    """
    Encrypts on write, decrypts on read. A value that is not a Fernet token at all is treated
    as a legacy plaintext row written before this field existed; a token that cannot be
    decrypted reads as the UNDECRYPTABLE placeholder (see from_db_value).
    """

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        return _get_fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            if _looks_like_fernet_token(value):
                # Ciphertext that no key we hold can open (e.g. SECRET_KEY or
                # FIELD_ENCRYPTION_KEYS changed). Handing it back would send
                # garbage to LDAP / OAuth / a device as if it were the real
                # secret. Give UNDECRYPTABLE instead - truthy, so checks that
                # key off "is a secret set" stay on, and never a valid
                # credential - and log loudly. The stored ciphertext is
                # untouched until someone saves a new value.
                logger.error(
                    "%s.%s: stored value is encrypted but cannot be decrypted with "
                    "the current keys (was SECRET_KEY / FIELD_ENCRYPTION_KEYS "
                    "changed?) - it reads as an unusable placeholder.",
                    getattr(getattr(self, "model", None), "__name__", "?"),
                    getattr(self, "name", "?"),
                )
                return UNDECRYPTABLE
            # Not a token at all: a legacy plaintext row written before this
            # field existed. Returned as is.
            logger.warning(
                "%s.%s: stored value is not encrypted (legacy plaintext row) - "
                "returning it unchanged; it is encrypted the next time it is saved.",
                getattr(getattr(self, "model", None), "__name__", "?"),
                getattr(self, "name", "?"),
            )
            return value


class EncryptedJSONField(EncryptedTextField):
    """
    Same as EncryptedTextField, but (de)serializes JSON so calling code
    keeps using it as a plain dict, exactly like the JSONField it replaces.
    """

    def get_prep_value(self, value):
        return super().get_prep_value(json.dumps(value if value is not None else {}))

    def from_db_value(self, value, expression, connection):
        value = super().from_db_value(value, expression, connection)
        if not value:
            return {}
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            # Almost always means the InvalidToken branch above already
            # fired and handed back undecrypted ciphertext (not valid
            # JSON) rather than this being genuinely malformed JSON - log
            # here too since that warning alone doesn't say the caller is
            # about to silently lose the whole stored config to `{}`.
            logger.warning(
                "%s.%s: decrypted value is not valid JSON - returning {} "
                "instead of the stored data.",
                getattr(getattr(self, "model", None), "__name__", "?"),
                getattr(self, "name", "?"),
            )
            return {}

    def to_python(self, value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
