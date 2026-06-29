"""Pre-render ensure-and-sweep for the create-survey-solicitation walkthrough.

The walkthrough films a **fully fresh lifecycle on camera**: scene 2 clicks
"Create Solicitation" (minting a brand-new ``type=solicitation`` record on program
10008), scene 3 submits a response, scene 4 reads the response id off the page, and
scene 5 awards it — all threaded through the canopy ``capture`` action
(``${solicitation_id}`` / ``${response_id}``). There is no fixed canonical record.

What this script does, per render, **before** the recorder starts:

1. **Ensure** the R6 — Attakar × Gura study group **4492** and its plan **4494**
   exist on program 10008 (the study-design demo seeds them). If either is
   missing, ERROR loudly — scenes 1-2 depend on them (the portfolio's "ready to
   solicit" group card and the create form's snapshotted coverage map).
2. **Sweep** EVERY ``type=solicitation`` record on program 10008 whose
   ``data.source_group_id == 4492`` (every call the walkthrough has ever minted),
   **and** all of their responses (``delete_solicitation`` with ``force=True``
   cascades the child responses + reviews). Nothing is kept — each render mints
   its own fresh call, so the prior render's call + responses are cleared before
   the next is minted, keeping the portfolio from accumulating duplicate R6 calls.

**Transport — the MCP, not AWS/ECS.** Records on program 10008 are labs-only
synthetic records (opp id >= 10_000) living in the labs prod DB behind the
local-records backend. As of PR #678 the ``connect_labs`` MCP tools route
labs-only opps to that backend and grant access to opted-in callers, so this
script talks to the MCP over HTTP (same transport as the verified-monitoring
``regenerate.py`` seeder) instead of firing a one-off ``aws ecs run-task``. No AWS
session is required.

Requirements:
- A labs MCP token: ``LABS_MCP_TOKEN`` env, or a ``connect_labs`` server configured
  in ``~/.claude.json`` (Claude Code writes it there). The caller's user must have
  ``view_synthetic_opps`` enabled (``synthetic_set_my_visibility``) — labs-only
  access is gated on the opt-in, not on AWS.

Usage::

    python scripts/walkthroughs/create-survey-solicitation/ensure_demo.py

Env overrides: LABS_MCP_URL, LABS_MCP_TOKEN.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

# Make the shared seeder MCP client importable whether run as a module or a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_client import call, session, token  # noqa: E402

PROGRAM_ID = 10008
SOURCE_GROUP_ID = 4492  # R6 — Attakar × Gura study group scenes 1-2 solicit from
SOURCE_PLAN_ID = 4494  # the R6 plan snapshotted as the coverage area


def _ids(records, key="id"):
    out = set()
    for r in records or []:
        v = r.get(key) if isinstance(r, dict) else None
        if v is not None:
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                pass
    return out


def _sgid(sol: dict):
    data = sol.get("data") or {}
    v = data.get("source_group_id", sol.get("source_group_id"))
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    with httpx.Client(timeout=600) as c:
        h = session(c, token())

        # (1) Ensure the study-design demo seeds (group 4492 + plan 4494) exist.
        plans_res, perr = call(c, h, "microplans_list_plans", {"program_id": PROGRAM_ID})
        if perr or not isinstance(plans_res, dict):
            print(
                f"[ensure_demo] FAILED: microplans_list_plans({PROGRAM_ID}) error: " f"{str(plans_res)[:300]}",
                file=sys.stderr,
            )
            return 1
        plan_ids = _ids(plans_res.get("plans"))
        group_ids = _ids(plans_res.get("groups"), key="group_id")
        group_present = SOURCE_GROUP_ID in group_ids
        plan_present = SOURCE_PLAN_ID in plan_ids
        if not group_present or not plan_present:
            print(
                f"[ensure_demo] FAILED: seed missing on program {PROGRAM_ID} "
                f"(group {SOURCE_GROUP_ID} present={group_present}, "
                f"plan {SOURCE_PLAN_ID} present={plan_present}). "
                "Run the study-design demo seeder.",
                file=sys.stderr,
            )
            return 1

        # (2) Sweep every R6 call this walkthrough has minted + their responses.
        sols_res, serr = call(c, h, "list_solicitations", {"program_id": PROGRAM_ID})
        if serr or not isinstance(sols_res, dict):
            print(f"[ensure_demo] FAILED: list_solicitations error: {str(sols_res)[:300]}", file=sys.stderr)
            return 1
        doomed = [s for s in (sols_res.get("solicitations") or []) if _sgid(s) == SOURCE_GROUP_ID]
        deleted = []
        for s in doomed:
            sid = s.get("id")
            _, derr = call(
                c, h, "delete_solicitation", {"solicitation_id": sid, "program_id": PROGRAM_ID, "force": True}
            )
            if derr:
                print(f"[ensure_demo] FAILED: delete_solicitation({sid}) error", file=sys.stderr)
                return 1
            deleted.append(sid)

        # Re-read to confirm nothing source_group_id==4492 survives.
        after_res, aerr = call(c, h, "list_solicitations", {"program_id": PROGRAM_ID})
        remaining = (
            [s.get("id") for s in (after_res.get("solicitations") or []) if _sgid(s) == SOURCE_GROUP_ID]
            if isinstance(after_res, dict)
            else ["<reread-failed>"]
        )
        if remaining:
            print(
                f"[ensure_demo] FAILED: {len(remaining)} source_group_id={SOURCE_GROUP_ID} "
                f"solicitations survived the sweep: {remaining}",
                file=sys.stderr,
            )
            return 1

        print(
            f"[ensure_demo] OK — group {SOURCE_GROUP_ID}/plan {SOURCE_PLAN_ID} present on "
            f"program {PROGRAM_ID}; swept {len(deleted)} prior R6 solicitation(s) "
            f"{deleted} + their responses. Scene 2 mints a fresh call on camera.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
