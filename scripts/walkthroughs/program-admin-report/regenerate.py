"""Synthetic generator entrypoint for the Program Admin Report walkthrough.

This is the single setup command for the demo — the one a canopy
``setup:`` block invokes before rendering. One run does all of:

1. **Generate** — load ``demo_config.json`` and call the
   ``program_admin_demo_seed`` MCP tool on labs. The tool executes
   server-side, inside the labs app, so the labs-only synthetic opps
   (10000/10001) are written through the local records backend on the
   labs DB — the only transport that actually reaches labs prod for
   synthetic opportunities.
2. **Verify** — run the ``_lib.verify`` smoke checks on the generation
   result (opps present, week counts, PAR run, in_progress week shape).
3. **Freshness preflight** — fetch the freshly-generated run pages over
   HTTP (labs session cookies) and compare the served ``render_code``
   against the local checkout's templates (AST-extracted). Aborts loudly
   when labs is serving stale template code — the 2-4 min ECS
   worker-cutover lag after a deploy "succeeds". Wait and re-run.
   ``SKIP_FRESHNESS=1`` bypasses the check (DANGEROUS — you'll record or
   grade a UI that doesn't match the code you think is live).
4. **Discover** — walk the PAR snapshot (``_lib.discovery``) to resolve
   the "good" (closed satisfactory) and "incomplete" (in-review /
   investigating) drill targets. This used to happen at record time in
   ``record_drill_through.py``; doing it at generation time means every
   downstream consumer (recorders, capture, the future canopy spec)
   reads the same resolved targets from the vars file.
5. **Emit a FLAT vars JSON** at ``.run_ids.json`` — every dynamic value
   the walkthrough needs: raw ids, path-relative URLs (the spec carries
   ``base_url``), and the archetype-derived FLW usernames used as click
   targets. String/number values only, no nesting. See the README's
   "Vars contract" section for the key list.

Requirements:

- ``LABS_MCP_TOKEN`` — a labs MCP PAT (mint at ``/labs/mcp/tokens/``;
  NOT the Connect OAuth token), or a configured ``connect_labs`` server
  in ``~/.claude.json``.
- A labs browser session file at ``~/.ace/labs-session.json`` (run
  ``/ace:labs-login``; override via ``LABS_SESSION_FILE``) — the run
  pages and the snapshot API are session-auth'd.

Usage::

    python scripts/walkthroughs/program-admin-report/regenerate.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.discovery import find_drill_targets  # noqa: E402
from walkthroughs._lib.freshness import (  # noqa: E402
    assert_served_current,
    served_render_code_from_html,
    skip_requested,
)
from walkthroughs._lib.labs_mcp import LabsMCPSession  # noqa: E402
from walkthroughs._lib.verify import report, run_checks  # noqa: E402

# ---------------------------------------------------------------------- #
# Path-relative URL builders (the spec carries base_url; vars carry paths)
# ---------------------------------------------------------------------- #


def _run_path(definition_id: int, run_id: int, opp_id: int) -> str:
    return f"/labs/workflow/{definition_id}/run/?run_id={run_id}&opportunity_id={opp_id}"


def _audit_path(audit_id: int, opp_id: int) -> str:
    return f"/audit/{audit_id}/?opportunity_id={opp_id}"


def _task_path(task_id: int, opp_id: int) -> str:
    return f"/tasks/{task_id}/edit/?opportunity_id={opp_id}"


def compute_week_window(completed_weeks: int, *, today: dt.date | None = None) -> tuple[list[str], str]:
    """Return ``(weeks, current_week)`` — ISO Mondays, computed from today.

    ``weeks`` is the trailing ``completed_weeks`` COMPLETE weeks (the PAR
    window — it always ends the Sunday before the current week, i.e. at
    the present). ``current_week`` is this week's Monday — the in-progress
    manager-flow run, deliberately OUTSIDE the PAR window so the live demo
    week never renders as a "NO RUN" hole in the grid.

    Dynamic on purpose: the demo previously told a hardcoded November-2025
    story while live-created records stamped today's date. Anchoring the
    window to now keeps seeded and live timestamps coherent forever.
    """
    today = today or dt.date.today()
    current_monday = today - dt.timedelta(days=today.weekday())
    weeks = [(current_monday - dt.timedelta(weeks=completed_weeks - i)).isoformat() for i in range(completed_weeks)]
    return weeks, current_monday.isoformat()


def _derive_manager_flagged_flw(config: dict) -> str | None:
    """The FLW the manager-flow scenes audit + coach live.

    Convention: in the opp that runs the in-progress CURRENT week, the FLW
    whose archetype flags in that week — trajectory index ``len(weeks)``
    (``jumoke_n`` in the shipped config). Derived from the config so the
    walkthrough spec never hardcodes an archetype-derived username.
    """
    if not config.get("current_week"):
        return None
    current_idx = len(config.get("weeks", []))
    for opp in config.get("opps", []):
        if not opp.get("in_progress_current_week"):
            continue
        for flw in opp.get("flws", []):
            if flw.get("archetype") == "solid":
                continue
            if current_idx in (flw.get("flag_week"), flw.get("second_flag_week")):
                return flw["id"]
    return None


def _labs_http_client() -> httpx.Client:
    """Session-cookie HTTP client for the labs run pages + snapshot API.

    Reuses the recorders' Playwright storage state (``/ace:labs-login``)
    — these endpoints are session-auth'd, so the MCP PAT won't do.
    """
    cookies = wcfg.session_cookies()
    if "sessionid" not in cookies:
        raise SystemExit(
            f"ERROR: no labs sessionid cookie in {wcfg.session_path()}. "
            "Run /ace:labs-login to refresh the labs session, then re-run."
        )
    return httpx.Client(timeout=60, cookies=cookies, follow_redirects=True)


def _check_freshness(client: httpx.Client, path: str, template_type: str, *, label: str) -> None:
    """Fetch a run page and assert it serves the local checkout's render_code."""
    if skip_requested():
        print(f"  !! SKIP_FRESHNESS=1 — skipping {template_type} freshness fetch ({label}). DANGEROUS.")
        return
    url = f"{wcfg.LABS_BASE_URL}{path}"
    resp = client.get(url)
    served = served_render_code_from_html(resp.text)
    if served is None:
        hint = ""
        if "login" in str(resp.url).lower() or resp.status_code in (401, 403):
            hint = " The labs session looks expired — run /ace:labs-login and retry."
        raise SystemExit(
            f"ERROR: could not read the served render_code from {url} "
            f"(status {resp.status_code}).{hint} "
            "(SKIP_FRESHNESS=1 bypasses this preflight — dangerous.)"
        )
    try:
        assert_served_current(served, template_type, label=label)
    except RuntimeError as e:
        raise SystemExit(str(e))


def main() -> int:
    config_path = HERE / "demo_config.json"
    config = json.loads(config_path.read_text())
    config.pop("_comment", None)

    # The week window is computed at generation time so the demo stays
    # current-dated forever: trailing N complete Mondays (the PAR window)
    # + the current week's Monday (the in-progress manager-flow run).
    completed_weeks = int(config.pop("completed_weeks", 4))
    weeks, current_week = compute_week_window(completed_weeks)
    config["weeks"] = weeks
    config["current_week"] = current_week
    print(f"Week window: {weeks[0]} .. {weeks[-1]} (completed) + {current_week} (current, in progress)")

    # ---------------- 1. Generate (server-side, via the MCP shim) -------- #
    print("Generating synthetic data via program_admin_demo_seed on labs...")
    with LabsMCPSession() as mcp:
        result, is_error = mcp.tool(
            "program_admin_demo_seed",
            {
                "weeks": config["weeks"],
                "current_week": config["current_week"],
                "opps": config["opps"],
                "cleanup_first": bool(config.get("cleanup_first", True)),
            },
        )
    if is_error or not isinstance(result, dict):
        print("program_admin_demo_seed ERROR:")
        print(json.dumps(result, indent=2, default=str)[:2000])
        return 1
    print(json.dumps(result, indent=2))

    # ---------------- 2. Verify the generation result -------------------- #
    print("\nRunning verify checks...")
    rc = report(run_checks(result, config))
    if rc:
        return rc

    # Resolve the base ids. The in-progress run is the CURRENT week,
    # outside the PAR window — the emitted var keys stay "wk4_*" because
    # the walkthrough spec references them (the name is historical: it
    # used to be the 4th window week, it is now the live 5th week).
    par = result["program_admin_report"]
    par_def_id = int(par["definition_id"])
    par_run_id = int(par["run_id"])
    primary_opp_id = int(config["opps"][0]["opportunity_id"])
    primary = next(opp for opp in result["opportunities"] if opp["opportunity_id"] == primary_opp_id)
    workflow_def_id = int(primary["workflow_definition_id"])
    current_wk = next((w for w in primary["weeks"] if w.get("in_progress")), None)

    par_url = _run_path(par_def_id, par_run_id, primary_opp_id)
    wk4_url = _run_path(workflow_def_id, int(current_wk["run_id"]), primary_opp_id) if current_wk else None

    client = _labs_http_client()

    # ---------------- 3. Freshness preflight ----------------------------- #
    # Abort loudly if labs is serving stale template code (the 2-4 min ECS
    # worker-cutover lag): regeneration stamps each def's render_code from
    # the template the *running* worker has, so a stale worker writes stale
    # JSX. Wait for the cutover, then re-run this generator.
    print("\nFreshness preflight (served render_code vs local checkout)...")
    _check_freshness(client, par_url, "program_admin_report", label="PAR run page")
    if wk4_url:
        _check_freshness(client, wk4_url, "chc_nutrition_analysis", label="current-week in_progress run page")
    else:
        print("  ! no in_progress current week configured — skipping chc_nutrition_analysis check")

    # ---------------- 4. Discover drill targets --------------------------- #
    # The PAR-snapshot walk used to run at record time (record_drill_through);
    # at generation time every consumer reads the same resolved targets.
    print("\nDiscovering drill targets from the PAR snapshot...")
    targets = find_drill_targets(
        client.get,
        par_run_id,
        labs_base_url=wcfg.LABS_BASE_URL,
        primary_opp_id=primary_opp_id,
    )
    good = targets["good"]
    bad = targets["incomplete"]
    print(
        f"  good:       {good['opp_label']} Wk{good['week_idx'] + 1} "
        f"flw={good['flw_id']}  audit #{good['audit_id']}, task #{good['task_id']}"
    )
    print(
        f"  incomplete: {bad['opp_label']} Wk{bad['week_idx'] + 1} "
        f"flw={bad['flw_id']}  audit #{bad['audit_id']}, task #{bad['task_id']}"
    )

    # ---------------- 5. Emit the FLAT vars JSON --------------------------- #
    # Contract: string/number values only — a canopy setup.outputs file the
    # walkthrough spec interpolates as ${var}. URLs are path-relative (the
    # spec carries base_url). FLW usernames are archetype-derived here so
    # the spec never hardcodes them.
    vars_json = {
        # Raw ids (recorders require par_run_id / wk4_run_id / opp_id /
        # workflow_def_id — keep those names stable).
        "par_def_id": par_def_id,
        "par_run_id": par_run_id,
        "opp_id": primary_opp_id,
        "workflow_def_id": workflow_def_id,
        # Path-relative URLs.
        "par_url": par_url,
        "chc_good_url": _run_path(good["wf_def_id"], good["run_id"], good["opp_id"]),
        "audit_good_url": _audit_path(good["audit_id"], good["opp_id"]),
        "task_good_url": _task_path(good["task_id"], good["opp_id"]),
        "audit_incomplete_url": _audit_path(bad["audit_id"], bad["opp_id"]),
        "task_incomplete_url": _task_path(bad["task_id"], bad["opp_id"]),
        # Drill targets — grid-cell coordinates + click-target ids.
        "good_opp_id": good["opp_id"],
        "good_opp_label": good["opp_label"],
        "good_week_idx": good["week_idx"],
        "good_run_id": good["run_id"],
        "good_audit_id": good["audit_id"],
        "good_task_id": good["task_id"],
        "incomplete_opp_id": bad["opp_id"],
        "incomplete_opp_label": bad["opp_label"],
        "incomplete_week_idx": bad["week_idx"],
        "incomplete_run_id": bad["run_id"],
        "incomplete_audit_id": bad["audit_id"],
        "incomplete_task_id": bad["task_id"],
        # Archetype-derived FLW usernames used as click targets.
        "flagged_flw_good": good["flw_id"],
        "flagged_flw_incomplete": bad["flw_id"],
    }
    if current_wk:
        # Key names are historical ("wk4" = the old 4th window week); the
        # run they point at is the CURRENT week's in-progress manager run.
        # Kept stable because the walkthrough spec interpolates them.
        vars_json["wk4_run_id"] = int(current_wk["run_id"])
        vars_json["wk4_url"] = wk4_url
    flagged_manager = _derive_manager_flagged_flw(config)
    if flagged_manager:
        vars_json["flagged_flw_manager"] = flagged_manager

    written = wcfg.write_run_ids(HERE, vars_json)
    print(f"\nWrote {written}:")
    for k, v in vars_json.items():
        print(f"  {k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
