"""Fernet-based credential encryption for at-rest protection."""

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# Permissions that are acceptable on the key file: owner read/write only.
_SECURE_PERMISSIONS = stat.S_IRUSR | stat.S_IWUSR  # 0o600


class CredentialEncryptor:
    """Encrypt and decrypt credential values using Fernet symmetric encryption.

    The encryption key is stored in a file with restricted permissions (0600).
    On first use, a new key is auto-generated if the key file does not exist.
    On subsequent uses, the key file's permissions are verified before loading.

    Args:
        key_path: Path to the Fernet key file (e.g. ``data/.credential_key``).
    """

    def __init__(self, key_path: Path) -> None:
        self._key_path = key_path
        self._fernet: Fernet | None = None

    def _ensure_key(self) -> Fernet:
        """Load or generate the Fernet key, lazily."""
        if self._fernet is not None:
            return self._fernet

        if self._key_path.exists():
            self._verify_key_permissions()
            key = self._key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            # Write with restrictive permissions: owner read/write only
            fd = os.open(
                str(self._key_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                _SECURE_PERMISSIONS,
            )
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
            logger.info("Generated new credential encryption key")

        self._fernet = Fernet(key)
        return self._fernet

    def _verify_key_permissions(self) -> None:
        """Verify the key file has secure permissions (0600).

        Raises:
            PermissionError: If the key file is group- or world-readable.
        """
        mode = self._key_path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
            raise PermissionError(
                f"Credential key file has insecure permissions "
                f"({oct(mode & 0o777)}). Expected 0600. "
                f"Fix with: chmod 600 {self._key_path}"
            )

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string, returning a base64-encoded ciphertext.

        Args:
            plaintext: The value to encrypt.

        Returns:
            Fernet-encrypted, base64-encoded string.
        """
        fernet = self._ensure_key()
        return fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a Fernet-encrypted, base64-encoded ciphertext.

        Args:
            ciphertext: The encrypted value.

        Returns:
            Decrypted plaintext string.
        """
        fernet = self._ensure_key()
        return fernet.decrypt(ciphertext.encode()).decode()
