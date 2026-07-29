"""Tests for safe_json_for_script — inline-<script> XSS hardening."""
import json

from connect_labs.utils.json_safe import safe_json_for_script


def test_escapes_script_close_sequence():
    payload = {"title": "</script><script>alert(document.cookie)</script>"}
    out = safe_json_for_script(payload)
    # The literal breakout sequence must not survive.
    assert "</script>" not in out
    assert "<" not in out
    assert ">" not in out
    # ...but the value round-trips identically once parsed as JS/JSON would.
    assert json.loads(out) == payload


def test_escapes_ampersand_and_line_separators():
    payload = {"x": "a & b", "y": f"line{chr(0x2028)}sep{chr(0x2029)}end"}
    out = safe_json_for_script(payload)
    assert "&" not in out
    assert chr(0x2028) not in out
    assert chr(0x2029) not in out
    assert json.loads(out) == payload


def test_plain_values_are_unchanged_when_parsed():
    payload = {"a": 1, "b": ["x", "y"], "c": None}
    assert json.loads(safe_json_for_script(payload)) == payload
