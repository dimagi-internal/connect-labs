"""Read a canopy conversation back, as ace (its canopy PAT), to know exactly what happened.

The panel shows the words; canopy's session API also says when the turn finished
(``turn_status``) and which tools the agent called with what input. ace started the
conversation (labs vouched for ace@dimagi-ai.com, the canopy user its PAT belongs to), so
the PAT may read it. A SENSITIVE conversation's words are sealed to its people and come
back as metadata only; the harness then falls back to the panel's text.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

DEFAULT_BASE = "https://canopy.dimagi.com"
ENV_FILES = (Path.home() / ".ace" / ".env", Path.home() / ".claude" / "plugins" / "data" / "ace-ace" / ".env")
#: turn_status.state values after which nothing more will arrive for the turn.
TERMINAL = {"done", "failed", "error", "cancelled", "canceled", "stopped", "timed_out"}


def ace_canopy_token() -> str | None:
    if os.environ.get("CANOPY_WEB_PAT"):
        return os.environ["CANOPY_WEB_PAT"]
    for path in ENV_FILES:
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith("CANOPY_WEB_PAT="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
    return None


class CanopyAPI:
    def __init__(self, token: str | None = None, base: str = DEFAULT_BASE):
        token = token or ace_canopy_token()
        self.enabled = bool(token)
        self.base = base.rstrip("/")
        self._http = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"} if token else {})

    def me(self) -> dict | None:
        if not self.enabled:
            return None
        resp = self._http.get(f"{self.base}/api/me/")
        return resp.json() if resp.status_code == 200 else None

    def session(self, session_id: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            resp = self._http.get(f"{self.base}/api/canopy-sessions/{session_id}", params={"full": "true"})
        except httpx.HTTPError:
            return None
        return resp.json() if resp.status_code == 200 else None


def _asked_at(session: dict, user_text: str, ordinal: int | None) -> int | None:
    """Index of the message that asked ``user_text``: by its words, or -- in a sealed
    conversation, whose words canopy does not return -- by its place among the person's
    messages (``ordinal``, 0-based)."""
    messages = session.get("messages") or []
    found = None
    for i, m in enumerate(messages):
        if m.get("role") == "user" and (m.get("plaintext") or "").strip() == user_text.strip():
            found = i
    if found is None and ordinal is not None and session.get("sensitive"):
        asked = [i for i, m in enumerate(messages) if m.get("role") == "user" and _human(m)]
        found = asked[ordinal] if ordinal < len(asked) else None
    return found


def _human(m: dict) -> bool:
    origin = ((m.get("content") or {}).get("record") or {}).get("turn_origin")
    return origin in (None, "human")


def turn_finished(session: dict, user_text: str, ordinal: int | None = None) -> bool:
    """Whether the turn that answers ``user_text`` (the latest such message) is over."""
    if _asked_at(session, user_text, ordinal) is None:
        return False  # our message has not landed yet
    status = session.get("turn_status") or {}
    return status.get("state") in TERMINAL and bool(status.get("settled", True)) and not session.get("running")


def turn_slice(session: dict, user_text: str, ordinal: int | None = None) -> list[dict]:
    """The messages of the turn that answers ``user_text``: everything after it."""
    messages = session.get("messages") or []
    start = _asked_at(session, user_text, ordinal)
    if start is None:
        return []
    out = []
    for m in messages[start + 1 :]:
        if m.get("role") == "user" and _human(m):
            break
        out.append(m)
    return out


def tool_calls(turn: list[dict]) -> list[dict]:
    """``[{name, short, input, is_error, result}]`` for the turn, short = the tool's own name."""
    results = {}
    for m in turn:
        if m.get("role") == "tool_result":
            content = m.get("content") or {}
            results[content.get("tool_use_id")] = (bool(content.get("is_error")), m.get("plaintext") or "")
    calls = []
    for m in turn:
        if m.get("role") != "tool_use":
            continue
        content = m.get("content") or {}
        name = content.get("name") or ""
        is_error, result = results.get(content.get("id"), (False, ""))
        calls.append(
            {
                # A sealed conversation keeps the tool's name, never its input or result.
                "sealed": content.get("redacted") == "sensitive",
                "name": name,
                "short": name.rsplit("__", 1)[-1],
                "input": content.get("input"),
                "is_error": is_error,
                "result": result[:2000],
            }
        )
    return calls


def reply_text(turn: list[dict]) -> str:
    return "\n\n".join((m.get("plaintext") or "").strip() for m in turn if m.get("role") == "assistant").strip()
