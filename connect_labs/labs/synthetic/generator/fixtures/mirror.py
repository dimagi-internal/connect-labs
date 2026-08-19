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
    # where each visit is ``{"day": <offset from first visit>, "values": {path: float}}``
    # plus an optional ``"dates": {path: <signed day-offset from first visit>}`` for
    # declared date leaves (e.g. ``child_dob``). Replaying a series reproduces that
    # case's owner, timing, visit count, value trajectory, and date-derived axes
    # (age = visit_date - dob) exactly. Numerics + date offsets only — names, phones,
    # free text, and absolute calendar dates never enter.
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


def _categorical_leaves(form_json: dict, categorical_paths: set[str] | None) -> dict[str, str]:
    """Categorical answers of a visit, as {dotted_path: value}.

    Only paths the profiler classified as categorical are read, and those are
    select/multiselect questions — bounded choice lists (yes/no, a status), never
    free text. So this carries clinical ANSWERS, not identifiers, the same basis on
    which the pool already carries weights and vitals.

    Without this the pool was numeric-only: 149,531 values on the KMC cohort, every
    one a float. Every outcome — child_alive, kmc_status, feeding, the danger-sign
    and referral yes/nos — was therefore re-drawn from marginals per visit instead
    of replayed, so a clone matched the source's death RATE while dying in the wrong
    babies. Cohort totals looked right and every cross-cutting question ("do
    slow-growing babies die more?", "which FLWs have worse outcomes?") was noise.
    """
    out: dict[str, str] = {}
    if not categorical_paths:
        return out
    for path in categorical_paths:
        raw = _extract_nested(form_json, path)
        if raw is None or raw == "":
            continue
        out[path] = str(raw)
    return out


def _date_offsets(form_json: dict, date_paths: set[str] | None, first: dt.date) -> dict[str, int]:
    """Declared date leaves as integer day-offsets from this entity's first visit.

    The growth curve's age axis is ``visit_date - child_dob``, where ``child_dob``
    is a *date*, not a number — so ``_numeric_leaves`` never carries it. Storing
    each date as a signed offset from the entity's first visit (negative for a DOB
    that precedes it) lets the clone reconstruct the exact relationship while
    leaking no absolute calendar date beyond the ``start_date`` the pool already
    records. Constant per-entity dates (a DOB repeated each visit) yield the same
    offset every visit, so the replayed date stays stable across the child's series.
    """
    out: dict[str, int] = {}
    if not date_paths:
        return out
    for path in date_paths:
        d = _parse_date(_extract_nested(form_json, path))
        if d is not None:
            out[path] = (d - first).days
    return out


def _extract_nested(obj: dict, dotted_path: str) -> Any:
    cur: Any = obj
    for part in dotted_path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _case_id(visit: dict) -> str | None:
    """The case this form was submitted against."""
    v = _extract_nested(visit.get("form_json") or {}, "form.case.@case_id")
    return v if isinstance(v, str) and v else None


def _subcase_id(visit: dict) -> str | None:
    """The case this form CREATED, if any."""
    v = _extract_nested(visit.get("form_json") or {}, "form.subcase_0.case.@case_id")
    return v if isinstance(v, str) and v else None


def build_entity_resolver(visits: list[dict]):
    """Return ``visit -> beneficiary id``, decided from the whole cohort.

    No per-visit rule can get this right, because the same two fields mean
    different things in different KMC app generations:

      V3 registration   case = the MOTHER, subcase = the baby
      V3 visit          case = the baby,   subcase = a per-visit case
      Gen-1 registration case = the baby,  no subcase
      Gen-1 visit       case = the baby,   subcase = a per-visit case

    So ``case`` alone strands V3 registrations on the mother (and collapses twins
    onto her), while ``subcase`` alone shreds every visit into its own entity.

    The cohort settles it. A subcase is the beneficiary only if some OTHER form was
    later submitted *against* it — that is what distinguishes the child a
    registration created from the throwaway case a visit created. So: collect every
    id that appears as ``form.case.@case_id`` anywhere, then prefer a subcase only
    when it is in that set.

    Getting this wrong is expensive and quiet — keying on ``case`` alone doubled the
    V3 opportunities' case counts (BERI 553 -> 1173) and halved their per-field
    coverage, because each baby split into a registration-only row and a
    visits-only row (connect-labs#1224/#1225).
    """
    submitted_against: set[str] = set()
    for v in visits:
        cid = _case_id(v)
        if cid:
            submitted_against.add(cid)

    def by_case(visit: dict) -> str | None:
        sub = _subcase_id(visit)
        if sub and sub in submitted_against:
            return sub
        cid = _case_id(visit)
        if cid:
            return cid
        eid = visit.get("entity_id")
        return eid if eid else None

    def by_entity(visit: dict) -> str | None:
        eid = visit.get("entity_id")
        return eid if eid else None

    # A beneficiary key has to actually GROUP. On some apps form.case.@case_id is
    # itself per-visit (opp 675: 505 distinct case ids across 505 visits), and the
    # case-based rule then yields one "baby" per visit — the very failure this
    # resolver exists to prevent, just arrived at from the other direction.
    # So check the candidate against the cohort and fall back to entity_id when the
    # case key is degenerate and entity_id genuinely groups better.
    def _spread(fn) -> tuple[int, int]:
        keys = [fn(v) for v in visits]
        present = [k for k in keys if k]
        return len(set(present)), len(present)

    case_groups, case_seen = _spread(by_case)
    ent_groups, ent_seen = _spread(by_entity)
    case_degenerate = case_seen and case_groups > 0.9 * case_seen
    entity_groups_better = ent_seen and ent_groups and ent_groups < 0.9 * case_groups
    if case_degenerate and entity_groups_better:
        return by_entity
    return by_case


def profile_entity_structure(
    visits: list[dict],
    *,
    numeric_paths: set[str] | None = None,
    date_paths: set[str] | None = None,
    categorical_paths: set[str] | None = None,
) -> EntityStructure:
    # visits per (entity, flw) so we can both count an entity's visits and find
    # the FLW who did the most of them.
    resolve = build_entity_resolver(visits)
    visits_by_entity_flw: dict[str, Counter[str]] = defaultdict(Counter)
    visits_by_entity: dict[str, list[dict]] = defaultdict(list)
    for v in visits:
        eid = resolve(v)
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
        series_visits = []
        for d, v in dated:
            fj = v.get("form_json") or {}
            visit_entry: dict[str, Any] = {"day": (d - first).days, "values": _numeric_leaves(fj, numeric_paths)}
            dates = _date_offsets(fj, date_paths, first)
            if dates:  # omit the key entirely when no dates requested/found (legacy shape)
                visit_entry["dates"] = dates
            cats = _categorical_leaves(fj, categorical_paths)
            if cats:  # omitted when none requested/found, so legacy pools are unchanged
                visit_entry["cats"] = cats
            series_visits.append(visit_entry)
        transplant_pool.append({"owner": entity_owner[eid], "start_date": first.isoformat(), "visits": series_visits})

    return EntityStructure(
        visits_per_entity=visits_per_entity,
        entity_owner=entity_owner,
        owner_visit_counts={k: sorted(v) for k, v in owner_visit_counts.items()},
        transplant_pool=transplant_pool,
    )
