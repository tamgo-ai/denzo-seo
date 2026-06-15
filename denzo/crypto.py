"""
crypto.py — Token encryption at rest via Fernet (AES-128-CBC + HMAC).

Tokens encrypted: github_token, wp_app_password (client_context),
                  access_token, refresh_token (oauth_tokens).

Backward-compatible: plaintext tokens (encrypted=0) are returned as-is.
Encrypted tokens (encrypted=1) are decrypted transparently on read.

Requires DENZO_ENCRYPTION_KEY in .env (generate with Fernet.generate_key()).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FERNET_SINGLETON: "Fernet | None" = None
_FERNET_UNAVAILABLE: bool = False


def _get_fernet():
    """Lazy-load Fernet from DENZO_ENCRYPTION_KEY env var. Singleton per process."""
    global _FERNET_SINGLETON, _FERNET_UNAVAILABLE

    if _FERNET_SINGLETON is not None:
        return _FERNET_SINGLETON
    if _FERNET_UNAVAILABLE:
        return None

    key = os.getenv("DENZO_ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning(
            "DENZO_ENCRYPTION_KEY not set in .env — tokens will be stored in PLAINTEXT. "
            "Generate one with: python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
        _FERNET_UNAVAILABLE = True
        return None

    try:
        from cryptography.fernet import Fernet
        _FERNET_SINGLETON = Fernet(key.encode("utf-8"))
        return _FERNET_SINGLETON
    except Exception as e:
        logger.error(f"Failed to initialize Fernet: {e}")
        _FERNET_UNAVAILABLE = True
        return None


def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a token. Returns Fernet ciphertext if key is configured,
    otherwise returns plaintext as-is (dev mode).
    Identity: encrypt_token("") → ""
    """
    if not plaintext or not plaintext.strip():
        return plaintext

    fernet = _get_fernet()
    if fernet is None:
        return plaintext  # no encryption key configured — dev mode

    try:
        return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Encryption failed, storing plaintext as fallback: {e}")
        return plaintext


def decrypt_token(ciphertext: str, encrypted: bool = True) -> str:
    """
    Decrypt a token. If encrypted=False, returns as-is (legacy plaintext).
    If Fernet unavailable, returns as-is with warning.
    Handles Fernet token format detection: if ciphertext doesn't start
    with 'gAAAAAB', treats it as unencrypted.
    """
    if not ciphertext or not ciphertext.strip():
        return ciphertext

    # Legacy plaintext — return as-is
    if not encrypted:
        return ciphertext

    # Autodetect: Fernet tokens always start with 'gAAAAAB' (base64-encoded)
    if not ciphertext.startswith("gAAAAAB"):
        return ciphertext  # already plaintext, or non-Fernet format

    fernet = _get_fernet()
    if fernet is None:
        logger.warning("DENZO_ENCRYPTION_KEY not configured — returning ciphertext as-is")
        return ciphertext

    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Decryption failed, returning ciphertext as-is: {e}")
        return ciphertext


def is_encryption_available() -> bool:
    """Check if encryption is configured (for UI/status indicators)."""
    return _get_fernet() is not None
