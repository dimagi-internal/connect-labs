"""Legacy random-in-ward primary-record generator.

Pure: no Django, no DB, no network. The offline / no-live-plan fallback that
scatters one arm's primary household-survey records uniformly across a ward
polygon (vs the plan-grounded :func:`..plan.simulate_plan`, which lands records
on real sampled building footprints).
"""

from __future__ import annotations

from commcare_connect.labs.synthetic.generator.core.survey_quality.stats import point_in_geom

from .geo import _ROOF_TYPES, _ROOF_WEIGHTS, _interp, _offset, _sample_in_geom


def scatter_primaries(rng, cfg, arm_key, arm_cfg, geom, round_idx, n_rounds, base_id):
    """Generate one arm's primary records for one round (legacy random-in-ward)."""
    q = cfg["quality"]
    elig = cfg.get("eligibility", {})
    n = max(1, int(round(arm_cfg["n_per_round"] + rng.uniform(-1, 1) * arm_cfg.get("n_jitter", 0))))
    coverage = _interp(arm_cfg["coverage_start"], arm_cfg["coverage_end"], round_idx, n_rounds)
    coverage = max(0.0, coverage + rng.gauss(0, arm_cfg.get("coverage_noise", 0.0)))
    n_enum = arm_cfg.get("enumerators", 5)
    enum_ids = [f"{arm_key[0].upper()}{k + 1}" for k in range(n_enum)]
    # Optional flagged surveyor — one enumerator whose data quality is degraded,
    # so the per-surveyor scorecard (and the back-check) catch them.
    flagged = cfg.get("flagged_surveyor") or {}
    flag_id = flagged.get("id") if flagged.get("arm") == arm_key else None
    near = q.get("gps_offset_near_m", [1, 13])
    far = q.get("gps_offset_far_m", [16, 55])
    dur = q["duration_min"]

    recs = []
    pts = _sample_in_geom(rng, geom, n)
    for j in range(n):
        surveyor = enum_ids[j % n_enum]
        bad = surveyor == flag_id
        gps_p = flagged.get("gps_within_15m", q["gps_within_15m"]) if bad else q["gps_within_15m"]
        ev_p = flagged.get("evidence", q["evidence_complete"]) if bad else q["evidence_complete"]
        alat, alon = pts[j % len(pts)]
        within = rng.random() < gps_p
        offset_m = rng.uniform(*near) if within else rng.uniform(*far)
        clat, clon = _offset(rng, alat, alon, offset_m)
        present = rng.random() < elig.get("present_rate", 0.99)
        age = rng.randint(elig.get("age_min_months", 6), elig.get("age_max_months", 59))
        eligible = present and (elig.get("age_min_months", 6) <= age <= elig.get("age_max_months", 59))
        received = bool(eligible and rng.random() < coverage)
        # duration: occasional implausibly-short record
        if rng.random() < dur.get("short_rate", 0.0):
            duration = round(rng.uniform(*dur.get("short_range", [1, 3])), 1)
        else:
            duration = round(max(dur.get("floor", 4), rng.gauss(dur["mean"], dur["sd"])), 1)
        rec = {
            "record_id": f"{base_id}-p{j}",
            "round": round_idx + 1,
            "form_type": "primary",
            "household_id": f"{base_id}-H{j:04d}",
            "ward": arm_cfg["ward"],
            "arm": arm_key,
            "enumerator_id": surveyor,
            "lat": round(clat, 6),
            "lon": round(clon, 6),
            "assigned_lat": round(alat, 6),
            "assigned_lon": round(alon, 6),
            "gps_offset_m": round(offset_m, 1),
            "in_ward": point_in_geom(geom, clat, clon),
            "start_ts": 1_700_000_000 + round_idx * 5_000_000 + j * 900,
            "end_ts": 1_700_000_000 + round_idx * 5_000_000 + j * 900 + int(duration * 60),
            "duration_min": duration,
            "evidence_photo": rng.random() < ev_p,
            "child_present": present,
            "child_sex": rng.choice(["M", "F"]),
            "child_age_months": age,
            "roof_type": rng.choices(_ROOF_TYPES, weights=_ROOF_WEIGHTS, k=1)[0],
            "eligible": eligible,
            "vitamin_a_received": received,
            "dose_source": rng.choice(["campaign", "routine", "facility"]) if received else None,
            "original_record_id": None,
            "original_enumerator_id": None,
        }
        # rare required-field drop -> exercises completeness metric
        if rng.random() > q.get("field_complete", 1.0):
            rec[rng.choice(["evidence_photo", "child_age_months"])] = None
        recs.append(rec)
    return recs
