"""Tests for EncryptionService (Fernet + PBKDF2).

Locks in invariant 13 from docs-internal/credentials_panel.md:
  - Encryption is reversible for unicode and large payloads
  - Tampered ciphertext raises ValueError
  - Uninitialized service raises RuntimeError
"""

from __future__ import annotations

import pytest

from core.encryption import EncryptionService


pytestmark = pytest.mark.credentials


class TestRoundTrip:
    def test_ascii_roundtrip(self, encryption: EncryptionService):
        assert encryption.decrypt(encryption.encrypt("sk-test-1234567890")) == "sk-test-1234567890"

    def test_unicode_roundtrip(self, encryption: EncryptionService):
        plaintext = "Привет мир — Здравствуй! 你好 🌍 emoji ✨"
        assert encryption.decrypt(encryption.encrypt(plaintext)) == plaintext

    def test_empty_string_roundtrip(self, encryption: EncryptionService):
        assert encryption.decrypt(encryption.encrypt("")) == ""

    def test_large_payload_roundtrip(self, encryption: EncryptionService):
        plaintext = "x" * (64 * 1024)
        assert encryption.decrypt(encryption.encrypt(plaintext)) == plaintext

    def test_ciphertext_differs_from_plaintext(self, encryption: EncryptionService):
        plaintext = "secret-api-key"
        ciphertext = encryption.encrypt(plaintext)
        assert ciphertext != plaintext
        assert plaintext not in ciphertext

    def test_two_encryptions_of_same_plaintext_differ(self, encryption: EncryptionService):
        # Fernet uses a random IV per encryption -> ciphertexts must differ.
        a = encryption.encrypt("same")
        b = encryption.encrypt("same")
        assert a != b
        assert encryption.decrypt(a) == encryption.decrypt(b) == "same"


class TestErrorPaths:
    def test_uninitialized_encrypt_raises_runtime_error(self, uninitialized_encryption: EncryptionService):
        with pytest.raises(RuntimeError, match="not initialized"):
            uninitialized_encryption.encrypt("data")

    def test_uninitialized_decrypt_raises_runtime_error(self, uninitialized_encryption: EncryptionService):
        with pytest.raises(RuntimeError, match="not initialized"):
            uninitialized_encryption.decrypt("anything")

    def test_tampered_ciphertext_raises_value_error(self, encryption: EncryptionService):
        ciphertext = encryption.encrypt("secret")
        # Flip one character in the middle of the token
        tampered = ciphertext[:-4] + "AAAA"
        with pytest.raises(ValueError, match="Decryption failed"):
            encryption.decrypt(tampered)

    def test_garbage_ciphertext_raises_value_error(self, encryption: EncryptionService):
        with pytest.raises(ValueError):
            encryption.decrypt("not-a-fernet-token")

    def test_wrong_key_raises_value_error(self, encryption_salt: bytes):
        a = EncryptionService()
        a.initialize("password-A", encryption_salt)
        b = EncryptionService()
        b.initialize("password-B", encryption_salt)

        ciphertext = a.encrypt("secret")
        with pytest.raises(ValueError):
            b.decrypt(ciphertext)


class TestSalt:
    def test_generate_salt_is_32_bytes(self):
        salt = EncryptionService.generate_salt()
        assert isinstance(salt, bytes)
        assert len(salt) == 32

    def test_generate_salt_is_random(self):
        salts = {EncryptionService.generate_salt() for _ in range(20)}
        assert len(salts) == 20  # extremely unlikely to collide


class TestState:
    def test_is_initialized_false_before_initialize(self, uninitialized_encryption: EncryptionService):
        assert uninitialized_encryption.is_initialized() is False

    def test_is_initialized_true_after_initialize(self, encryption: EncryptionService):
        assert encryption.is_initialized() is True

    def test_clear_resets_state(self, encryption: EncryptionService):
        encryption.clear()
        assert encryption.is_initialized() is False
        with pytest.raises(RuntimeError):
            encryption.encrypt("after-clear")
