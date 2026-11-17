"""Encryption at rest, API key hashing and user identity tokens.

Provider secrets (Firebase service accounts, APNs .p8 keys) and each tenant's
identity secret are stored as AES-256-GCM ciphertext under MASTER_ENCRYPTION_KEY.
The ciphertext carries its key version ("v1:...") so the master key can be
rotated later by adding a v2 and re-encrypting, without a flag day.

API keys are high-entropy random strings, so a plain SHA-256 is the right hash:
it is a lookup index, not a password store, and it keeps the auth query to one
indexed row.
"""

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

KEY_VERSION = "v1"


def _master_key() -> bytes:
    key = base64.b64decode(settings.MASTER_ENCRYPTION_KEY)
    if len(key) != 32:
        raise ValueError("MASTER_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt(plaintext: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), KEY_VERSION.encode())
    return f"{KEY_VERSION}:{base64.b64encode(nonce + sealed).decode()}"


def decrypt(token: str) -> str:
    version, _, data = token.partition(":")
    if version != KEY_VERSION:
        raise ValueError(f"unknown encryption key version {version!r}")
    raw = base64.b64decode(data)
    return AESGCM(_master_key()).decrypt(raw[:12], raw[12:], version.encode()).decode()


def new_api_key(kind: str) -> str:
    return f"{'sk' if kind == 'secret' else 'pk'}_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_identity_secret() -> str:
    return secrets.token_urlsafe(32)


def user_token(identity_secret: str, user_external_id: str) -> str:
    """HMAC-SHA256(identity_secret, user id), hex.

    The tenant's own backend computes this after the user signs in and hands it
    to its app. The app passes it when registering a device, which proves the
    device really belongs to that user. Without it, anyone holding the public
    pk_ key could attach their phone to someone else's user id and receive that
    person's notifications.
    """
    return hmac.new(identity_secret.encode(), user_external_id.encode(), hashlib.sha256).hexdigest()


def verify_user_token(identity_secret: str, user_external_id: str, token: str) -> bool:
    return hmac.compare_digest(user_token(identity_secret, user_external_id), token or "")
