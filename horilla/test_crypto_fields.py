"""
C-3 (step 1): EncryptedTextField/EncryptedJSONField used to derive their
Fernet key directly from SECRET_KEY on every call, with no way to rotate
it independently - rotating SECRET_KEY (for any reason, including an
incident response) would silently and permanently lose every stored
credential. FIELD_ENCRYPTION_KEYS + MultiFernet fixes that, but the
whole point is that it must not require re-encrypting or losing a single
existing row: the setting falls back, by default, to the exact key every
row has always implicitly been encrypted with. These tests exist mainly
to prove that fallback is byte-for-byte compatible with the old code
path - the one property that actually matters here - before this change
ever reaches a deployment with real encrypted data in it.
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from horilla.crypto_fields import (
    EncryptedJSONField,
    EncryptedTextField,
    _get_fernet,
    _legacy_key,
)


def _old_scheme_encrypt(value, secret_key):
    """Exactly what the pre-fix _get_fernet() did: Fernet(SHA256(SECRET_KEY))."""
    digest = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest)).encrypt(value.encode()).decode()


class LegacyKeyFallbackTests(SimpleTestCase):
    """The critical property: with no FIELD_ENCRYPTION_KEYS configured,
    behavior must be identical to before this change existed."""

    @override_settings(SECRET_KEY="a-test-secret-key", FIELD_ENCRYPTION_KEYS=None)
    def test_a_row_encrypted_under_the_old_scheme_still_decrypts(self):
        old_ciphertext = _old_scheme_encrypt("sk-live-abc123", "a-test-secret-key")

        field = EncryptedTextField()
        decrypted = field.from_db_value(old_ciphertext, None, None)

        self.assertEqual(decrypted, "sk-live-abc123")

    @override_settings(SECRET_KEY="a-test-secret-key", FIELD_ENCRYPTION_KEYS=None)
    def test_new_writes_still_use_the_same_legacy_derived_key(self):
        field = EncryptedTextField()
        ciphertext = field.get_prep_value("hello")

        # Decryptable by the plain old-scheme Fernet directly - proves the
        # new code path writes exactly what the old one would have.
        digest = hashlib.sha256(b"a-test-secret-key").digest()
        plain = Fernet(base64.urlsafe_b64encode(digest)).decrypt(
            ciphertext.encode()
        )
        self.assertEqual(plain.decode(), "hello")

    @override_settings(SECRET_KEY="another-key")
    def test_legacy_key_helper_matches_the_manual_derivation(self):
        digest = hashlib.sha256(b"another-key").digest()
        expected = base64.urlsafe_b64encode(digest).decode()
        self.assertEqual(_legacy_key(), expected)


class EncryptedFieldRoundTripTests(SimpleTestCase):
    @override_settings(SECRET_KEY="rt-key", FIELD_ENCRYPTION_KEYS=None)
    def test_text_field_round_trip(self):
        field = EncryptedTextField()
        stored = field.get_prep_value("super-secret-password")
        self.assertNotIn("super-secret-password", stored)
        self.assertEqual(
            field.from_db_value(stored, None, None), "super-secret-password"
        )

    @override_settings(SECRET_KEY="rt-key", FIELD_ENCRYPTION_KEYS=None)
    def test_text_field_none_and_empty_pass_through_unencrypted(self):
        field = EncryptedTextField()
        self.assertIsNone(field.get_prep_value(None))
        self.assertEqual(field.get_prep_value(""), "")
        self.assertIsNone(field.from_db_value(None, None, None))

    @override_settings(SECRET_KEY="rt-key", FIELD_ENCRYPTION_KEYS=None)
    def test_json_field_round_trip(self):
        field = EncryptedJSONField()
        stored = field.get_prep_value({"client_id": "abc", "client_secret": "xyz"})
        self.assertNotIn("client_secret", stored)
        self.assertEqual(
            field.from_db_value(stored, None, None),
            {"client_id": "abc", "client_secret": "xyz"},
        )

    @override_settings(SECRET_KEY="rt-key", FIELD_ENCRYPTION_KEYS=None)
    def test_json_field_empty_value_round_trips_to_empty_dict(self):
        field = EncryptedJSONField()
        stored = field.get_prep_value(None)
        self.assertEqual(field.from_db_value(stored, None, None), {})


class RotationCapabilityTests(SimpleTestCase):
    """Proves the actual point of this change: with an explicit key list,
    new writes move to the newest key while old ciphertext under a
    retired key still decrypts - the capability that was previously
    entirely impossible."""

    def test_new_key_first_in_list_is_used_for_new_writes_and_old_data_still_reads(
        self,
    ):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        with override_settings(FIELD_ENCRYPTION_KEYS=[old_key]):
            old_field = EncryptedTextField()
            old_ciphertext = old_field.get_prep_value("pre-rotation-secret")

        with override_settings(FIELD_ENCRYPTION_KEYS=[new_key, old_key]):
            field = EncryptedTextField()

            # Old data, written before rotation, still decrypts.
            self.assertEqual(
                field.from_db_value(old_ciphertext, None, None),
                "pre-rotation-secret",
            )

            # New data is encrypted under the new (first) key - a plain
            # Fernet using *only* the new key can decrypt it directly;
            # one using *only* the old key cannot.
            new_ciphertext = field.get_prep_value("post-rotation-secret")
            self.assertEqual(
                Fernet(new_key).decrypt(new_ciphertext.encode()).decode(),
                "post-rotation-secret",
            )
            with self.assertRaises(Exception):
                Fernet(old_key).decrypt(new_ciphertext.encode())

    def test_get_fernet_builds_multifernet_from_the_configured_list(self):
        k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with override_settings(FIELD_ENCRYPTION_KEYS=[k1, k2]):
            fernet = _get_fernet()
        # MultiFernet exposes the keys it was built from via ._fernets.
        self.assertEqual(len(fernet._fernets), 2)
