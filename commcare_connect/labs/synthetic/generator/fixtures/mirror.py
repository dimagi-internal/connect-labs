"""High-fidelity 'close mirror' source profiling (issue #713).

Mirror mode reproduces a real opp's *structure* — visits-per-case and
cases-per-FLW ratios, per-entity value trajectories — rather than re-sampling
from fitted summary statistics. This module groups source visits by entity and
extracts the empirical structure the engine replays.

De-identification: only numbers and counts ever leave the source here. No names,
phones, GPS, or free text are carried out.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EntityStructure:
    """Empirical structure of a source opp's visits, grouped by entity.

    ``visits_per_entity`` maps a visit-count to the number of entities that had
    exactly that many visits — the exact empirical histogram, so a clone can
    reproduce the real visits-per-case multiset rather than a refit of it.

    ``entity_owner`` maps each entity to the FLW who visited it most (ties break
    on the lower username, for determinism) — the case→FLW link the source
    otherwise doesn't record.

    ``owner_visit_counts`` maps each owner FLW to the sorted visit-counts of the
    entities it owns. This captures cases-per-FLW (list length) jointly with
    visits-per-case (the counts), so the engine can rebuild the exact ownership
    shape: each ranked persona gets that many cases, each with those visit counts.
    """

    visits_per_entity: dict[int, int]
    entity_owner: dict[str, str]
    owner_visit_counts: dict[str, list[int]]
    # One de-identified series per entity. Each is
    # ``{"owner": <source flw>, "start_date": <ISO first visit>, "visits": [...]}``
    # where each visit is ``{"day": <offset from first visit>, "values": {path: float}}``.
    # Replaying a series reproduces that case's owner, timing, visit count, and
    # value trajectory exactly. Numerics only — names/phones/free text never enter.
    transplant_pool: list[dict[str, Any]]


def _parse_date(raw: Any) -> dt.date | None:
    if not isinstance(raw, str):
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _numeric_leaves(form_json: dict, numeric_paths: set[str] | None) -> dict[str, float]:
    """Numeric leaf values of a visit's form_json as {dotted_path: float}.

    With ``numeric_paths``, only those paths are read (real exports encode numbers
    as strings, so type alone is unreliable — the caller supplies the schema's
    numeric paths). Without it, only genuine int/float leaves qualify (bools and
    strings are excluded), which keeps de-identification safe by default.
    """
    out: dict[str, float] = {}
    if numeric_paths is not None:
        for path in numeric_paths:
            raw = _extract_nested(form_json, path)
            try:
                out[path] = float(raw)
            except (TypeError, ValueError):
                continue
        return out

    def walk(obj: dict, prefix: str) -> None:
        for key, val in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(val, dict):
                walk(val, path)
            elif isinstance(val, bool):
                continue
            elif isinstance(val, (int, float)):
                out[path] = float(val)

    walk(form_json, "")
    return out


def _extract_nested(obj: dict, dotted_path: str) -> Any:
    cur: Any = obj
    for part in dotted_path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def profile_entity_structure(visits: list[dict], *, numeric_paths: set[str] | None = None) -> EntityStructure:
    # visits per (entity, flw) so we can both count an entity's visits and find
    # the FLW who did the most of them.
    visits_by_entity_flw: dict[str, Counter[str]] = defaultdict(Counter)
    visits_by_entity: dict[str, list[dict]] = defaultdict(list)
    for v in visits:
        eid = v.get("entity_id")
        if not eid:
            continue
        visits_by_entity_flw[eid][v.get("username") or ""] += 1
        visits_by_entity[eid].append(v)

    counts_by_entity = {eid: sum(by_flw.values()) for eid, by_flw in visits_by_entity_flw.items()}
    visits_per_entity = dict(Counter(counts_by_entity.values()))

    entity_owner: dict[str, str] = {}
    owner_visit_counts: dict[str, list[int]] = defaultdict(list)
    for eid, by_flw in visits_by_entity_flw.items():
        # Most visits wins; tie breaks on the lower username (negate count to sort
        # high-count-first while username sorts ascending).
        owner = min(by_flw.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        entity_owner[eid] = owner
        owner_visit_counts[owner].append(counts_by_entity[eid])

    transplant_pool: list[dict[str, Any]] = []
    for eid in visits_by_entity:
        dated = sorted(
            ((d, v) for v in visits_by_entity[eid] if (d := _parse_date(v.get("visit_date"))) is not None),
            key=lambda dv: dv[0],
        )
        if not dated:
            continue
        first = dated[0][0]
        series_visits = [
            {"day": (d - first).days, "values": _numeric_leaves(v.get("form_json") or {}, numeric_paths)}
            for d, v in dated
        ]
        transplant_pool.append({"owner": entity_owner[eid], "start_date": first.isoformat(), "visits": series_visits})

    return EntityStructure(
        visits_per_entity=visits_per_entity,
        entity_owner=entity_owner,
        owner_visit_counts={k: sorted(v) for k, v in owner_visit_counts.items()},
        transplant_pool=transplant_pool,
    )
