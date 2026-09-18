"""WA Revisit's Step 3: which active work areas sit more than a given
distance from every other active work area — reviewers want to drop these
before hand-off so field staff are never routed somewhere for just one
isolated work area.

Deliberately unrelated to `core.gaps.planning_gap_features`'s own
`dist_to_multi_m`/`roof_area_m2` fields (consumed by microplans' own review
page as a single-building-detection noise filter, gated on a gap cell having
exactly one building) — this module's isolation check is purely WA-centroid-
to-WA-centroid distance, applies uniformly to every active work area
regardless of source (an original candidate or a Step 2 gap-fill cell) or how
many buildings it has, and is evaluated explicitly by the reviewer on Step 3
rather than baked into the gap-fill computation itself.
"""

from __future__ import annotations

from shapely.geometry import shape

from connect_labs.mopup.core.indicators import build_neighbor_graph


def isolated_work_area_ids(rows: list[dict], distance_m: float) -> set[str]:
    """wa_ids from `rows` (candidate-row shape — needs only `wa_id` and
    `boundary`, the GeoJSON geometry both `run.candidate_work_areas` and
    `core.candidates.gap_feature_to_candidate_row` already produce) that have
    NO other row's centroid within `distance_m` of their own — i.e. every
    other active work area is further away than that.

    Reuses `core.indicators.build_neighbor_graph`/`haversine_distance_m` —
    "isolated" is simply an empty neighbor list at that same primitive, keyed
    off each row's own polygon centroid (candidate rows don't carry `lat`/
    `lon` directly, only `boundary`). A row with no boundary is skipped
    entirely (never guessed, matches `evaluate_run`'s own convention) and can
    neither be isolated itself nor corroborate a neighbor."""
    positioned = []
    for row in rows:
        boundary = row.get("boundary")
        if not boundary:
            continue
        centroid = shape(boundary).centroid
        positioned.append({"wa_id": row["wa_id"], "lat": centroid.y, "lon": centroid.x})

    graph = build_neighbor_graph(positioned, distance_m)
    return {wa_id for wa_id, neighbors in graph.items() if not neighbors}
