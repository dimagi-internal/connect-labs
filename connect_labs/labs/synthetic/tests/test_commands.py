"""Tests for per-phase synthetic management commands."""

from io import StringIO
from unittest.mock import patch


def test_profile_command_parses_opps_and_calls_service(tmp_path, monkeypatch):
    monkeypatch.setenv("X_TOKEN", "test-token-value")
    with patch(
        "connect_labs.labs.synthetic.clone_from_prod.profile_opps_bulk",
        return_value=(str(tmp_path), []),
    ) as svc:
        from django.core.management import call_command

        call_command(
            "synthetic_profile_opps",
            "--opps",
            "523,524",
            "--out",
            str(tmp_path),
            "--token-env",
            "X_TOKEN",
            "--base-url",
            "https://x",
            stdout=StringIO(),
        )
    assert svc.called
    assert list(svc.call_args.args[0]) == [523, 524]
