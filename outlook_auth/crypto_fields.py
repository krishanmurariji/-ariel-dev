"""
outlook_auth/crypto_fields.py

Model fields that keep their value encrypted at rest, so a database leak or
an over-privileged admin/DB user can't read the Azure client secret or the
Outlook OAuth tokens directly. Reading a pre-encryption plaintext row still
works transparently - it gets encrypted the next time that row is saved.

The actual encrypt/decrypt logic lives in horilla.crypto_fields (the
shared copy every other app imports) - these classes just subclass it
under this module's original path. They can't simply import and reuse
those classes directly in outlook_auth's models: Django migrations store
a field's class by its dotted module path
(outlook_auth.crypto_fields.EncryptedTextField), and outlook_auth's
migrations were already applied in production against that path before
horilla.crypto_fields existed. Subclassing keeps that path resolvable
(deconstruct() still reports outlook_auth.crypto_fields.*) while
delegating all behavior to the shared implementation, so a fix made there
(e.g. logging a failed decrypt) applies here too without copy-pasting it.
"""

from horilla.crypto_fields import EncryptedJSONField as _EncryptedJSONField
from horilla.crypto_fields import EncryptedTextField as _EncryptedTextField


class EncryptedTextField(_EncryptedTextField):
    pass


class EncryptedJSONField(_EncryptedJSONField):
    pass
