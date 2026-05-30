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
    """Each opp should have one weekly run entry per configured week
    (including missed weeks, which appear as ran=False entries)."""
    expected_weeks = config.get("weeks", [])
    for opp in result.get("opportunities", []):
        weeks = opp.get("weeks", [])
        if len(weeks) != len(expected_weeks):
            yield Issue(
                "error",
                "WEEK_COUNT_MISMATCH",
                f"opp {opp['opportunity_id']}: " f"expected {len(expected_weeks)} week entries, got {len(weeks)}.",
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
    """If any opp sets ``in_progress_last_week=true``, its last week's
    run must show ``in_progress=True`` and zero actions (audits/tasks
    that the recorder is going to write live during the scene)."""
    last_idx = len(config.get("weeks", [])) - 1
    if last_idx < 0:
        return
    in_prog_opp_ids = {opp["opportunity_id"] for opp in config.get("opps", []) if opp.get("in_progress_last_week")}
    if not in_prog_opp_ids:
        return
    for opp in result.get("opportunities", []):
        if opp["opportunity_id"] not in in_prog_opp_ids:
            continue
        weeks = opp.get("weeks", [])
        if len(weeks) <= last_idx:
            yield Issue(
                "error",
                "IN_PROGRESS_MISSING",
                f"opp {opp['opportunity_id']}: expected an in_progress last "
                f"week (index {last_idx}) but week list is shorter.",
            )
            continue
        last_week = weeks[last_idx]
        if not last_week.get("in_progress"):
            yield Issue(
                "error",
                "NOT_IN_PROGRESS",
                f"opp {opp['opportunity_id']}: last week run " f"{last_week.get('run_id')} is not marked in_progress.",
            )
        elif last_week.get("actions"):
            yield Issue(
                "warning",
                "IN_PROGRESS_HAS_ACTIONS",
                f"opp {opp['opportunity_id']}: in_progress week already has "
                f"{last_week['actions']} action(s) seeded — the manager-flow "
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
