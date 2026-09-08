from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache
from typing import Optional, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings

logger = logging.getLogger(__name__)


class TokenStore(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


class FernetTokenStore:
    """Local encrypted token storage. Swap for Secrets Manager via TOKEN_BACKEND later."""

    def __init__(self, key_material: str):
        self._fernet = Fernet(self._derive_key(key_material))

    @staticmethod
    def _derive_key(material: str) -> bytes:
        # Accept raw Fernet keys or arbitrary secrets (derive urlsafe 32-byte key).
        raw = material.encode("utf-8")
        try:
            if len(material) >= 40:
                Fernet(material.encode("utf-8"))
                return material.encode("utf-8")
        except Exception:
            pass
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt connector token.") from exc


class SecretsManagerTokenStore:
    """Placeholder for AWS Secrets Manager. Not implemented in Phase D."""

    def encrypt(self, plaintext: str) -> str:
        raise RuntimeError("TOKEN_BACKEND=secrets_manager is not configured yet.")

    def decrypt(self, ciphertext: str) -> str:
        raise RuntimeError("TOKEN_BACKEND=secrets_manager is not configured yet.")


def build_token_store(settings: Settings) -> TokenStore:
    backend = settings.token_backend.lower().strip()
    if backend == "fernet":
        return FernetTokenStore(settings.token_encryption_key)
    if backend == "secrets_manager":
        return SecretsManagerTokenStore()
    raise RuntimeError(f"Unknown TOKEN_BACKEND: {backend}")


@lru_cache
def get_token_store() -> TokenStore:
    from app.config import get_settings

    return build_token_store(get_settings())
