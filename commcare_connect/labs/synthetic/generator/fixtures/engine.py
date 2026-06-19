"""Top-level generator orchestrator.

Composes manifest → timeline → fields → status → user_data → works
into the five fixture dicts the labs synthetic system serves.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import uuid
from typing import Any

from .copula import build_copula_sampler
from .fields import fill_form_json
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

    visits: list[dict[str, Any]] = []
    for slot in slots:
        persona = persona_index[slot.flw_id]
        anomalies = _anomalies_at(slot.week_index, slot.flw_id, manifest)
        correlated_values = copula_sampler.draw() if copula_sampler else None
        form_json = fill_form_json(
            schema=form_schema,
            cohort=cohort,
            anomalies_for_visit=anomalies,
            rng=rng,
            persona=persona,
            period=slot.week_index,
            correlated_values=correlated_values,
        )
        status = decide_visit_status(
            persona=persona,
            has_anomaly=bool(anomalies),
            rng=rng,
            flag_reason_distribution=manifest.flag_reason_distribution,
        )
        # One beneficiary index per visit, reused for the display name AND the
        # household GPS so repeat visits to the same beneficiary share a location.
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
        visits.append(
            {
                "id": rng.getrandbits(60),
                "xform_id": str(uuid.UUID(int=rng.getrandbits(128))),
                "opportunity_id": manifest.opportunity_id,
                "username": persona.id,
                "deliver_unit": str(deliver_unit_id) if deliver_unit_id is not None else "",
                "deliver_unit_id": deliver_unit_id,
                "entity_id": str(uuid.UUID(int=rng.getrandbits(128))),
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
        )

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
