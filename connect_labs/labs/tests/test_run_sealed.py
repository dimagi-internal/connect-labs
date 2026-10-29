"""run_sealed: the plaintext never reaches stdout; only the recipient can open it."""

import base64
import io
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from django.core.management import call_command
from django.core.management.base import CommandError

from connect_labs.utils import sealed_output as so


def _keypair():
    sk = X25519PrivateKey.generate()
    return sk, base64.b64encode(sk.public_key().public_bytes_raw()).decode()


def _run(capsys, monkeypatch, *wrapped):
    sk, pub = _keypair()
    out = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", out)
    code = 0
    try:
        call_command("run_sealed", "--recipient", pub, "--", *wrapped)
    except SystemExit as exc:
        code = exc.code
    printed = out.getvalue() + capsys.readouterr().out
    return sk, printed, code


def test_round_trip_and_no_plaintext(capsys, monkeypatch):
    sk, printed, code = _run(capsys, monkeypatch, "shell", "-c", "print('flw_username_SECRET_ROW')")
    assert code == 0
    assert "flw_username_SECRET_ROW" not in printed
    assert so.BEGIN_MARKER in printed and so.END_MARKER in printed
    assert "flw_username_SECRET_ROW" in so.unseal(so.read_envelope(printed), sk).decode()


def test_traceback_is_sealed_and_exit_code_kept(capsys, monkeypatch):
    sk, printed, code = _run(capsys, monkeypatch, "shell", "-c", "raise ValueError('visit_value_SECRET')")
    assert code == 1
    assert "visit_value_SECRET" not in printed
    assert "visit_value_SECRET" in so.unseal(so.read_envelope(printed), sk).decode()


def test_other_key_cannot_open():
    _, pub = _keypair()
    other, _ = _keypair()
    with pytest.raises(Exception):
        so.unseal(so.seal(b"x", pub), other)


def test_envelope_survives_actions_log_prefixes():
    sk, pub = _keypair()
    payload = b"row\n" * 5000
    lines = so.envelope_lines(so.seal(payload, pub), plaintext_bytes=len(payload), plaintext_lines=5000)
    log = "\n".join(f"Run management command\tRun command\t2026-10-29T12:00:00.0000000Z {ln}" for ln in lines)
    assert so.unseal(so.read_envelope(log), sk) == payload


def test_bad_recipient_refused():
    with pytest.raises(CommandError):
        call_command("run_sealed", "--recipient", "not-a-key", "--", "check")


def test_workflow_never_prints_raw_task_output():
    """run-labs-command.yml: sealed with a recipient, sizes only without one."""
    from pathlib import Path

    wf = (Path(__file__).resolve().parents[3] / ".github/workflows/run-labs-command.yml").read_text()
    assert "run_sealed" in wf and "recipient" in wf
    assert 'echo "${OUT' not in wf and 'cat "$OUT_FILE"' not in wf
    assert "--output text" not in wf.split("get-log-events", 1)[1].split("\n", 3)[0]
