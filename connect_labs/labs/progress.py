"""Progress reporting primitives for long-running work (connect-labs#1220).

A cohort operation can run for many minutes. Returning nothing until the end is
indistinguishable, from a client's side, from a hang: MCP clients apply an
idle-output timeout (Claude Code's default is 300s) and kill the call. The work
had already succeeded server-side every time — what was lost was the RESULT,
which for the clone tools carries the resolved ``bundle_root`` and the
source->clone id mapping.

This module holds only the transport-agnostic half, so the domain code that
reports progress does not depend on the MCP layer that delivers it. The FastMCP
bridge lives in ``connect_labs.mcp.progress``.

Two rules the rest of the code depends on:

* **Progress is telemetry, never control flow.** ``safe_call`` swallows the
  callback's errors. A client that has gone away must not take down work that
  has already succeeded — that is the precise failure #1220 is about.
* **A missing reporter is normal.** Callers default to ``NULL_PROGRESS`` so they
  work unchanged from tests, management commands, and clients that sent no
  ``progressToken``.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class ProgressCallback(Protocol):
    """Sync callback invoked as work advances. May be called from a worker thread."""

    def __call__(self, progress: float, total: float | None = None, message: str | None = None) -> None:
        ...


def NULL_PROGRESS(progress: float, total: float | None = None, message: str | None = None) -> None:
    """Default reporter: does nothing. Named rather than a lambda so a traceback
    that somehow reaches it says what it is."""
    return None


def safe_call(progress: ProgressCallback | None, value: float, total: float | None, message: str) -> None:
    """Invoke ``progress`` without ever letting it raise into the caller.

    Call sites are inside per-item loops that isolate their own failures; a
    reporter that raised would defeat that isolation from the outside.
    """
    if progress is None:
        return
    try:
        progress(value, total, message)
    except Exception:  # noqa: BLE001 — see module docstring: telemetry, not control flow
        logger.debug("progress callback raised; continuing", exc_info=True)
