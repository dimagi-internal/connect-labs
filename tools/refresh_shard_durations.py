#!/usr/bin/env python3
"""Rebuild connect_labs/testing/shard_durations.json from a CI run's timings.

Every `pytest (K/N)` slice in ci.yml uploads `shard-durations-K`: each test
file it ran and that file's total test time. This merges one run's slices into
the table `--shard` balances on, and prints how the next run would split.

    python3 tools/refresh_shard_durations.py            # latest green push run on main
    python3 tools/refresh_shard_durations.py --run 4212...

Refresh when the slices' `passed ... in Ns` lines drift apart by more than a
minute or so. A file the table does not know is still run, placed by a hash of
its path, so a stale table costs balance, never coverage.

Needs `gh`, signed in to a GitHub account that can read the repo's artifacts.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "dimagi-internal/connect-labs"
ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "connect_labs" / "testing" / "shard_durations.json"


def latest_run() -> str:
    out = subprocess.check_output(
        [
            "gh",
            "run",
            "list",
            "--repo",
            REPO,
            "--workflow",
            "ci.yml",
            "--branch",
            "main",
            "--event",
            "push",
            "--status",
            "success",
            "--limit",
            "1",
            "--json",
            "databaseId",
            "-q",
            ".[0].databaseId",
        ],
        text=True,
    ).strip()
    if not out:
        sys.exit("no green push run of ci.yml on main to read timings from")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", help="ci.yml run id (default: the latest green push run on main)")
    args = parser.parse_args()
    run = args.run or latest_run()

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.check_call(
            ["gh", "run", "download", run, "--repo", REPO, "--pattern", "shard-durations-*", "--dir", tmp]
        )
        slices = sorted(Path(tmp).glob("shard-durations-*/*.json"))
        if not slices:
            sys.exit(f"run {run} uploaded no shard-durations-* artifacts")
        table: dict[str, float] = {}
        for path in slices:
            table.update(json.loads(path.read_text()))

    TABLE.write_text(json.dumps(dict(sorted(table.items())), indent=2) + "\n")
    print(f"{TABLE.relative_to(ROOT)}: {len(table)} files, {sum(table.values()):.0f}s of test time, from run {run}")

    sys.path.insert(0, str(ROOT))
    from connect_labs.testing.shard import assign

    for n in (3, 4, 5):
        owner = assign(table, n)
        loads = [sum(t for f, t in table.items() if owner[f] == k) for k in range(1, n + 1)]
        # Worker-seconds; each slice runs four xdist workers.
        print(f"  {n} slices: " + "  ".join(f"{load / 4:.0f}s" for load in loads) + "  (per slice, over 4 workers)")


if __name__ == "__main__":
    main()
