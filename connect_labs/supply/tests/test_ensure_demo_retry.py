"""The OES render setup step retries a 409 instead of failing the take.

`scripts/walkthroughs/oes/ensure_demo.py` is the `setup:` command every OES
recipe declares, and the reseed it calls is single-flight. Back-to-back callers
collide constantly in the loop it exists to serve — a preflight and the render
that follows it are two reseeds seconds apart — and treating the second one's 409
as fatal aborted a take whose only problem was being punctual. That happened on
the first prod double-take.

Loaded by path because the script lives outside the package (it is invoked as a
subprocess by the canopy recorder, which has no Django).
"""
import importlib.util
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/walkthroughs/oes/ensure_demo.py"


@pytest.fixture()
def ensure_demo():
    spec = importlib.util.spec_from_file_location("ensure_demo_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http_409():
    return urllib.error.HTTPError(
        url="http://x/supply/api/demo/reseed/",
        code=409,
        msg="Conflict",
        hdrs=None,
        fp=BytesIO(b'{"error": "a reseed is already running", "retry": true}'),
    )


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_it_retries_a_409_and_then_succeeds(ensure_demo, monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    def fake_urlopen(_request, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_409()
        return _Response(b'{"ok": true, "summary": {"suppliers": 17, "solicitations": 6, "awards": 6}}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    rc = ensure_demo._reseed_remotely("https://labs.example", "pat", None)

    assert rc == 0, "a 409 that clears must not fail the render"
    assert calls["n"] == 3, "it should have retried twice before succeeding"
    assert slept, "it should have waited between attempts rather than hammering"


def test_a_wedged_409_still_fails_rather_than_looping_forever(ensure_demo, monkeypatch):
    """Retrying is not the same as hanging: a reseed that never clears has to
    surface, or a stuck instance silently stalls every render behind it."""
    calls = {"n": 0}

    def always_409(_request, timeout=None):
        calls["n"] += 1
        raise _http_409()

    monkeypatch.setattr("urllib.request.urlopen", always_409)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    rc = ensure_demo._reseed_remotely("https://labs.example", "pat", None)

    assert rc == 1
    assert calls["n"] == 10, "bounded attempts, not an unbounded wait"


def test_a_401_is_not_retried(ensure_demo, monkeypatch):
    """An invalid token will not become valid by waiting."""
    calls = {"n": 0}

    def unauthorized(_request, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="http://x/", code=401, msg="Unauthorized", hdrs=None, fp=BytesIO(b'{"error": "bad token"}')
        )

    monkeypatch.setattr("urllib.request.urlopen", unauthorized)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    assert ensure_demo._reseed_remotely("https://labs.example", "pat", None) == 1
    assert calls["n"] == 1


def test_the_password_is_sent_when_given(ensure_demo, monkeypatch):
    """Reseeding also establishes the credential the render signs in with, so the
    password has to reach the body — not just the local environment."""
    seen = {}

    def capture(request, timeout=None):
        seen["body"] = request.data
        seen["auth"] = request.get_header("Authorization")
        return _Response(b'{"ok": true, "summary": {}}')

    monkeypatch.setattr("urllib.request.urlopen", capture)

    ensure_demo._reseed_remotely("https://labs.example", "the-pat", "known-password")

    assert b"known-password" in seen["body"]
    assert seen["auth"] == "Bearer the-pat"
