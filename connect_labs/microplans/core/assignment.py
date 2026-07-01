"""Assignment algorithms — work-area groups → CHWs.

Phase 2 of the two-phase planning pipeline:

    cells  →  [GROUPING]  →  work-area groups  →  [ASSIGNMENT]  →  CHWs

Given groups produced by Phase 1 (each = a spatially-contiguous CHW-walkable
territory) and a list of CHW names, decide who walks each. The decision
propagates back to the cells: every cell in group G gets ``opportunity_access``
set to the CHW assigned to G.

Strategies
----------
- ``manual``         — no-op. Existing assignments are preserved (the LLO
                       drives reassignment cell-by-cell via the table).
- ``round_robin``    — sort groups by building count desc, then round-robin
                       across the workers. Crude but predictable; useful
                       when you just want every CHW to have roughly the same
                       number of buildings.
- ``minimax_spread`` — Neal Lesh's greedy-with-restarts (see
                       ``enhanced_flw_assigner.py`` referenced in
                       ``reference_microplan_kpis.md``). Objective: minimize
                       the MAXIMUM FLW territory diameter (the worst-case
                       worker travel). For each restart, shuffle the group
                       order and greedy-assign each group to the worker whose
                       new territory diameter would be smallest. Pick the
                       restart with the lowest max diameter overall.
                       Operates on group centroids for speed; the on-screen
                       per-FLW spread (computed cell-by-cell in
                       ``plan.plan_kpis``) is the final word.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

DEFAULT_RESTARTS = 20
DEFAULT_SEED = 42

VALID_STRATEGIES = ("manual", "round_robin", "minimax_spread")


@dataclass
class AssignmentConfig:
    strategy: str = "minimax_spread"
    workers: list[str] = field(default_factory=list)
    # minimax_spread only:
    restarts: int = DEFAULT_RESTARTS
    seed: int = DEFAULT_SEED

    @classmethod
    def from_payload(cls, d: dict) -> AssignmentConfig:
        strategy = d.get("strategy", "minimax_spread")
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"unknown assignment strategy: {strategy!r} (one of {VALID_STRATEGIES})")
        workers_raw = d.get("workers") or []
        if isinstance(workers_raw, str):
            # Accept newline- or comma-separated string as a convenience.
            workers_raw = [w.strip() for w in workers_raw.replace(",", "\n").split("\n")]
        workers = [w for w in (str(x).strip() for x in workers_raw) if w][:1000]
        if not workers and strategy in ("round_robin", "minimax_spread"):
            raise ValueError(f"strategy {strategy!r} requires at least one worker")
        return cls(
            strategy=strategy,
            workers=workers,
            restarts=max(1, min(int(d.get("restarts", DEFAULT_RESTARTS)), 200)),
            seed=int(d.get("seed", DEFAULT_SEED)),
        )


def assign_groups_to_chws(work_areas: list[dict], config: AssignmentConfig) -> list[dict]:
    """In-place: set ``opportunity_access`` on each non-excluded cell based on
    which CHW the strategy assigned its ``work_area_group`` to.

    Excluded cells are left alone (their assignment is irrelevant). Cells whose
    group ends up unassigned (e.g. fewer workers than groups in some edge case)
    are also left alone.
    """
    if config.strategy == "manual":
        return work_areas
    active = [w for w in work_areas if w.get("status") != "EXCLUDED"]
    if not active or not config.workers:
        return work_areas

    groups = _aggregate_groups(active)
    if not groups:
        return work_areas

    if config.strategy == "round_robin":
        assignment = _round_robin(groups, config.workers)
    elif config.strategy == "minimax_spread":
        assignment = _minimax_spread(groups, config.workers, config.restarts, config.seed)
    else:  # pragma: no cover — guarded above
        raise ValueError(f"unknown assignment strategy: {config.strategy!r}")

    for w in active:
        g = w.get("work_area_group")
        if g in assignment:
            w["opportunity_access"] = assignment[g]
    return work_areas


# ---- aggregate helpers -------------------------------------------------------


def _aggregate_groups(active_cells: list[dict]) -> list[dict]:
    """Mean centroid + sum building count per work_area_group."""
    by_group: dict[str, dict] = {}
    for w in active_cells:
        g = w.get("work_area_group", "")
        if not g:
            continue
        c = w.get("centroid") or [0.0, 0.0]
        info = by_group.setdefault(g, {"lons": [], "lats": [], "buildings": 0})
        info["lons"].append(c[0])
        info["lats"].append(c[1])
        info["buildings"] += int(w.get("building_count", 0))
    out = []
    for name, info in by_group.items():
        n = len(info["lons"]) or 1
        out.append(
            {
                "name": name,
                "centroid": (sum(info["lons"]) / n, sum(info["lats"]) / n),
                "buildings": info["buildings"],
            }
        )
    return out


# ---- round-robin -------------------------------------------------------------


def _round_robin(groups: list[dict], workers: list[str]) -> dict[str, str]:
    """Sort groups by building count desc, then round-robin across workers."""
    sorted_groups = sorted(groups, key=lambda g: (-g["buildings"], g["name"]))
    return {g["name"]: workers[i % len(workers)] for i, g in enumerate(sorted_groups)}


# ---- minimax-spread (Neal Lesh) ----------------------------------------------


_EARTH_KM = 6371.0088


def _haversine(p: tuple, q: tuple) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [p[0], p[1], q[0], q[1]])
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    a = min(1.0, max(0.0, a))  # guard float error so asin(sqrt) can't domain-error
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def _diameter(centroids: list[tuple]) -> float:
    """Max pairwise haversine distance (km) among the given centroids."""
    n = len(centroids)
    if n <= 1:
        return 0.0
    return max(_haversine(centroids[i], centroids[j]) for i in range(n) for j in range(i + 1, n))


def _minimax_spread(groups: list[dict], workers: list[str], restarts: int, seed: int) -> dict[str, str]:
    """Greedy with restarts: minimize max FLW territory diameter.

    For each restart r:
      1. Shuffle group order (deterministic via seed+r).
      2. Seed each worker with one group (first n_workers).
      3. For each remaining group, greedy-assign to the worker whose new
         territory diameter would be smallest.
    Keep the restart whose max-FLW-diameter is lowest. The "dumping ground"
    caveat from Neal's spec (the LAST FLW in a pure greedy loop accumulates
    leftovers) is partially mitigated by always picking the FLW with the
    smallest *new* diameter, not the smallest current load.
    """
    n_workers = len(workers)
    best_assignment: dict[str, str] | None = None
    best_max = math.inf
    for r in range(restarts):
        rng = random.Random(seed + r)
        order = list(groups)
        rng.shuffle(order)

        buckets: dict[str, list[dict]] = {w: [] for w in workers}
        for i, g in enumerate(order[:n_workers]):
            buckets[workers[i]].append(g)
        for g in order[n_workers:]:
            best_w = workers[0]
            best_d = math.inf
            for w in workers:
                trial = [x["centroid"] for x in buckets[w]] + [g["centroid"]]
                d = _diameter(trial)
                if d < best_d:
                    best_d = d
                    best_w = w
            buckets[best_w].append(g)

        final_max = max((_diameter([x["centroid"] for x in gs]) for gs in buckets.values()), default=0.0)
        if final_max < best_max:
            best_max = final_max
            best_assignment = {g["name"]: w for w, gs in buckets.items() for g in gs}

    return best_assignment or {}
