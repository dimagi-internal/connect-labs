#!/usr/bin/env python3
"""Ask a person for a one-hour, no-user-visit labs token, from an agent that is NOT on their computer.

`setup_labs_token.py --temporary` sends the token back to 127.0.0.1, so it only works when the
person's browser and the agent share a machine. This is for the other case (a cloud runner, a
remote box): the agent asks with its own labs token, the person approves on a labs page signed in
as themselves, and the token comes back sealed to a key only this script holds.

    request_agent_token.py ask <labs_base_url> --reason "what you will read, and why" [--level full]
        Prints the approval link for the person (stdout: one JSON line with `ref` and `approve_url`).
        Keeps the private half of a fresh key in a 0600 state file.

    request_agent_token.py collect <ref> [--output PATH] [--wait SECONDS]
        Waits for the answer. On approval writes the token to PATH (0600) and prints only the path.
        Exit 0 approved, 4 still pending, 3 declined or expired, 1 other failure.

The agent's own labs token is read from $LABS_MCP_TOKEN (or --token-file). The default level is
no-user-visit (it never sees a real visit row); `--level full` asks for everything the person can
do. The person's passkey, through canopy, is needed for either. The token lasts one hour. Never
print the token; delete the file when the job is done. Needs the `cryptography` package.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Must match connect_labs/utils/sealed_output.py.
VERSION = 1
INFO = b"connect-labs sealed-output v1"
STATE_DIR = Path(os.environ.get("LABS_AGENT_REQUEST_DIR", Path.home() / ".labs-agent-request"))
POLL_SECONDS = 3
LEVELS = {"no-user-visit": "no-uservisit-data", "full": "full"}


def _own_token(token_file: str | None) -> str:
    raw = Path(token_file).read_text().strip() if token_file else os.environ.get("LABS_MCP_TOKEN", "").strip()
    if not raw:
        sys.exit("No labs token of your own: set LABS_MCP_TOKEN or pass --token-file.")
    return raw


def _post(url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def _unseal(blob: bytes, private: X25519PrivateKey) -> bytes:
    if not blob or blob[0] != VERSION:
        raise ValueError("unknown sealed-output version")
    epk, nonce, ct = blob[1:33], blob[33:45], blob[45:]
    rpk = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = private.exchange(X25519PublicKey.from_public_bytes(epk))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + rpk, info=INFO).derive(shared)
    return zlib.decompress(ChaCha20Poly1305(key).decrypt(nonce, ct, bytes([VERSION])))


def ask(args) -> int:
    private = X25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    base = args.labs_base_url.rstrip("/")
    status, body = _post(
        f"{base}/mcp/admin/agent-requests/",
        _own_token(args.token_file),
        {"reason": args.reason, "recipient_key": public, "scope": LEVELS[args.level]},
    )
    if status != 201:
        message = (body.get("error") or {}).get("message", "")
        sys.exit(f"Labs refused the request ({status}): {message}")
    private_bytes = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    _write_private(
        STATE_DIR / f"{body['ref']}.json",
        json.dumps({"base": base, "private_key": base64.b64encode(private_bytes).decode()}),
    )
    print(json.dumps({"ref": body["ref"], "approve_url": body["approve_url"], "expires_at": body["expires_at"]}))
    return 0


def collect(args) -> int:
    state_path = STATE_DIR / f"{args.ref}.json"
    if not state_path.exists():
        sys.exit(f"No saved request {args.ref!r} on this computer: ask again.")
    state = json.loads(state_path.read_text())
    private = X25519PrivateKey.from_private_bytes(base64.b64decode(state["private_key"]))
    token = _own_token(args.token_file)
    deadline = time.monotonic() + max(args.wait, 0)
    while True:
        status, body = _post(f"{state['base']}/mcp/admin/agent-requests/{args.ref}/claim/", token)
        if status == 404:
            sys.exit("Labs has no such request for this account: ask again.")
        if status != 200:
            sys.exit(f"Labs answered {status} while collecting.")
        answer = body.get("status")
        if answer == "approved":
            plain = _unseal(base64.b64decode(body["sealed_token"]), private).decode()
            out = Path(args.output) if args.output else Path(tempfile.mkdtemp(prefix="labs-token-")) / "token"
            _write_private(out, plain)
            state_path.unlink(missing_ok=True)
            print(str(out))
            return 0
        if answer in ("declined", "expired", "claimed"):
            state_path.unlink(missing_ok=True)
            print(f"Request {answer}.", file=sys.stderr)
            return 3
        if time.monotonic() >= deadline:
            print("Still waiting for the person.", file=sys.stderr)
            return 4
        time.sleep(POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ask = sub.add_parser("ask", help="create the request and print the approval link")
    p_ask.add_argument("labs_base_url")
    p_ask.add_argument("--reason", required=True, help="what you will read and why (shown to the person)")
    p_ask.add_argument("--level", choices=sorted(LEVELS), default="no-user-visit", help="default: no-user-visit")
    p_ask.add_argument("--token-file", help="a file holding the agent's own labs token (default: $LABS_MCP_TOKEN)")
    p_ask.set_defaults(func=ask)
    p_collect = sub.add_parser("collect", help="wait for the answer and write the token to a private file")
    p_collect.add_argument("ref")
    p_collect.add_argument("--output", help="where to write the token (default: a new private file)")
    p_collect.add_argument("--wait", type=int, default=240, help="seconds to wait for the person (default 240)")
    p_collect.add_argument("--token-file", help="a file holding the agent's own labs token (default: $LABS_MCP_TOKEN)")
    p_collect.set_defaults(func=collect)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
