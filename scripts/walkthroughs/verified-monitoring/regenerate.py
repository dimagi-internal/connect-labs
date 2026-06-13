"""Regenerate the Verified Monitoring (N1) demo and seed it onto synthetic opp 10008.

Thin wire: read ``demo_config.json`` -> generate per-round, row-level survey
records and compute all KPIs via ``survey_sim.build_state`` (which uses the
shared ``commcare_connect.labs.synthetic.generator.core.survey_quality`` library) -> create a workflow run
on the synthetic opp via the ``connect_labs`` MCP. The dashboard render
(``commcare_connect/workflow/templates/verified_monitoring_render.js``) reads the
resulting ``instance.state`` and never fetches.

Opp 10008 is a labs-only synthetic opp, so ``workflow_create_run`` routes
in-process to the local records backend (no prod data, no HTTP permission checks).

Usage::

    export LABS_MCP_TOKEN=...        # or it is read from ~/.claude.json
    python scripts/walkthroughs/verified-monitoring/regenerate.py

Writes ``.run_ids.json`` — a FLAT vars JSON (run_id, opp_id,
workflow_def_id, runner_path, runner_url). This script is the demo's
synthetic-generator entrypoint: the ``setup:`` block in
``docs/walkthroughs/verified-monitoring.yaml`` runs it per render and the
spec interpolates the vars (e.g. ``goto: ${runner_path}``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from survey_sim import build_state, summarize  # noqa: E402

MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")


def _token() -> str:
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    # Fall back to the connect_labs server entry. Claude Code stores MCP servers
    # in ~/.claude.json (mcpServers.*); the older standalone ~/.claude/mcp.json
    # is checked too for compatibility.
    for cfg in (Path.home() / ".claude.json", Path.home() / ".claude" / "mcp.json"):
        if not cfg.exists():
            continue
        data = json.loads(cfg.read_text())
        servers = data.get("mcpServers", data.get("servers", {}))
        for name, spec in servers.items():
            if "connect_labs" in name or "labs" in name:
                hdrs = spec.get("headers", {}) or {}
                auth = hdrs.get("Authorization", hdrs.get("authorization", ""))
                if auth.startswith("Bearer "):
                    return auth[len("Bearer ") :]
    sys.exit("No MCP token: set LABS_MCP_TOKEN or configure connect_labs in ~/.claude.json")


# ----- minimal MCP client (also imported by push_render.py) -----


def _parse(r):
    for ln in r.text.splitlines():
        if ln.startswith("data:"):
            try:
                return json.loads(ln[5:].strip())
            except Exception:
                pass
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:400]}


def _call(c, h, name, args):
    r = c.post(
        MCP_URL,
        headers=h,
        json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args}},
    )
    res = _parse(r).get("result", {})
    sc = res.get("structuredContent")
    if sc is not None:
        return sc, res.get("isError")
    cont = res.get("content")
    if isinstance(cont, list) and cont and "text" in cont[0]:
        try:
            return json.loads(cont[0]["text"]), res.get("isError")
        except Exception:
            return cont[0]["text"], res.get("isError")
    return res, res.get("isError")


def _session(c, headers):
    r = c.post(
        MCP_URL,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "regen", "version": "1"},
            },
        },
    )
    sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
    h = dict(headers)
    if sid:
        h["Mcp-Session-Id"] = sid
    c.post(MCP_URL, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return h


def _fetch_rounds_plans(c, h, program_id: int) -> dict:
    """Live-fetch each study's sampled plans (per arm) via the microplans MCP read
    tools, so survey GPS can be grounded on the real primary/alternate footprints.

    Returns ``{round_idx: {"treatment": {...}, "comparison": {...}}}`` (round index
    parsed from the study group's ``R<n>`` name). Empty dict on any failure — the
    generator then scatters in-ward, so a missing/undeployed read path degrades
    gracefully instead of breaking the demo."""
    overview, err = _call(c, h, "microplans_list_plans", {"program_id": program_id})
    if err or not isinstance(overview, dict) or "groups" not in overview:
        print("  microplans_list_plans unavailable -> in-ward scatter:", str(overview)[:160])
        return {}
    plans_by_id = {p["id"]: p for p in overview.get("plans", [])}
    rounds: dict = {}
    for g in overview.get("groups", []):
        if g.get("kind") != "study":
            continue
        m = re.search(r"R(\d+)", g.get("name", ""))
        if not m:
            continue
        ri = int(m.group(1)) - 1
        roles: dict = {}
        for pid_s, arm in (g.get("arm_for") or {}).items():
            role = (
                "treatment"
                if arm in ("intervention", "treatment")
                else ("comparison" if arm in ("control", "comparison") else None)
            )
            if role is None:
                continue
            pid = int(pid_s)
            wa, werr = _call(c, h, "microplans_plan_work_areas", {"program_id": program_id, "plan_id": pid})
            if werr or not isinstance(wa, dict) or not wa.get("work_areas"):
                continue
            p = plans_by_id.get(pid, {})
            roles[role] = {"plan_id": pid, "ward": p.get("region") or p.get("name"), "work_areas": wa["work_areas"]}
        if roles:
            rounds[ri] = roles
    return rounds


def main() -> int:
    cfg = json.loads((HERE / "demo_config.json").read_text())
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}

    opp, wf = cfg["opportunity_id"], cfg["workflow_def_id"]
    token = _token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=600) as c:
        h = _session(c, headers)
        # Guarantee the study plans exist (idempotent; re-run is a no-op), then
        # live-fetch them so survey GPS grounds on real footprints. The labs-only
        # program id IS the opportunity id (positive, >= 10_000 floor).
        ens, eerr = _call(c, h, "microplans_study_ensure", {"generate": True})
        if eerr:
            print("  microplans_study_ensure unavailable (continuing):", str(ens)[:160])
        else:
            print(f"  study ensure: {json.dumps(ens, default=str)[:200]}")
        rounds_plans = _fetch_rounds_plans(c, h, opp)
        grounded = sorted(ri + 1 for ri in rounds_plans)
        print(f"grounded rounds (on live plans of program {opp}): {grounded or 'none -> in-ward scatter'}")
        state, records = build_state(cfg, HERE, rounds_plans=rounds_plans)
        print(f"generated {len(records)} records across {len(state['rounds'])} rounds")
        print(summarize(state))

        run, err = _call(
            c, h, "workflow_create_run", {"definition_id": wf, "opportunity_id": opp, "initial_state": state}
        )
        if err:
            print("workflow_create_run ERROR:", json.dumps(run, default=str)[:400])
            return 1
        run_id = run.get("run_id") if isinstance(run, dict) else None
        # FLAT vars JSON (string/number values only) — the canopy setup block
        # points its `outputs:` at this file and the walkthrough spec
        # interpolates the keys as ${var}. `runner_path` is path-relative
        # (the spec carries base_url); `runner_url` stays absolute for
        # humans pasting it into a browser.
        runner_path = f"/labs/workflow/{wf}/run/?opportunity_id={opp}&run_id={run_id}"
        runner_url = f"https://labs.connect.dimagi.com{runner_path}"
        (HERE / ".run_ids.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "opp_id": opp,
                    "workflow_def_id": wf,
                    "runner_path": runner_path,
                    "runner_url": runner_url,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"\nrun_id={run_id}\n{runner_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
