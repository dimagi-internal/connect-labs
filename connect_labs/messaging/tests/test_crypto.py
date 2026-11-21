"""The channel encryption is PersonalID's, byte for byte: open-chat-studio's layout and connect-id's."""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from connect_labs.messaging import crypto


def test_a_text_round_trips():
    key = crypto.new_key()
    assert len(base64.b64decode(key)) == 32
    sealed = crypto.encrypt_text(key, "Habari! Your stock is low ✓")
    assert set(sealed) == {"ciphertext", "tag", "nonce"}
    assert len(base64.b64decode(sealed["nonce"])) == 12
    assert len(base64.b64decode(sealed["tag"])) == 16
    assert crypto.decrypt_text(key, sealed) == "Habari! Your stock is low ✓"


def test_reads_what_the_reference_sender_writes():
    """connect-id's _local_message_server: AESGCM, ciphertext = sealed[:-16], tag = sealed[-16:]."""
    key = base64.b64encode(os.urandom(32)).decode()
    nonce = os.urandom(12)
    sealed = AESGCM(base64.b64decode(key)).encrypt(nonce, b"from the phone", None)
    message = {
        "ciphertext": base64.b64encode(sealed[:-16]).decode(),
        "tag": base64.b64encode(sealed[-16:]).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }
    assert crypto.decrypt_text(key, message) == "from the phone"


def test_what_we_write_the_reference_receiver_reads():
    """connect-id's decrypt: AESGCM(key).decrypt(nonce, ciphertext + tag, None)."""
    key = crypto.new_key()
    message = crypto.encrypt_text(key, "to the phone")
    sealed = base64.b64decode(message["ciphertext"]) + base64.b64decode(message["tag"])
    plain = AESGCM(base64.b64decode(key)).decrypt(base64.b64decode(message["nonce"]), sealed, None)
    assert plain.decode() == "to the phone"


def test_ocs_layout_is_the_same_split():
    """open-chat-studio (pycryptodome) sends cipher.encrypt_and_digest -> (ciphertext, 16-byte tag), 16-byte nonce
    by default; our decrypt accepts any nonce length GCM allows, and the phone sends 12."""
    key = crypto.new_key()
    nonce = os.urandom(16)  # pycryptodome's default GCM nonce
    sealed = AESGCM(base64.b64decode(key)).encrypt(nonce, b"ocs", None)
    message = {
        "ciphertext": base64.b64encode(sealed[:-16]).decode(),
        "tag": base64.b64encode(sealed[-16:]).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }
    assert crypto.decrypt_text(key, message) == "ocs"


def test_attachment_layout_is_nonce_ciphertext_tag():
    key = crypto.new_key()
    blob = crypto.encrypt_attachment(key, b"\x89PNG...")
    nonce, sealed = blob[:12], blob[12:]
    assert AESGCM(base64.b64decode(key)).decrypt(nonce, sealed, None) == b"\x89PNG..."
    assert crypto.decrypt_attachment(key, blob) == b"\x89PNG..."


@pytest.mark.parametrize(
    "tamper",
    [
        lambda m, k: {**m, "tag": base64.b64encode(os.urandom(16)).decode()},
        lambda m, k: {**m, "ciphertext": base64.b64encode(b"x" + base64.b64decode(m["ciphertext"])).decode()},
        lambda m, k: {k2: v for k2, v in m.items() if k2 != "nonce"},
    ],
)
def test_a_tampered_or_broken_message_is_refused(tamper):
    key = crypto.new_key()
    message = tamper(crypto.encrypt_text(key, "hello"), key)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_text(key, message)


def test_the_wrong_key_is_refused():
    message = crypto.encrypt_text(crypto.new_key(), "hello")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_text(crypto.new_key(), message)
