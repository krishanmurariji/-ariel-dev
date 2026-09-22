"""
outlook_auth/crypto_fields.py

Model fields that keep their value encrypted at rest, so a database leak or
an over-privileged admin/DB user can't read the Azure client secret or the
Outlook OAuth tokens directly. Reading a pre-encryption plaintext row still
works transparently - it gets encrypted the next time that row is saved.
"""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _get_fernet() -> Fernet:
    # Derived from SECRET_KEY so this needs no extra key to manage or
    # rotate separately - same tradeoff Django already makes for signed
    # cookies/sessions: rotating SECRET_KEY invalidates old ciphertext too.
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


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
