import os
import stat
from pathlib import Path

import pytest

from src.storage.encryption import CredentialEncryptor


class TestCredentialEncryptor:
    def test_encrypt_decrypt_round_trip(self, tmp_path: Path) -> None:
        encryptor = CredentialEncryptor(tmp_path / ".credential_key")

        plaintext = "my_secret_refresh_token_abc123"
        ciphertext = encryptor.encrypt(plaintext)

        assert ciphertext != plaintext
        assert encryptor.decrypt(ciphertext) == plaintext

    def test_key_persists_across_instances(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".credential_key"

        enc1 = CredentialEncryptor(key_path)
        ciphertext = enc1.encrypt("secret_value")

        enc2 = CredentialEncryptor(key_path)
        assert enc2.decrypt(ciphertext) == "secret_value"

    def test_key_file_permissions(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".credential_key"
        encryptor = CredentialEncryptor(key_path)
        encryptor.encrypt("trigger_key_creation")

        file_mode = os.stat(key_path).st_mode
        assert file_mode & stat.S_IRUSR
        assert file_mode & stat.S_IWUSR
        assert not (file_mode & stat.S_IRGRP)
        assert not (file_mode & stat.S_IWGRP)
        assert not (file_mode & stat.S_IROTH)
        assert not (file_mode & stat.S_IWOTH)

    def test_rejects_world_readable_key_file(self, tmp_path: Path) -> None:
        key_path = tmp_path / ".credential_key"
        enc = CredentialEncryptor(key_path)
        enc.encrypt("trigger key creation")

        os.chmod(key_path, 0o644)

        enc2 = CredentialEncryptor(key_path)
        with pytest.raises(PermissionError, match="insecure permissions"):
            enc2.encrypt("should fail")
