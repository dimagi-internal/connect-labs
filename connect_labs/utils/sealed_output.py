"""Seal a command's output to a public key only the requester holds.

`run-labs-command.yml` runs code against prod's DB and secrets, and whatever the
task printed used to be copied into the GitHub Actions log -- readable by every
reader of the repo for ~90 days. A sealed run (`manage.py run_sealed`) captures
the command's stdout+stderr in memory and prints only this envelope, so neither
CloudWatch nor the Actions log ever holds the plaintext.

Envelope: X25519 ECDH with a fresh ephemeral key -> HKDF-SHA256 -> ChaCha20-
Poly1305 over zlib(plaintext). The requester makes a NEW keypair per run and
keeps the private half in memory (see hal `bin/hal-labs-prod-read`), so there is
no stored key to steal and an old log is undecryptable once that process exits.

    blob = 0x01 || ephemeral_pub(32) || nonce(12) || ciphertext

Printed as BEGIN_MARKER line, base64 chunks, END_MARKER line. The header carries
only sizes -- never a plaintext hash (a hash of a short answer is guessable).
"""

from __future__ import annotations

import base64
import os
import re
import zlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
INFO = b"connect-labs sealed-output v1"
BEGIN_MARKER = "__SEALED_OUTPUT_BEGIN__"
END_MARKER = "__SEALED_OUTPUT_END__"
# Under the awslogs driver's 16 KiB line split, so each chunk is one log event.
CHUNK = 3000
_RECIPIENT_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


class SealedOutputError(ValueError):
    pass


def parse_recipient(recipient_b64: str) -> X25519PublicKey:
    if not _RECIPIENT_RE.match(recipient_b64 or ""):
        raise SealedOutputError("recipient must be a base64 raw X25519 public key (44 chars)")
    return X25519PublicKey.from_public_bytes(base64.b64decode(recipient_b64))


def _key(shared: bytes, epk: bytes, rpk: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + rpk, info=INFO).derive(shared)


def seal(plaintext: bytes, recipient_b64: str) -> bytes:
    rpk_obj = parse_recipient(recipient_b64)
    rpk = rpk_obj.public_bytes_raw()
    esk = X25519PrivateKey.generate()
    epk = esk.public_key().public_bytes_raw()
    key = _key(esk.exchange(rpk_obj), epk, rpk)
    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, zlib.compress(plaintext, 9), bytes([VERSION]))
    return bytes([VERSION]) + epk + nonce + ct


def unseal(blob: bytes, private_key: X25519PrivateKey) -> bytes:
    """The requester's side. Kept here so the round trip is tested in-repo."""
    if not blob or blob[0] != VERSION:
        raise SealedOutputError("unknown sealed-output version")
    epk, nonce, ct = blob[1:33], blob[33:45], blob[45:]
    rpk = private_key.public_key().public_bytes_raw()
    shared = private_key.exchange(X25519PublicKey.from_public_bytes(epk))
    pt = ChaCha20Poly1305(_key(shared, epk, rpk)).decrypt(nonce, ct, bytes([VERSION]))
    return zlib.decompress(pt)


def envelope_lines(blob: bytes, *, plaintext_bytes: int, plaintext_lines: int) -> list[str]:
    b64 = base64.b64encode(blob).decode()
    chunks = [b64[i : i + CHUNK] for i in range(0, len(b64), CHUNK)] or [""]
    header = f"{BEGIN_MARKER} v{VERSION} bytes={plaintext_bytes} lines={plaintext_lines} chunks={len(chunks)}"
    return [header, *chunks, END_MARKER]


def read_envelope(text: str) -> bytes:
    """Pull the base64 blob back out of log text. Tolerates prefixes on each line
    (Actions adds `<job>\\t<step>\\t<timestamp> `); a chunk is the last token."""
    lines = text.splitlines()
    begins = [i for i, line in enumerate(lines) if BEGIN_MARKER in line and "=" in line.split(BEGIN_MARKER, 1)[1]]
    if not begins:
        raise SealedOutputError("no sealed output in the text")
    start = begins[-1]
    parts = []
    for line in lines[start + 1 :]:
        if END_MARKER in line:
            return base64.b64decode("".join(parts))
        token = line.strip().split()[-1] if line.strip() else ""
        parts.append(token)
    raise SealedOutputError("sealed output is truncated (no end marker)")
