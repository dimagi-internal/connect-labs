"""Run the data chat eval: ``python -m tools.data_chat_eval [run] [options]``.

Steps: preflight (ace's labs session, labs PAT, canopy PAT), prepare (load each page's
synthetic opportunities into labs' visit cache, one at a time), ground truth (``data_query``
as ace), drive each scenario in a headless browser, check, judge, report. See README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SUITE = HERE / "scenarios" / "kmc.yaml"
RUNS = HERE / "runs"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.data_chat_eval", description=__doc__)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "list", "preflight"])
    ap.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    ap.add_argument("--only", nargs="*", help="scenario ids (or prefixes) to run")
    ap.add_argument(
        "--surface",
        choices=["workflow_run", "data_page", "data_space", "containment"],
        help="only scenarios on this surface",
    )
    ap.add_argument("--parallel", type=int, default=2, help="scenarios at once (each is its own conversation)")
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--turn-timeout", type=int, default=480)
    ap.add_argument("--skip-prepare", action="store_true", help="do not (re)load the visit cache")
    ap.add_argument("--out", type=Path, default=None, help="run directory (default runs/<timestamp>)")
    ap.add_argument("--no-spaces", action="store_true", help="skip the data space scenarios")
    ap.add_argument("--no-containment", action="store_true", help="skip the direct-MCP containment probes")
    args = ap.parse_args(argv)

    import yaml

    from . import canopy_api, containment, labs_mcp, spaces, suite

    mcp = labs_mcp.LabsMCP()
    scenarios = suite.load(args.suite, mcp)
    raw_suite = yaml.safe_load(args.suite.read_text())
    space_scenarios = [] if args.no_spaces else spaces.load(raw_suite)
    probe_scenarios = [] if args.no_containment else containment.load(raw_suite)
    if args.only:
        scenarios = [s for s in scenarios if any(s.id == o or s.id.startswith(o) for o in args.only)]
        space_scenarios = [s for s in space_scenarios if any(s.id == o or s.id.startswith(o) for o in args.only)]
        probe_scenarios = [s for s in probe_scenarios if any(s.id == o or s.id.startswith(o) for o in args.only)]
    if args.surface:
        scenarios = [s for s in scenarios if s.page.surface == args.surface]
        space_scenarios = space_scenarios if args.surface == "data_space" else []
        probe_scenarios = probe_scenarios if args.surface == "containment" else []
    if args.command == "list":
        for s in scenarios:
            print(f"{s.id:28} {s.page.surface:13} {len(s.turns)} turn(s)  {s.title}\n{'':28} {s.page.url}")
        for s in space_scenarios:
            print(f"{s.id:28} {'data_space':13} 7 steps     {s.title}\n{'':28} sources {s.sources}")
        for s in probe_scenarios:
            print(f"{s.id:28} {'containment':13} probes      {s.title}\n{'':28} program {s.program_id}")
        return 0

    problems, who = preflight(mcp, canopy_api.CanopyAPI())
    for p in problems:
        print(f"preflight: {p}", file=sys.stderr)
    if args.command == "preflight" or any(p.startswith("FATAL") for p in problems):
        return 1 if problems else 0

    started = datetime.now()
    run_dir = args.out or RUNS / started.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"run dir: {run_dir}")

    if not args.skip_prepare:
        prepare(mcp, scenarios)
    for s in scenarios:
        if s.truth_sql:
            suite.resolve_truth(s, mcp)
            print(f"truth {s.id}: {s.truth or s.truth_error}")

    t0 = time.time()
    with ThreadPoolExecutor(max(1, args.parallel)) as ex:
        entries = list(ex.map(lambda s: run_one(s, run_dir, args), scenarios))

    if not args.no_judge:
        from . import judge

        if judge.available():
            with ThreadPoolExecutor(4) as ex:
                verdicts = list(ex.map(lambda pair: judge_one(*pair, run_dir, args), zip(scenarios, entries)))
            for e, v in zip(entries, verdicts):
                e["judge"] = v
        else:
            print("judge: the claude CLI is not on PATH; skipped", file=sys.stderr)

    for e in entries:
        e.pop("_capture", None)

    for sc in space_scenarios:
        print(f"[{sc.id}] data space on {sc.sources}", flush=True)
        try:
            entries.append(spaces.run(sc, mcp, run_dir / sc.id, args))
        except Exception as e:  # noqa: BLE001 -- reported as an errored scenario
            entries.append(
                {
                    "id": sc.id,
                    "title": sc.title,
                    "surface": "data_space",
                    "url": "",
                    "checks": [],
                    "turns": [],
                    "truth_error": f"{type(e).__name__}: {e}",
                }
            )

    for sc in probe_scenarios:
        print(f"[{sc.id}] containment probes on program {sc.program_id}", flush=True)
        try:
            entries.append(containment.run(sc, mcp, run_dir / sc.id, args))
        except Exception as e:  # noqa: BLE001 -- reported as an errored scenario
            entries.append(
                {
                    "id": sc.id,
                    "title": sc.title,
                    "surface": "containment",
                    "url": "",
                    "checks": [],
                    "turns": [],
                    "truth_error": f"{type(e).__name__}: {e}",
                }
            )

    from . import report

    meta = {
        "started": started.isoformat(timespec="seconds"),
        "suite": str(args.suite.relative_to(HERE) if args.suite.is_relative_to(HERE) else args.suite),
        "labs_user": who,
        "seconds": round(time.time() - t0),
        "preflight": problems,
    }
    md, js = report.write(run_dir, meta, entries)
    from . import vault

    if vault.scrub_dir(run_dir):  # the vault values must never reach a file; say so if they tried
        print("note: vault values were scrubbed from the run's files", file=sys.stderr)
    print(md.read_text())
    print(f"\nreport: {md}\n        {js}")
    return 0 if all(e["status"] == "PASS" for e in entries) else 1


def preflight(mcp, canopy) -> tuple[list[str], str]:
    """What would make the run meaningless, said before spending any agent turns."""
    from .driver import SESSION_FILE

    problems = []
    who = "?"
    try:
        who = mcp.call("labs_context", {}).get("user", {}).get("email", "?")
    except Exception as e:  # noqa: BLE001
        problems.append(f"FATAL labs MCP as ace failed ({e}); re-mint ace's PAT with /ace:labs-token-mint")
    if not SESSION_FILE.exists():
        problems.append(f"FATAL {SESSION_FILE} missing; run the ace plugin's /ace:labs-login")
    else:
        cookies = json.loads(SESSION_FILE.read_text()).get("cookies", [])
        host = "labs.connect.dimagi.com"
        labs = [c for c in cookies if c.get("domain", "").lstrip(".") == host and c["name"] == "sessionid"]
        if not labs or max(c.get("expires", 0) for c in labs) < time.time():
            problems.append("FATAL ace's labs session cookie has expired; run the ace plugin's /ace:labs-login")
    if not canopy.enabled or not canopy.me():
        problems.append(
            "no canopy PAT for ace (CANOPY_WEB_PAT in ~/.ace/.env): turns will be timed and read from the "
            "panel only (no tool-call record)"
        )
    return problems, who


def prepare(mcp, scenarios) -> None:
    """Load every opportunity the suite reads into labs' visit cache, one call each (a whole
    programme in one call outlives the load balancer's timeout)."""
    opps = sorted({o for s in scenarios for o in s.page.opportunity_ids})
    if not opps:
        return
    have = {
        o["opportunity_id"]: o["cached_visits"]
        for o in mcp.call("data_describe", {"opportunity_ids": opps})["opportunities"]
    }
    for o in opps:
        if not have.get(o):
            print(f"prepare: loading opportunity {o} into the visit cache ...", flush=True)
            mcp.call("data_describe", {"opportunity_ids": [o], "load_missing": True})


def run_one(scenario, run_dir: Path, args) -> dict:
    from . import checks, suite
    from .driver import PanelDriver, dump

    out = run_dir / scenario.id
    entry = {
        "id": scenario.id,
        "title": scenario.title,
        "covers": scenario.covers,
        "surface": scenario.page.surface,
        "url": scenario.page.url,
        "truth": scenario.truth,
        "truth_error": scenario.truth_error,
        "checks": [],
        "turns": [],
    }
    if scenario.truth_error:
        return entry
    print(f"[{scenario.id}] driving {scenario.page.url}", flush=True)
    cap = PanelDriver(out, headed=args.headed, turn_timeout=args.turn_timeout).run(
        scenario.page.url, [suite.render(t.say, scenario.truth) for t in scenario.turns]
    )
    data = dump(cap)
    (out / "capture.json").write_text(json.dumps(data, indent=1))
    if cap.transcript:
        (out / "transcript.json").write_text(json.dumps(cap.transcript, indent=1, default=str))
    entry.update(
        agent=cap.agent,
        session_id=cap.session_id,
        page_state=cap.page_state,
        blocked=cap.blocked,
        blocked_shot=f"{scenario.id}/blocked.png" if (out / "blocked.png").exists() else "",
        turns=data["turns"],
    )
    if not cap.blocked:
        for i, (spec, turn) in enumerate(zip(scenario.turns, cap.turns), 1):
            # A turn that names its own no_errors (e.g. allow_refused) replaces the implicit one.
            implicit = [] if any("no_errors" in c for c in spec.checks) else [{"no_errors": True}]
            for c in implicit + spec.checks:
                try:
                    r = checks.run(c, turn, scenario.truth)
                except ValueError as e:
                    r = checks.Result(str(c), False, f"bad check: {e}")
                entry["checks"].append({"turn": i, **r.__dict__})
        if cap.sensitive and cap.turns:
            entry["sensitive"] = True
            entry["history"] = cap.history
            entry["checks"] += sealed_checks(cap, len(cap.turns))
        (out / "reply.md").write_text(
            "\n\n".join(f"## Turn {i}\n\n> {t.say}\n\n{t.reply}" for i, t in enumerate(cap.turns, 1))
        )
    print(f"[{scenario.id}] done: {'BLOCKED ' + cap.blocked if cap.blocked else 'captured'}", flush=True)
    entry["_capture"] = cap
    return entry


ROUTE_SEALED = (
    "canopy-web: a private conversation in the embedded panel (frontend/src/vault/sealedChat.ts, "
    "useSealedConversation, the panel's sealed banner and unlock form)"
)


def sealed_checks(cap, last: int) -> list[dict]:
    """A private (sealed) conversation: live turns needed no unlock, and after a reload the
    panel offers it again, ace's vault key opens it, and the last reply reads again."""
    h = cap.history or {}
    banners = [i for i, t in enumerate(cap.turns, 1) if (t.unlock or {}).get("banner")]
    out = [
        ("live replies need no unlock", not banners, f"sealed banner during turn(s) {banners}" if banners else "none"),
        ("sealed conversation reopens after a reload", h.get("reopened"), h.get("error") or str(h)),
        (
            "ace's vault key unlocks it",
            h.get("unlocked") or (h.get("reopened") and not h.get("banner")),
            h.get("error")
            or ("unlocked" if h.get("unlocked") else "no banner shown" if not h.get("banner") else "locked"),
        ),
        (
            "the last reply reads again",
            h.get("reply_visible"),
            "visible" if h.get("reply_visible") else "not found in the panel",
        ),
    ]
    return [
        {
            "turn": last,
            "name": name,
            "passed": bool(ok),
            "detail": str(detail)[:300],
            "gate": True,
            "route": ROUTE_SEALED,
        }
        for name, ok, detail in out
    ]


def judge_one(scenario, entry, run_dir: Path, args) -> dict:
    from . import judge

    cap = entry.pop("_capture", None)
    if cap is None or entry.get("blocked") or not cap.turns:
        return {}
    verdict = judge.judge(scenario, cap, entry["checks"], run_dir / scenario.id, model=args.judge_model)
    print(f"[{scenario.id}] judge: {verdict.get('mean', verdict.get('error'))}", flush=True)
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
