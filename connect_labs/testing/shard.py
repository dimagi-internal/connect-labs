"""Run one slice of the suite: ``pytest --shard=2/3``.

CI splits the suite over several runners because one runner is the ceiling:
four shared vCPUs, Postgres in a container on the same box, and ~13,500 tests
that each cost little. Nothing in the suite is an outlier worth cutting (the
slowest file is ~15s of a ~450s total, measured 2026-10-15); the time is the
sum, and the only way to shrink a sum on a fixed box is more boxes.

**Whole files, never single tests.** A module- or class-scoped fixture (the
seed-once worlds in supply_chain/tests) would be built once per shard that
holds one of its tests, so splitting a file pays its setup again.

**Balanced by test count, deterministically.** Files are dealt largest-first to
whichever shard holds the fewest tests so far. Every shard collects the whole
suite and runs the same deal, so the shards agree on the split without talking
to each other -- and so do the xdist workers inside each shard, which is what
xdist checks before it starts. Test count is a proxy for time; on this suite,
where no file dominates, it lands the shards within seconds of each other.

Without ``--shard`` nothing is deselected.
"""

import re

_SPEC = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


def pytest_addoption(parser):
    parser.addoption(
        "--shard",
        default=None,
        help="Run only slice K of N of the suite, split by whole files: --shard=2/3.",
    )


def parse(spec: str) -> tuple[int, int]:
    match = _SPEC.match(spec)
    if not match:
        raise ValueError(f"--shard wants K/N, like 2/3; got {spec!r}")
    k, n = int(match.group(1)), int(match.group(2))
    if not 1 <= k <= n:
        raise ValueError(f"--shard={spec}: K must be between 1 and N")
    return k, n


def assign(counts: dict[str, int], n: int) -> dict[str, int]:
    """Deal files to shards 1..n, largest first, each to the lightest shard."""
    loads = [0] * n
    owner = {}
    # Ties broken by path, so the deal never depends on collection order.
    for path, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        shard = min(range(n), key=lambda i: (loads[i], i))
        loads[shard] += count
        owner[path] = shard + 1
    return owner


def _file(item) -> str:
    return item.nodeid.split("::", 1)[0]


def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    if not spec:
        return
    k, n = parse(spec)
    counts: dict[str, int] = {}
    for item in items:
        counts[_file(item)] = counts.get(_file(item), 0) + 1
    owner = assign(counts, n)
    keep, drop = [], []
    for item in items:
        (keep if owner[_file(item)] == k else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
