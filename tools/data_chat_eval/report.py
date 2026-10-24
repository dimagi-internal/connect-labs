"""The run's report: ``report.json`` (everything) and ``report.md`` (to read), plus findings.

A finding is one thing to fix, routed to where it is most likely fixed, with its evidence:
the check that failed and what it saw, the reply, and the screenshot to open.
"""

from __future__ import annotations

import json
from pathlib import Path

from .checks import blocked_route

JUDGE_FLOOR = 3.5  # a judge mean under this is a finding even when every gate passed


def status(entry: dict) -> str:
    if entry.get("blocked"):
        return "BLOCKED"
    if entry.get("truth_error"):
        return "ERROR"
    if not entry["checks"]:
        return "ERROR"
    gates_pass = all(r["passed"] for r in entry["checks"] if r["gate"])
    if entry.get("blocked_agent") and gates_pass:
        return "BLOCKED"  # everything it could check passed; the chat itself could not be had
    return "PASS" if all(r["passed"] for r in entry["checks"] if r["gate"]) else "FAIL"


def findings(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        sid = e["id"]
        if e.get("blocked"):
            out.append(
                {
                    "scenario": sid,
                    "severity": "blocker",
                    "what": f"no conversation: {e['blocked']}",
                    "route": blocked_route(e["blocked"]),
                    "evidence": e.get("blocked_shot") or "",
                }
            )
            continue
        if e.get("blocked_agent"):
            out.append(
                {
                    "scenario": sid,
                    "severity": "blocker",
                    "what": f"no conversation with the agent: {e['blocked_agent']}",
                    "route": blocked_route(e["blocked_agent"]),
                    "evidence": f"{sid}/",
                }
            )
        if e.get("truth_error"):
            out.append(
                {
                    "scenario": sid,
                    "severity": "fail",
                    "what": f"harness could not run it: {e['truth_error']}",
                    "route": "the scenario or the harness (tools/data_chat_eval)",
                }
            )
        for r in e["checks"]:
            if r["passed"]:
                continue
            spaced = e.get("surface") == "data_space"
            turn = e["turns"][r["turn"] - 1] if not spaced and r["turn"] - 1 < len(e["turns"]) else {}
            out.append(
                {
                    "scenario": sid,
                    "severity": "fail" if r["gate"] else "advisory",
                    "what": f"turn {r['turn']}: {r['name']} -- {r['detail']}",
                    "route": r["route"],
                    "evidence": f"{sid}/{(turn.get('screenshots') or {}).get('panel', '')}" if turn else f"{sid}/",
                    "reply": (turn.get("reply") or "")[:400],
                }
            )
        j = e.get("judge") or {}
        if j.get("mean") is not None and j["mean"] < JUDGE_FLOOR:
            out.append(
                {
                    "scenario": sid,
                    "severity": "judge",
                    "what": f"judge {j['mean']}/5: {j.get('verdict', '')}",
                    "problems": j.get("problems", []),
                    "route": j.get("fix", ""),
                    "evidence": f"{sid}/",
                }
            )
    order = {"blocker": 0, "fail": 1, "judge": 2, "advisory": 3}
    return sorted(out, key=lambda f: order.get(f["severity"], 9))


def write(run_dir: Path, meta: dict, entries: list[dict]) -> tuple[Path, Path]:
    for e in entries:
        e["status"] = status(e)
    found = findings(entries)
    data = {"meta": meta, "scenarios": entries, "findings": found}
    jpath = run_dir / "report.json"
    jpath.write_text(json.dumps(data, indent=1, default=str))
    mpath = run_dir / "report.md"
    mpath.write_text(_markdown(meta, entries, found))
    return mpath, jpath


def _markdown(meta: dict, entries: list[dict], found: list[dict]) -> str:
    counts = {s: sum(1 for e in entries if e["status"] == s) for s in ("PASS", "FAIL", "BLOCKED", "ERROR")}
    lines = [
        f"# Data chat eval -- {meta['started']}",
        "",
        f"As **{meta.get('labs_user', 'ace')}** on labs; suite `{meta['suite']}`; "
        f"{len(entries)} scenarios: **{counts['PASS']} pass, {counts['FAIL']} fail, "
        f"{counts['BLOCKED']} blocked, {counts['ERROR']} error**. Wall time {meta.get('seconds', '?')} s.",
        "",
        "| Scenario | Surface | Agent | Status | Gates | Judge (mean) | Turn time |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        gates = [r for r in e["checks"] if r["gate"]]
        passed = sum(1 for r in gates if r["passed"])
        j = e.get("judge") or {}
        secs = "/".join(str(t.get("seconds", "")) for t in e["turns"])
        lines.append(
            f"| {e['id']} | {e['surface']} | {e.get('agent', '')} | **{e['status']}** | {passed}/{len(gates)} | "
            f"{j.get('mean', j.get('error', '-') if j else '-')} | {secs} |"
        )
    lines += ["", "## Findings", ""]
    if not found:
        lines.append("None.")
    for i, f in enumerate(found, 1):
        lines.append(f"{i}. **[{f['severity']}] {f['scenario']}** -- {f['what']}")
        for p in f.get("problems", []):
            lines.append(f"   - {p}")
        if f.get("reply"):
            lines.append(f"   - reply: \"{_one_line(f['reply'])[:300]}\"")
        lines.append(f"   - fix in: {f['route']}")
        if f.get("evidence"):
            lines.append(f"   - evidence: `{f['evidence']}`")
    lines += ["", "## Scenarios", ""]
    for e in entries:
        lines += [f"### {e['id']} -- {e['title']} ({e['status']})", ""]
        lines.append(f"Page: {e['url']}  ")
        if e.get("covers"):
            lines.append(f"Covers: {e['covers']}  ")
        if e.get("blocked"):
            lines += [f"**Blocked:** {e['blocked']}", ""]
        if e.get("truth"):
            lines.append(f"Ground truth: `{json.dumps(e['truth'], default=str)}`  ")
        if e.get("page_state"):
            lines.append(f"Page state the agent got: `{json.dumps(e['page_state'], default=str)[:400]}`")
        lines.append("")
        for i, t in enumerate([] if e.get("surface") == "data_space" else e["turns"], 1):
            tools = ", ".join(c["short"] + ("!" if c.get("is_error") else "") for c in t.get("tool_calls", []))
            n_cards = len(t.get("cards", []))
            lines += [
                f"**Turn {i}** ({t.get('seconds')} s, tools: {tools or 'none seen'}, cards: {n_cards})",
                "",
                f"> {t['say']}",
                "",
                _quote(t.get("reply") or "(no reply)"),
                "",
            ]
            for r in [r for r in e["checks"] if r["turn"] == i]:
                mark = "PASS" if r["passed"] else ("FAIL" if r["gate"] else "warn")
                lines.append(f"- {mark} {r['name']}: {r['detail']}")
            shots = t.get("screenshots") or {}
            if shots.get("panel"):
                lines.append(f"- screenshots: `{e['id']}/{shots['panel']}`, `{e['id']}/{shots.get('page', '')}`")
            lines.append("")
        if e.get("surface") == "data_space":
            for r in e["checks"]:
                mark = "PASS" if r["passed"] else ("FAIL" if r["gate"] else "warn")
                lines.append(f"- step {r['turn']}: {mark} {r['name']}: {r['detail']}")
            lines.append("")
        j = e.get("judge") or {}
        if j:
            if "error" in j:
                lines.append(f"Judge: error -- {j['error']}")
            else:
                lines.append(
                    f"Judge: correct {j.get('correct')}, clear {j.get('clear')}, used page {j.get('used_page_well')}, "
                    f"card {j.get('card_readable')} -- {j.get('verdict', '')}"
                )
                for p in j.get("problems", []):
                    lines.append(f"- {p}")
                if j.get("fix"):
                    lines.append(f"- fix: {j['fix']}")
            lines.append("")
    return "\n".join(lines) + "\n"


def _one_line(s: str) -> str:
    return " ".join(s.split())


def _quote(s: str) -> str:
    return "\n".join("    " + ln for ln in s.splitlines())
