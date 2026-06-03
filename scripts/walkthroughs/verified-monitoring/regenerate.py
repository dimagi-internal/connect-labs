"""Regenerate the Verified Monitoring (N1) demo from demo_config.json.

Builds the full dashboard state payload — coverage series, verification,
self-report premium, service-delivery counts, and the two-ward map overlay
GeoJSON (sampled deterministically from ``rng_seed``) — and creates a workflow
run on the synthetic opp via the ``connect_labs`` MCP. The dashboard render
(``commcare_connect/workflow/templates/verified_monitoring_render.js``) reads
this state from ``instance.state`` and never fetches.

This is the durable home for the demo recipe — do NOT seed from ad-hoc /tmp
scripts. Opp 10008 is a labs-only synthetic opp, so ``workflow_create_run``
routes in-process to the local records backend (no prod data, no HTTP
permission checks).

Usage::

    # From a connect-labs checkout, with the labs venv active.
    # Needs an MCP token (see docs/MCP_SETUP.md / `/labs-token-setup`):
    export LABS_MCP_TOKEN=...        # or it is read from ~/.claude/mcp.json
    python scripts/walkthroughs/verified-monitoring/regenerate.py

Writes ``scripts/walkthroughs/verified-monitoring/.run_ids.json`` with:

    run_id            — the verified_monitoring run to point the dashboard at
    opp_id            — synthetic opportunity id (10008)
    workflow_def_id   — verified_monitoring workflow definition id (3699)
    runner_url        — full URL to open the dashboard
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
MCP_URL = os.environ.get("LABS_MCP_URL", "https://labs.connect.dimagi.com/mcp/")


def _token() -> str:
    tok = os.environ.get("LABS_MCP_TOKEN")
    if tok:
        return tok
    # Fall back to the connect_labs server entry in ~/.claude/mcp.json.
    cfg = Path.home() / ".claude" / "mcp.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        servers = data.get("mcpServers", data.get("servers", {}))
        for name, spec in servers.items():
            if "connect_labs" in name or "labs" in name:
                hdrs = spec.get("headers", {})
                auth = hdrs.get("Authorization", "")
                if auth.startswith("Bearer "):
                    return auth[len("Bearer ") :]
    sys.exit("No MCP token: set LABS_MCP_TOKEN or configure connect_labs in ~/.claude/mcp.json")


# ----- deterministic geometry helpers -----


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _inside(poly, x, y):
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _sample(rng, poly, n):
    x0, y0, x1, y1 = _bbox(poly)
    pts = []
    guard = 0
    while len(pts) < n and guard < n * 50:
        guard += 1
        x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if _inside(poly, x, y):
            pts.append([round(x, 5), round(y, 5)])
    return pts


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _pt(x, y, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": props}


def _poly_feature(coords, ward):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords + [coords[0]]]},
        "properties": {"ward": ward},
    }


def build_payload(cfg: dict) -> dict:
    rng = random.Random(cfg["rng_seed"])
    prog = cfg["program"]
    tw, cw = prog["treatment_ward"], prog["control_ward"]
    polys = cfg["ward_polygons"]
    colors = cfg["pin_colors"]

    sd_pts = _sample(rng, polys[tw], cfg["service_delivery_sample"])
    service_delivery = _fc([_pt(x, y, {}) for x, y in sd_pts])

    def pins(ward):
        spec = cfg["survey_pins"][ward]
        feats = []
        for x, y in _sample(rng, polys[ward], spec["n"]):
            confirmed = rng.random() < spec["confirmed_rate"]
            feats.append(
                _pt(x, y, {"color": colors["confirmed"] if confirmed else colors["absent"], "confirmed": confirmed})
            )
        return feats

    survey_pins = _fc(pins(tw) + pins(cw))
    rounds = cfg["coverage_rounds"]
    t = rounds["intervention"]
    c = rounds["comparison"]
    return {
        "program": prog,
        "coverage": {
            "rounds": [r["round"] for r in t],
            "by_arm": rounds,
            "gap_series": [
                {"round": t[i]["round"], "gap_pp": round(t[i]["coverage_pct"] - c[i]["coverage_pct"], 1)}
                for i in range(len(t))
            ],
            "latest": cfg["latest"],
        },
        "verification": cfg["verification"],
        "self_report": cfg["self_report"],
        "service_delivery_counts": cfg["service_delivery_counts"],
        "overlay": {
            "ward_boundaries": _fc([_poly_feature(polys[tw], tw), _poly_feature(polys[cw], cw)]),
            "service_delivery": service_delivery,
            "survey_pins": survey_pins,
        },
    }


# ----- minimal MCP client -----


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


def main() -> int:
    cfg = json.loads((HERE / "demo_config.json").read_text())
    cfg.pop("_comment", None)
    payload = build_payload(cfg)
    ov = payload["overlay"]
    purple = sum(1 for f in ov["survey_pins"]["features"] if f["properties"]["confirmed"])
    print(
        f"payload: SD={len(ov['service_delivery']['features'])} pts · "
        f"pins={len(ov['survey_pins']['features'])} ({purple} confirmed) · "
        f"{len(payload['coverage']['rounds'])} rounds"
    )

    opp, wf = cfg["opportunity_id"], cfg["workflow_def_id"]
    token = _token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=180) as c:
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
        run, err = _call(
            c, h, "workflow_create_run", {"definition_id": wf, "opportunity_id": opp, "initial_state": payload}
        )
        if err:
            print("workflow_create_run ERROR:", json.dumps(run, default=str)[:400])
            return 1
        run_id = run.get("run_id") if isinstance(run, dict) else None
        runner_url = f"https://labs.connect.dimagi.com/labs/workflow/{wf}/run/?opportunity_id={opp}&run_id={run_id}"
        (HERE / ".run_ids.json").write_text(
            json.dumps({"run_id": run_id, "opp_id": opp, "workflow_def_id": wf, "runner_url": runner_url}, indent=2)
            + "\n"
        )
        print(f"run_id={run_id}\n{runner_url}\nwrote {HERE / '.run_ids.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
