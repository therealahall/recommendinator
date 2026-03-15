"""Tests for credential encryption."""

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

from src.storage.encryption import CredentialEncryptor


class TestCredentialEncryptor:
    """Tests for CredentialEncryptor."""

    def test_encrypt_decrypt_round_trip(self, tmp_path: Path) -> None:
        """Plaintext survives an encrypt-then-decrypt cycle."""
        encryptor = CredentialEncryptor(tmp_path / ".credential_key")

        plaintext = "my_secret_refresh_token_abc123"
        ciphertext = encryptor.encrypt(plaintext)

        assert ciphertext != plaintext
        assert encryptor.decrypt(ciphertext) == plaintext

    def test_key_auto_generated_on_first_use(self, tmp_path: Path) -> None:
        """Key file is created on first encrypt call."""
        key_path = tmp_path / ".credential_key"
        encryptor = CredentialEncryptor(key_path)

        assert not key_path.exists()
        encryptor.encrypt("test")
        assert key_path.exists()

    def test_key_persists_across_instances(self, tmp_path: Path) -> None:
        """A second encryptor with the same key file can decrypt."""
        key_path = tmp_path / ".credential_key"

        enc1 = CredentialEncryptor(key_path)
        ciphertext = enc1.encrypt("secret_value")

        enc2 = CredentialEncryptor(key_path)
        assert enc2.decrypt(ciphertext) == "secret_value"

    def test_key_file_permissions(self, tmp_path: Path) -> None:
        """Key file is created with owner-only read/write (0600)."""
        key_path = tmp_path / ".credential_key"
        encryptor = CredentialEncryptor(key_path)
        encryptor.encrypt("trigger_key_creation")

        file_mode = os.stat(key_path).st_mode
        # Check that only owner read/write bits are set
        assert file_mode & stat.S_IRUSR  # owner read
        assert file_mode & stat.S_IWUSR  # owner write
        assert not (file_mode & stat.S_IRGRP)  # no group read
        assert not (file_mode & stat.S_IWGRP)  # no group write
        assert not (file_mode & stat.S_IROTH)  # no other read
        assert not (file_mode & stat.S_IWOTH)  # no other write

    def test_different_keys_produce_different_ciphertext(self, tmp_path: Path) -> None:
        """Two different keys encrypt the same plaintext differently."""
        enc1 = CredentialEncryptor(tmp_path / "key1")
        enc2 = CredentialEncryptor(tmp_path / "key2")

        ct1 = enc1.encrypt("same_value")
        ct2 = enc2.encrypt("same_value")

        assert ct1 != ct2

    def test_decrypt_raises_on_invalid_ciphertext(self, tmp_path: Path) -> None:
        """Decrypting garbage raises InvalidToken (not swallowed)."""
        encryptor = CredentialEncryptor(tmp_path / ".credential_key")
        encryptor.encrypt("trigger key creation")

        with pytest.raises(InvalidToken):
            encryptor.decrypt("this is not valid fernet ciphertext")

    def test_decrypt_with_wrong_key_raises(self, tmp_path: Path) -> None:
        """Decrypting with a different key raises InvalidToken."""
        enc1 = CredentialEncryptor(tmp_path / "key1")
        enc2 = CredentialEncryptor(tmp_path / "key2")
        ciphertext = enc1.encrypt("secret")

        with pytest.raises(InvalidToken):
            enc2.decrypt(ciphertext)

    def test_rejects_world_readable_key_file(self, tmp_path: Path) -> None:
        """Loading a key file with insecure permissions raises PermissionError."""
        key_path = tmp_path / ".credential_key"
        enc = CredentialEncryptor(key_path)
        enc.encrypt("trigger key creation")

        # Widen permissions to simulate a misconfigured key file
        os.chmod(key_path, 0o644)

        # A fresh encryptor loading the file should reject it
        enc2 = CredentialEncryptor(key_path)
        with pytest.raises(PermissionError, match="insecure permissions"):
            enc2.encrypt("should fail")
