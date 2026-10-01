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

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


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


class EncryptedTextField(models.TextField):
    """
    Encrypts on write, decrypts on read. A value that fails to decrypt is
    treated as a legacy plaintext row written before this field existed.
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
            # Either a legacy plaintext row written before this field
            # existed, or ciphertext that no longer decrypts under the
            # current SECRET_KEY (e.g. after a key rotation) - the two are
            # indistinguishable from here, so we still return the raw value
            # rather than raising, but log it so a rotation that silently
            # blanks out every stored credential doesn't go unnoticed.
            logger.warning(
                "%s.%s: stored value failed to decrypt - returning it "
                "unchanged. Expected for legacy plaintext rows written "
                "before this field existed; unexpected (and a sign "
                "SECRET_KEY was rotated) for anything written since.",
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
