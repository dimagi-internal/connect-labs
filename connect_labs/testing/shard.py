"""Run one slice of the suite: ``pytest --shard=2/4``.

CI splits the suite over several runners because one runner is the ceiling:
four shared vCPUs, Postgres in a container on the same box, and ~16,000 tests
that each cost little. The time is the sum, and the only way to shrink a sum on
a fixed box is more boxes.

**Whole files, never single tests.** A module- or class-scoped fixture (the
seed-once worlds in supply_chain/tests) would be built once per shard that
holds one of its tests, so splitting a file pays its setup again.

**A shard never imports another shard's files.** The slice is decided per file
in ``pytest_ignore_collect``, BEFORE the module is imported, rather than by
collecting everything and deselecting two thirds of it. Collecting the whole
suite was most of the minute every shard spent before its first test
(Django import plus ~16k items, in each of four xdist workers at once).

**Balanced by measured time.** ``shard_durations.json`` holds each file's test
time from a real CI run (setup + call + teardown of every test in it, as the
xdist workers report it). Files are dealt longest-first to whichever shard has
the least time so far. Balancing by test COUNT, as this used to, left one shard
~2 minutes behind the others: a file of three slow seeding tests weighs the
same as three one-millisecond assertions.

A file the table does not know yet (added since it was measured) is placed by a
stable hash of its path. Every shard -- and every xdist worker inside one --
computes the same deal from the same table and paths without talking to each
other, which is what xdist checks before it starts. Refresh the table with
``tools/refresh_shard_durations.py`` when the shards drift apart; CI uploads
fresh timings from every run as the ``shard-durations-*`` artifacts.

Without ``--shard`` nothing is skipped.
"""

import json
import re
import zlib
from fnmatch import fnmatch
from pathlib import Path

DURATIONS_FILE = Path(__file__).with_name("shard_durations.json")

_SPEC = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


def pytest_addoption(parser):
    parser.addoption(
        "--shard",
        default=None,
        help="Run only slice K of N of the suite, split by whole files: --shard=2/4.",
    )
    parser.addoption(
        "--shard-durations-out",
        default=None,
        help="Write each test file's total test time (seconds, JSON) to this path.",
    )


def parse(spec: str) -> tuple[int, int]:
    match = _SPEC.match(spec)
    if not match:
        raise ValueError(f"--shard wants K/N, like 2/3; got {spec!r}")
    k, n = int(match.group(1)), int(match.group(2))
    if not 1 <= k <= n:
        raise ValueError(f"--shard={spec}: K must be between 1 and N")
    return k, n


def assign(weights: dict[str, float], n: int) -> dict[str, int]:
    """Deal files to shards 1..n, heaviest first, each to the lightest shard."""
    loads = [0.0] * n
    owner = {}
    # Ties broken by path, so the deal never depends on the table's order.
    for path, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        shard = min(range(n), key=lambda i: (loads[i], i))
        loads[shard] += weight
        owner[path] = shard + 1
    return owner


def shard_of(path: str, n: int, owner: dict[str, int]) -> int:
    """The shard a file belongs to: the deal's, or a stable hash for a file it has not seen."""
    if path in owner:
        return owner[path]
    return zlib.crc32(path.encode()) % n + 1


def load_durations(source: Path = DURATIONS_FILE) -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads(source.read_text()).items()}
    except (OSError, ValueError):
        # No table (or a broken one) still gives a complete, consistent split:
        # every file falls through to the hash.
        return {}


def _deal(config) -> tuple[int, int, dict[str, int]] | None:
    cached = getattr(config, "_shard_deal", False)
    if cached is not False:
        return cached
    spec = config.getoption("--shard")
    deal = None
    if spec:
        k, n = parse(spec)
        deal = (k, n, assign(load_durations(), n))
    config._shard_deal = deal
    return deal


def pytest_ignore_collect(collection_path, config):
    deal = _deal(config)
    if deal is None or collection_path.suffix != ".py" or not collection_path.is_file():
        return None
    if not any(fnmatch(collection_path.name, pattern) for pattern in config.getini("python_files")):
        return None  # conftest.py and helpers are never ours to skip
    k, n, owner = deal
    try:
        rel = collection_path.relative_to(config.rootpath).as_posix()
    except ValueError:
        return None
    # True skips the file; None (never False) leaves every other ignore rule in force.
    return True if shard_of(rel, n, owner) != k else None


# ---- measuring, for the next table --------------------------------------------


# Per-process; under xdist only the controller's copy sees every report.
_times: dict[str, float] = {}


def pytest_runtest_logreport(report):
    # Fires in the xdist controller for every worker's report, so the controller
    # sees the whole shard. Setup carries a module-scoped fixture's cost, which
    # belongs to the file like the rest.
    path = report.nodeid.split("::", 1)[0]
    _times[path] = _times.get(path, 0.0) + (report.duration or 0.0)


def pytest_sessionfinish(session):
    config = session.config
    out = config.getoption("--shard-durations-out")
    if not out or hasattr(config, "workerinput"):
        return  # an xdist worker only sees its share; the controller writes
    times = {path: round(seconds, 2) for path, seconds in sorted(_times.items())}
    Path(out).write_text(json.dumps(times, indent=0, sort_keys=True) + "\n")
