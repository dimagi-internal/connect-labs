"""The channel encryption PersonalID messaging uses, byte for byte.

Each channel has one AES-256 key, held by the message server (us) and fetched by the worker's
phone from our key endpoint. A text is AES-256-GCM with a 12-byte nonce and no associated
data, sent as three base64 strings -- ``{"ciphertext", "tag", "nonce"}`` -- which PersonalID
relays without reading. An attachment is ``nonce (12) + ciphertext + tag (16)`` in one blob.

These are the formats of open-chat-studio's ``connect_client.py`` (pycryptodome) and
connect-id's reference sender (``_local_message_server/server.py``, ``cryptography``); the
tests pin them against both layouts.

The key argument is called ``encryption_key`` so Sentry's default scrubber drops it from
captured stack-frame locals.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32


class DecryptionError(ValueError):
    """A message could not be decrypted with the channel's key (wrong key, or tampered)."""


def new_key() -> str:
    """A fresh channel key, base64 (as the key endpoint serves it)."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()


def _key(encryption_key: str) -> AESGCM:
    raw = base64.b64decode(encryption_key)
    if len(raw) != KEY_BYTES:
        raise ValueError("a channel key is 32 bytes")
    return AESGCM(raw)


def encrypt_text(encryption_key: str, text: str) -> dict[str, str]:
    nonce = os.urandom(NONCE_BYTES)
    sealed = _key(encryption_key).encrypt(nonce, text.encode("utf-8"), None)
    return {
        "ciphertext": base64.b64encode(sealed[:-TAG_BYTES]).decode(),
        "tag": base64.b64encode(sealed[-TAG_BYTES:]).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }


def decrypt_text(encryption_key: str, message: dict) -> str:
    try:
        sealed = base64.b64decode(message["ciphertext"]) + base64.b64decode(message["tag"])
        nonce = base64.b64decode(message["nonce"])
        return _key(encryption_key).decrypt(nonce, sealed, None).decode("utf-8")
    except Exception as e:  # noqa: BLE001 -- InvalidTag, bad base64, missing fields: all "cannot read it"
        raise DecryptionError(type(e).__name__) from None


def encrypt_attachment(encryption_key: str, content: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + _key(encryption_key).encrypt(nonce, content, None)


def decrypt_attachment(encryption_key: str, blob: bytes) -> bytes:
    try:
        return _key(encryption_key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], None)
    except Exception as e:  # noqa: BLE001
        raise DecryptionError(type(e).__name__) from None
