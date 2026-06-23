"""Top-level generator orchestrator.

Composes manifest → timeline → fields → status → user_data → works
into the five fixture dicts the labs synthetic system serves.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
import random
import uuid
from typing import Any

from .copula import build_copula_sampler
from .entities import plan_mirror_visits
from .fields import _outlier, fill_form_json
from .images import assign_visit_images
from .manifest import Manifest
from .opportunity import build_opportunity
from .schema_loader import FormSchema
from .status import decide_visit_status
from .tasks import build_task_records
from .timeline import expand_visit_schedule
from .user_data import build_user_data
from .works import build_works_and_modules


def _sample_hour(rng: random.Random, temporal) -> int:
    """Return the base hour for visit timestamps.

    When *temporal* is present and its ``hour_of_day`` weights are non-zero,
    draw one hour via weighted sampling.  Otherwise return 11 (legacy default)
    WITHOUT consuming any rng draws — so the None-temporal golden output is
    byte-identical to the previous hardcoded timestamps.
    """
    if temporal and sum(temporal.hour_of_day) > 0:
        return rng.choices(range(24), weights=temporal.hour_of_day, k=1)[0]
    return 11  # legacy default base hour


def _anomalies_at(week_index: int, flw_id: str, manifest: Manifest):
    out = []
    for a in manifest.anomalies:
        if flw_id not in a.flw_ids:
            continue
        if a.week and a.week == week_index:
            out.append(a)
        elif a.weeks and week_index in a.weeks:
            out.append(a)
    return out


def _persona_index(manifest: Manifest):
    return {p.id: p for p in manifest.flw_personas}


_DUPLICATE_FLAG_REASON = "Beneficiary already visited this week"


def _make_duplicate(visit: dict[str, Any], created_dt: dt.datetime, rng: random.Random) -> dict[str, Any]:
    """A near-identical resubmission of ``visit`` for the duplicate_submission anomaly.

    Same beneficiary (entity_id), same date, and a deep-copied identical form_json,
    submitted a few minutes later with a fresh id/xform_id and a dedup flag. The
    original is left clean; only this copy carries the flag, so a dedup check finds a
    matched pair rather than two independently-flagged rows.
    """
    dup_created = created_dt + dt.timedelta(minutes=rng.randint(2, 25))
    dup = copy.deepcopy(visit)
    dup.update(
        {
            "id": rng.getrandbits(60),
            "xform_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "status": "pending",
            "flagged": True,
            "flag_reason": _DUPLICATE_FLAG_REASON,
            "review_status": "pending",
            "status_modified_date": (dup_created + dt.timedelta(hours=1)).isoformat(),
            "review_created_on": (dup_created + dt.timedelta(hours=1, minutes=30)).isoformat(),
            "date_created": dup_created.isoformat(),
        }
    )
    return dup


def _payment_units(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return detail.get("payment_units", [])


def _default_deliver_unit(detail: dict[str, Any]) -> int | None:
    units = detail.get("deliver_units") or []
    return units[0]["id"] if units else None


def _build_household_locations(geography, cohort_size: int, rng: random.Random) -> dict[int, tuple[float, float]]:
    """Place one fixed household point (lon, lat) per beneficiary index, scattered
    across a few settlement clusters inside the geography polygon. Deterministic
    given ``rng``. Visits to the same beneficiary then stack at the same point."""
    from shapely.geometry import Point, shape

    poly = shape(geography.polygon)
    minx, miny, maxx, maxy = poly.bounds

    def rand_in_poly() -> Point:
        for _ in range(20000):
            p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
            if poly.contains(p):
                return p
        return poly.representative_point()

    centers = [rand_in_poly() for _ in range(int(geography.settlements))]
    spread = float(geography.settlement_spread_km)
    locations: dict[int, tuple[float, float]] = {}
    for bidx in range(1, cohort_size + 1):
        c = centers[rng.randrange(len(centers))]
        dlat = rng.gauss(0.0, spread) / 111.0
        dlon = rng.gauss(0.0, spread) / (111.0 * max(0.1, math.cos(math.radians(c.y))))
        p = Point(c.x + dlon, c.y + dlat)
        if not poly.contains(p):
            p = c  # offset wandered outside the ward — clamp to the settlement center
        locations[bidx] = (p.x, p.y)
    return locations


def _packed_location(locations, bidx: int, geography, rng: random.Random) -> str:
    """CommCare packed GPS string 'lat lon altitude accuracy' for a beneficiary's
    household; empty string when no geography is configured."""
    if not geography or locations is None:
        return ""
    lon, lat = locations[bidx]
    alt = rng.gauss(geography.altitude_m.mean, geography.altitude_m.stddev)
    acc = rng.uniform(geography.accuracy_m_min, geography.accuracy_m_max)
    return f"{lat:.6f} {lon:.6f} {alt:.0f} {acc:.0f}"


def _build_mirror_visits(
    *,
    manifest: Manifest,
    cohort,
    longitudinal,
    form_schema: FormSchema,
    rng: random.Random,
    persona_index: dict[str, Any],
    personas,
    deliver_unit_id: int | None,
) -> list[dict[str, Any]]:
    """Replay the transplant pool: one stable entity per real case, with its owner
    FLW, timing, visit count, and (jittered) value trajectory reproduced exactly."""
    planned = plan_mirror_visits(longitudinal, seed=manifest.random_seed)
    entity_count = max((pv.beneficiary_idx for pv in planned), default=0)
    household_locations = (
        _build_household_locations(manifest.geography, entity_count, rng)
        if manifest.geography and entity_count
        else None
    )
    start_date = manifest.timeline.start_date

    visits: list[dict[str, Any]] = []
    for pv in planned:
        persona = persona_index.get(pv.owner) or personas[0]
        location = _packed_location(household_locations, pv.beneficiary_idx, manifest.geography, rng)
        # week index relative to the timeline, for any non-forced period-varying draws.
        period = max(1, (pv.visit_date - start_date).days // 7 + 1)
        # Seeded QA anomalies still apply under faithful replay (mirror is the clone
        # default since #734): a missing_visits drops this visit, a field_outlier
        # corrupts one transplanted measure, a duplicate_submission appends a flagged
        # copy — so curated mirror clones keep the signal audits/evals look for.
        anomalies = _anomalies_at(period, persona.id, manifest)
        if any(a.type == "missing_visits" for a in anomalies):
            continue
        forced_values = pv.forced_values
        outlier_paths = [
            a.field_path for a in anomalies if a.type == "field_outlier" and a.field_path in cohort.field_distributions
        ]
        if outlier_paths:
            forced_values = dict(forced_values)
            for path in outlier_paths:
                forced_values[path] = _outlier(cohort.field_distributions[path], rng)
        form_json = fill_form_json(
            schema=form_schema,
            cohort=cohort,
            anomalies_for_visit=[],
            rng=rng,
            persona=persona,
            period=period,
            forced_values=forced_values,
            mirror=True,
        )
        if location:
            form_json.setdefault("metadata", {})["location"] = location
        status = decide_visit_status(
            persona=persona,
            has_anomaly=bool(outlier_paths),
            rng=rng,
            flag_reason_distribution=manifest.flag_reason_distribution,
        )
        base_hour = _sample_hour(rng, manifest.temporal)
        created_dt = dt.datetime.combine(pv.visit_date, dt.time(base_hour, 0))
        visit = {
            "id": rng.getrandbits(60),
            "xform_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "opportunity_id": manifest.opportunity_id,
            "username": persona.id,
            "deliver_unit": str(deliver_unit_id) if deliver_unit_id is not None else "",
            "deliver_unit_id": deliver_unit_id,
            "entity_id": pv.entity_id,
            "entity_name": pv.entity_name,
            "visit_date": pv.visit_date.isoformat(),
            "status": status.status,
            "reason": None,
            "location": location,
            "flagged": status.flagged,
            "flag_reason": status.flag_reason,
            "form_json": form_json,
            "completed_work": "",
            "status_modified_date": (created_dt + dt.timedelta(hours=1)).isoformat(),
            "review_status": status.review_status,
            "review_created_on": (created_dt + dt.timedelta(hours=1, minutes=30)).isoformat(),
            "justification": None,
            "date_created": created_dt.isoformat(),
            "completed_work_id": None,
            "images": [],
        }
        visits.append(visit)
        # A duplicate_submission resubmits the same case minutes later with a dedup
        # flag — the "already visited" signal a QA check is meant to catch.
        if any(a.type == "duplicate_submission" for a in anomalies):
            visits.append(_make_duplicate(visit, created_dt, rng))
    return visits


def _progression_sign(progression: str) -> int:
    """Trend direction for net-new trajectories: improvement rises, regression falls,
    flat has no trend (value stays near its per-entity intercept plus noise/AR)."""
    return {"improvement_curve": 1, "regression": -1, "flat": 0}.get(progression, 0)


def _synthetic_traj_step(traj, sign, entity_state, traj_rng, idx, visit_date):
    """Advance one entity's net-new trajectory and return (forced_values, entity_id).

    On first sight of an entity, sample its latent params (intercept = starting
    value, slope = per-x change) once and mint a stable id. Each visit's value is
    ``intercept + sign*slope*x + noise`` (x = days since the entity's first visit,
    or visit index), blended with the previous value for an autoregressive field.
    """
    st = entity_state.get(idx)
    if st is None:
        latent = {}
        for path, tp in traj.fields.items():
            intercept = traj_rng.gauss(tp.intercept.mean, tp.intercept.stddev)
            slope = traj_rng.gauss(tp.slope.mean, tp.slope.stddev)
            latent[path] = (intercept, slope)
        st = {
            "first_date": visit_date,
            "visit_index": 0,
            "last": {},
            "latent": latent,
            "id": str(uuid.UUID(int=traj_rng.getrandbits(128))),
        }
        entity_state[idx] = st
    st["visit_index"] += 1
    forced: dict[str, float] = {}
    for path, tp in traj.fields.items():
        intercept, slope = st["latent"][path]
        x = (visit_date - st["first_date"]).days if tp.x_axis == "day" else st["visit_index"] - 1
        base = intercept + sign * slope * x
        if tp.model == "autoregressive" and path in st["last"]:
            base = tp.autocorr * st["last"][path] + (1 - tp.autocorr) * base
        val = base + (traj_rng.gauss(0, tp.residual_std) if tp.residual_std else 0.0)
        st["last"][path] = val
        forced[path] = val
    return forced, st["id"]


def generate(
    *,
    manifest: Manifest,
    opportunity_detail: dict[str, Any],
    form_schema: FormSchema,
    app_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rng = random.Random(manifest.random_seed)
    personas = manifest.flw_personas
    persona_index = _persona_index(manifest)
    cohort = manifest.beneficiary_cohorts[0]  # v1 supports the primary cohort
    deliver_unit_id = _default_deliver_unit(opportunity_detail)
    payment_units = _payment_units(opportunity_detail)

    # Mirror mode replays real de-identified case series as stable entities; it
    # bypasses the FLW-cadence slot schedule because it reproduces the source's
    # exact owner/timing/visit-count structure directly.
    longitudinal = cohort.longitudinal
    if longitudinal is not None and longitudinal.mode == "mirror":
        visits = _build_mirror_visits(
            manifest=manifest,
            cohort=cohort,
            longitudinal=longitudinal,
            form_schema=form_schema,
            rng=rng,
            persona_index=persona_index,
            personas=personas,
            deliver_unit_id=deliver_unit_id,
        )
        return _assemble(
            manifest=manifest,
            visits=visits,
            personas=personas,
            payment_units=payment_units,
            opportunity_detail=opportunity_detail,
            app_structure=app_structure,
            rng=rng,
        )

    household_locations = (
        _build_household_locations(manifest.geography, cohort.size, rng) if manifest.geography else None
    )

    slots = expand_visit_schedule(
        manifest.timeline,
        personas,
        random_seed=manifest.random_seed,
        day_of_week=(manifest.temporal.day_of_week if manifest.temporal else None),
    )
    slots.sort(key=lambda s: (s.visit_date, s.flw_id))

    copula_sampler = build_copula_sampler(cohort.correlation, cohort.field_distributions, seed=manifest.random_seed)

    # Net-new (synthetic) longitudinal: give each beneficiary a stable id and a
    # per-entity trajectory threaded across its repeat visits. Inactive (traj None)
    # leaves every rng draw in its legacy position, so default output is unchanged.
    traj = longitudinal if (longitudinal is not None and longitudinal.mode == "synthetic") else None
    traj_sign = _progression_sign(cohort.progression) if traj else 0
    traj_rng = random.Random(manifest.random_seed ^ 0x7137A1) if traj else None
    entity_state: dict[int, dict[str, Any]] = {}

    visits: list[dict[str, Any]] = []
    for slot in slots:
        persona = persona_index[slot.flw_id]
        anomalies = _anomalies_at(slot.week_index, slot.flw_id, manifest)
        # A missing_visits anomaly removes the targeted FLW's visits for this week,
        # leaving a detectable coverage gap (the visit is simply never emitted).
        if any(a.type == "missing_visits" for a in anomalies):
            continue
        correlated_values = copula_sampler.draw() if copula_sampler else None
        forced_values = None
        traj_entity_id = None
        if traj:
            # Pick the entity first (so its trajectory can drive this visit), then
            # advance its per-entity series.
            beneficiary_idx = rng.randint(1, cohort.size)
            forced_values, traj_entity_id = _synthetic_traj_step(
                traj, traj_sign, entity_state, traj_rng, beneficiary_idx, slot.visit_date
            )
        form_json = fill_form_json(
            schema=form_schema,
            cohort=cohort,
            anomalies_for_visit=anomalies,
            rng=rng,
            persona=persona,
            period=slot.week_index,
            correlated_values=correlated_values,
            forced_values=forced_values,
        )
        # Only a within-visit anomaly (a seeded field_outlier) flags the genuine
        # visit. A duplicate_submission flags its injected copy instead (below), so
        # the original stays clean — mirroring how a real dedup check fires.
        status = decide_visit_status(
            persona=persona,
            has_anomaly=any(a.type == "field_outlier" for a in anomalies),
            rng=rng,
            flag_reason_distribution=manifest.flag_reason_distribution,
        )
        # One beneficiary index per visit, reused for the display name AND the
        # household GPS so repeat visits to the same beneficiary share a location.
        # Under a synthetic trajectory the index was already drawn above (to drive
        # the entity's series), so don't draw a second one — keeps legacy draw order.
        if not traj:
            beneficiary_idx = rng.randint(1, cohort.size)
        location = _packed_location(household_locations, beneficiary_idx, manifest.geography, rng)
        # The service-delivery GPS pipeline (SERVICE_DELIVERY_GPS_SCHEMA) reads the
        # device location from form_json.metadata.location (packed "lat lon alt acc"),
        # mirroring real CommCare submissions. The top-level `location` field alone is
        # invisible to that pipeline, so mirror it into metadata.location here.
        if location:
            form_json.setdefault("metadata", {})["location"] = location
        base_hour = _sample_hour(rng, manifest.temporal)
        created_dt = dt.datetime.combine(slot.visit_date, dt.time(base_hour, 0))
        # Visit id MUST be a PostgreSQL bigint-compatible integer — the audit
        # data-access layer and labs cache both type the column as int, and a
        # UUID-string id breaks `filter_visit_ids=set([...])` lookups + the
        # `/audit/api/<id>/bulk-data/` rendering path (500s on type mismatch).
        # 60 bits ≈ 1e18, way under bigint max; chance of collision with another
        # synthetic opp is vanishingly small for demo-scale fixture sets.
        visit = {
            "id": rng.getrandbits(60),
            "xform_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "opportunity_id": manifest.opportunity_id,
            "username": persona.id,
            "deliver_unit": str(deliver_unit_id) if deliver_unit_id is not None else "",
            "deliver_unit_id": deliver_unit_id,
            "entity_id": traj_entity_id if traj else str(uuid.UUID(int=rng.getrandbits(128))),
            "entity_name": f"Beneficiary {beneficiary_idx}",
            "visit_date": slot.visit_date.isoformat(),
            "status": status.status,
            "reason": None,
            "location": location,
            "flagged": status.flagged,
            "flag_reason": status.flag_reason,
            "form_json": form_json,
            "completed_work": "",
            "status_modified_date": (created_dt + dt.timedelta(hours=1)).isoformat(),
            "review_status": status.review_status,
            "review_created_on": (created_dt + dt.timedelta(hours=1, minutes=30)).isoformat(),
            "justification": None,
            "date_created": created_dt.isoformat(),
            "completed_work_id": None,
            "images": [],
        }
        visits.append(visit)

        # A duplicate_submission anomaly resubmits the same visit minutes later:
        # same beneficiary, identical form_json, but a new id and a dedup flag — the
        # signal an audit/QA "already visited" check is meant to catch.
        if any(a.type == "duplicate_submission" for a in anomalies):
            visits.append(_make_duplicate(visit, created_dt, rng))

    return _assemble(
        manifest=manifest,
        visits=visits,
        personas=personas,
        payment_units=payment_units,
        opportunity_detail=opportunity_detail,
        app_structure=app_structure,
        rng=rng,
    )


def _assemble(
    *,
    manifest: Manifest,
    visits: list[dict[str, Any]],
    personas,
    payment_units: list[dict[str, Any]],
    opportunity_detail: dict[str, Any],
    app_structure: dict[str, Any] | None,
    rng: random.Random,
) -> dict[str, Any]:
    """Shared tail: images, tasks, user_data, works/modules, opportunity. Both the
    legacy slot-based path and the mirror path build a ``visits`` list and assemble
    the five fixture endpoints the same way from here."""
    if manifest.image_config:
        assign_visit_images(visits, manifest.image_config, rng)

    persona_names = {p.id: p.display_name or p.id for p in personas}
    task_records = build_task_records(
        opportunity_id=manifest.opportunity_id,
        tasks=manifest.tasks,
        coaching_arcs=manifest.coaching_arcs,
        timeline=manifest.timeline,
        persona_names=persona_names,
    )

    user_data = build_user_data(personas, visits)
    works, modules = build_works_and_modules(visits, payment_units)
    opportunity = build_opportunity(opportunity_detail, opportunity_name_override=manifest.opportunity_name)

    return {
        "opportunity": opportunity,
        "user_visits": visits,
        "user_data": user_data,
        "completed_works": works,
        "completed_module": modules,
        "task_records": task_records,
        "app_structure": app_structure,
    }
