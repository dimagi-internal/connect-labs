"""Walkthrough config plumbing — session path, run id handoff.

This is the contract between ``regenerate.py`` (the synthetic seeder
wrapper) and the recording scripts. Both ends read the same defaults,
so a typo in one isn't a recording-time surprise.

Two pieces:

1. ``session_path()`` — where Playwright reads the labs OAuth state.
   Defaults to ``~/.ace/labs-session.json``, overridable via
   ``LABS_SESSION_FILE``. The previous recorders hardcoded
   ``/Users/acedimagi/.ace/labs-session.json`` and would silently fail
   on any other machine.

2. ``run_ids.json`` — written by regenerate.py next to ``demo_config.json``,
   read by recorders at startup. Holds the freshly-generated PAR run id,
   the Wk4 in_progress run id, the opp id, and the chc_nutrition workflow
   def id. Recorders that try to fall back to a stale int constant
   silently re-record yesterday's data — this file plus its mtime check
   makes that impossible.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

LABS_BASE_URL = os.environ.get("LABS_BASE_URL", "https://labs.connect.dimagi.com")
DEFAULT_SESSION_PATH = Path.home() / ".ace" / "labs-session.json"

# How old run_ids.json is allowed to be before the recorder shouts. The
# point is just to flag "you regenerated yesterday and forgot today" —
# not to enforce a hard ceiling. 24h is generous; the seeded data backdates
# completed_at so the actual records stay valid for weeks.
RUN_IDS_STALE_AFTER = dt.timedelta(hours=24)


def session_path() -> Path:
    """Return the path to the labs Playwright storage state JSON.

    Override with ``LABS_SESSION_FILE`` for non-default locations
    (CI, alternate logins, …).
    """
    override = os.environ.get("LABS_SESSION_FILE")
    if override:
        return Path(override).expanduser()
    return DEFAULT_SESSION_PATH


def require_session_file() -> Path:
    p = session_path()
    if not p.exists():
        raise SystemExit(
            f"ERROR: labs session file not found at {p}. Run ace:labs-login "
            "(or set LABS_SESSION_FILE) before recording."
        )
    return p


def run_ids_path(walkthrough_dir: Path) -> Path:
    return walkthrough_dir / ".run_ids.json"


def write_run_ids(walkthrough_dir: Path, ids: dict) -> Path:
    """Persist the seeded run ids next to demo_config.json.

    ``ids`` should at minimum include the keys the recorders consume —
    typically ``par_run_id``, ``wk4_run_id``, ``opp_id``,
    ``workflow_def_id`` (PAR walkthrough). Extra keys are fine.
    """
    p = run_ids_path(walkthrough_dir)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        **ids,
    }
    p.write_text(json.dumps(payload, indent=2) + "\n")
    return p


def read_run_ids(walkthrough_dir: Path, *, required: list[str] | None = None) -> dict:
    """Read .run_ids.json and validate freshness + required keys.

    Hard-fails on missing file, missing keys, or stale data. Recorders
    should always call this rather than fall back to environment-variable
    defaults; the previous "default to int constant 1774" pattern silently
    recorded against last week's snapshot when the user forgot to
    regenerate.
    """
    p = run_ids_path(walkthrough_dir)
    if not p.exists():
        raise SystemExit(
            f"ERROR: {p} not found. Run regenerate.py first to seed synthetic "
            "data and emit the run ids the recorder needs."
        )
    payload = json.loads(p.read_text())
    generated_at = payload.get("generated_at")
    if generated_at:
        try:
            ts = dt.datetime.fromisoformat(generated_at)
            age = dt.datetime.now(dt.timezone.utc) - ts
            if age > RUN_IDS_STALE_AFTER:
                hours = age.total_seconds() / 3600
                print(
                    f"WARNING: .run_ids.json is {hours:.1f}h old "
                    f"(generated_at={generated_at}). Re-run regenerate.py "
                    "if the synthetic state may have drifted."
                )
        except ValueError:
            pass
    if required:
        missing = [k for k in required if k not in payload]
        if missing:
            raise SystemExit(f"ERROR: {p} is missing required keys: {missing}. " "Re-run regenerate.py.")
    return payload
