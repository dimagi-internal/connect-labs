"""Pre-record smoke checks: did regenerate.py produce the world the
recorder expects?

The README's "Audit + task leftovers wedge the recorder" footgun is
the exact kind of thing this catches: after one recorder run, the bad-MUAC
FLW already has an audit + task, so the next ``Create Audit`` click finds
``View audit`` instead. Without this check, the recorder fails opaquely
on scene 2.

Each ``check_*`` function takes the snapshot dict (the one regenerate.py
returns) and the demo_config dict, and yields ``Issue`` instances. The
runner prints them and exits non-zero if any are found.

Add new walkthrough-specific checks by writing a new function that takes
``(result, config) -> Iterable[Issue]`` and registering it in ``ALL_CHECKS``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper():7}] {self.code}: {self.message}"


# ---------------------------------------------------------------------- #
# Checks
# ---------------------------------------------------------------------- #


def check_opps_present(result: dict, config: dict) -> Iterator[Issue]:
    """Every opp in the config should appear in the result's
    ``opportunities`` list. Missing opps are a hard error: the recorder
    will hit a 404 trying to load them."""
    seeded_ids = {opp["opportunity_id"] for opp in result.get("opportunities", [])}
    for opp in config.get("opps", []):
        if opp["opportunity_id"] not in seeded_ids:
            yield Issue(
                "error",
                "OPP_NOT_SEEDED",
                f"opportunity {opp['opportunity_id']} ({opp.get('label')}) "
                "is in demo_config.json but missing from the seed result.",
            )


def check_week_counts(result: dict, config: dict) -> Iterator[Issue]:
    """Each opp should have one weekly run entry per configured COMPLETED
    week (including missed weeks, which appear as ran=False entries), plus
    one extra entry for the in-progress current week when the opp opts in
    via ``in_progress_current_week``."""
    expected_completed = len(config.get("weeks", []))
    has_current = bool(config.get("current_week"))
    cfg_by_id = {opp["opportunity_id"]: opp for opp in config.get("opps", [])}
    for opp in result.get("opportunities", []):
        cfg = cfg_by_id.get(opp["opportunity_id"], {})
        expected = expected_completed + (1 if has_current and cfg.get("in_progress_current_week") else 0)
        weeks = opp.get("weeks", [])
        if len(weeks) != expected:
            yield Issue(
                "error",
                "WEEK_COUNT_MISMATCH",
                f"opp {opp['opportunity_id']}: " f"expected {expected} week entries, got {len(weeks)}.",
            )


def check_par_run_present(result: dict, config: dict) -> Iterator[Issue]:
    par = result.get("program_admin_report")
    if not par or not par.get("run_id"):
        yield Issue(
            "error",
            "NO_PAR_RUN",
            "program_admin_report run_id is missing from the seed result.",
        )
        return
    expected_sources = len(config.get("opps", []))
    actual_sources = par.get("watched_sources_count")
    if actual_sources != expected_sources:
        yield Issue(
            "warning",
            "PAR_SOURCE_COUNT_MISMATCH",
            f"PAR run watches {actual_sources} sources; config has {expected_sources} opps.",
        )


def check_in_progress_week(result: dict, config: dict) -> Iterator[Issue]:
    """If any opp sets ``in_progress_current_week=true``, it must carry one
    extra week entry (the CURRENT week, outside the PAR window) marked
    ``in_progress=True`` with zero actions — the audits/tasks for it are
    written live by the manager-flow recording. Every PAR-window week, by
    contrast, must be completed (never in_progress) or the grid shows a
    NO RUN hole for a week the window claims to watch."""
    current_week = config.get("current_week")
    in_prog_opp_ids = {opp["opportunity_id"] for opp in config.get("opps", []) if opp.get("in_progress_current_week")}
    completed_count = len(config.get("weeks", []))
    for opp in result.get("opportunities", []):
        weeks = opp.get("weeks", [])
        # No PAR-window week may be in_progress, for any opp.
        for w in weeks[:completed_count]:
            if w.get("in_progress"):
                yield Issue(
                    "error",
                    "WINDOW_WEEK_IN_PROGRESS",
                    f"opp {opp['opportunity_id']}: PAR-window week {w.get('week')} "
                    "is in_progress — the window must contain only completed weeks "
                    "(the live manager run belongs to current_week, outside it).",
                )
        if opp["opportunity_id"] not in in_prog_opp_ids:
            continue
        if not current_week:
            yield Issue(
                "error",
                "NO_CURRENT_WEEK",
                f"opp {opp['opportunity_id']}: in_progress_current_week is set "
                "but the config carries no current_week.",
            )
            continue
        current = weeks[completed_count] if len(weeks) > completed_count else None
        if current is None or not current.get("in_progress"):
            yield Issue(
                "error",
                "NOT_IN_PROGRESS",
                f"opp {opp['opportunity_id']}: expected an in_progress run for "
                f"the current week ({current_week}) after the {completed_count} "
                "window weeks, but none was generated.",
            )
        elif current.get("actions"):
            yield Issue(
                "warning",
                "IN_PROGRESS_HAS_ACTIONS",
                f"opp {opp['opportunity_id']}: in_progress current week already "
                f"has {current['actions']} action(s) seeded — the manager-flow "
                "recorder expects to create the audit + task live.",
            )


ALL_CHECKS: list[Callable[[dict, dict], Iterable[Issue]]] = [
    check_opps_present,
    check_week_counts,
    check_par_run_present,
    check_in_progress_week,
]


# ---------------------------------------------------------------------- #
# Runner
# ---------------------------------------------------------------------- #


def run_checks(result: dict, config: dict, *, extra: list[Callable] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    checks = list(ALL_CHECKS) + (extra or [])
    for check in checks:
        issues.extend(check(result, config))
    return issues


def report(issues: list[Issue]) -> int:
    """Print issues. Return non-zero exit code if any are errors."""
    if not issues:
        print("All verify checks passed.")
        return 0
    for issue in issues:
        print(str(issue))
    errors = [i for i in issues if i.level == "error"]
    if errors:
        print(f"\n{len(errors)} error(s) — recorder likely to fail. Re-run regenerate.py.")
        return 1
    print(f"\n{len(issues)} warning(s) — proceed with caution.")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result", type=Path, required=True, help="Seed result JSON.")
    p.add_argument("--config", type=Path, required=True, help="demo_config.json.")
    args = p.parse_args(argv)
    result = json.loads(args.result.read_text())
    config = json.loads(args.config.read_text())
    config.pop("_comment", None)
    return report(run_checks(result, config))


if __name__ == "__main__":
    sys.exit(main())
