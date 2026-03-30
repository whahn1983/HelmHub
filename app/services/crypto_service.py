"""Symmetric encryption helpers for sensitive fields."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class EncryptionKeyError(ValueError):
    """Raised when the configured encryption key is missing or invalid."""


def _build_fernet(key: str | None) -> Fernet:
    if not key:
        raise EncryptionKeyError('TOTP_ENCRYPTION_KEY is required.')
    try:
        return Fernet(key.encode('utf-8'))
    except Exception as exc:  # noqa: BLE001
        raise EncryptionKeyError('TOTP_ENCRYPTION_KEY must be a valid Fernet key.') from exc


def encrypt_value(plaintext: str, key: str | None) -> str:
    """Encrypt *plaintext* with Fernet using *key*."""
    fernet = _build_fernet(key)
    return fernet.encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt_value(ciphertext: str, key: str | None) -> str:
    """Decrypt *ciphertext* with Fernet using *key*."""
    fernet = _build_fernet(key)
    try:
        return fernet.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except InvalidToken as exc:
        raise EncryptionKeyError(
            'Failed to decrypt TOTP secret. Check TOTP_ENCRYPTION_KEY configuration.'
        ) from exc
