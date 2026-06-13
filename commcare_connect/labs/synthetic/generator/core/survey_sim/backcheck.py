"""Independent back-check re-survey simulator.

Pure: no Django, no DB, no network. Re-survey a stratified sample of primary
records with a different enumerator, injecting the three J-PAL back-check error
types (identity / location / outcome) at configured agreement rates — and at
degraded rates for a flagged surveyor, so the back-check error rate catches them.
"""

from __future__ import annotations

from .geo import _ROOF_TYPES, _offset


def simulate_backchecks(rng, cfg, primaries, round_idx, base_id):
    """Re-survey a stratified sample of primaries with a different enumerator."""
    bc = cfg["backcheck"]
    n_bc_enum = bc.get("enumerators", 3)
    bc_ids = [f"BC{k + 1}" for k in range(n_bc_enum)]
    pct = bc["sample_pct"]
    if round_idx < bc.get("front_load_rounds", 0):
        pct = bc.get("front_load_pct", pct)
    # stratify by enumerator so every enumerator gets covered
    by_enum = {}
    for r in primaries:
        by_enum.setdefault(r["enumerator_id"], []).append(r)
    selected = []
    for _enum, rs in by_enum.items():
        k = max(1, int(round(len(rs) * pct)))
        selected.extend(rng.sample(rs, min(k, len(rs))))

    flagged = cfg.get("flagged_surveyor") or {}
    out = []
    for idx, o in enumerate(selected):
        # Re-survey values: agree most of the time, perturb otherwise. A flagged
        # surveyor's originals agree LESS with the independent re-survey — the
        # signal that catches them (the J-PAL back-check error rate per surveyor).
        is_flagged = flagged.get("id") == o.get("enumerator_id") and flagged.get("arm") == o.get("arm")
        # Type-3 (outcome): a flagged surveyor's originals agree LESS with the
        # independent re-survey — the headline back-check signal that catches them.
        agree_p = (
            flagged.get("backcheck_agreement", bc["outcome_agreement"]) if is_flagged else bc["outcome_agreement"]
        )
        outcome = o["vitamin_a_received"]
        if rng.random() > agree_p:
            outcome = not outcome
        sex = o["child_sex"]
        present = o["child_present"]
        age = o["child_age_months"]
        roof = o.get("roof_type")
        # Type-1 (identity): a flagged surveyor also shows more identity discordance.
        # Covers the respondent (sex/age/present) AND the household (roof type).
        t1_p = flagged.get("backcheck_type1_agreement", bc["type1_agreement"]) if is_flagged else bc["type1_agreement"]
        if rng.random() > t1_p:
            # introduce a Type-1 discordance on one identifier
            roll = rng.random()
            if roll < 0.25:
                sex = "M" if sex == "F" else "F"
            elif roll < 0.5:
                present = not present
            elif roll < 0.75:
                roof = rng.choice([r for r in _ROOF_TYPES if r != roof] or _ROOF_TYPES)
            elif age is not None:
                age = age + rng.choice([-4, 4, 6])
        # Type-2 (location/protocol): the re-survey lands further from a flagged
        # surveyor's claimed household — their original location was sloppy.
        off_m = rng.uniform(22, 45) if is_flagged else rng.uniform(2, 12)
        blat, blon = _offset(rng, o["assigned_lat"], o["assigned_lon"], off_m)
        out.append(
            {
                "record_id": f"{base_id}-b{idx}",
                "round": round_idx + 1,
                "form_type": "back_check",
                "household_id": o["household_id"],
                "ward": o["ward"],
                "arm": o["arm"],
                "enumerator_id": bc_ids[idx % n_bc_enum],
                "lat": round(blat, 6),
                "lon": round(blon, 6),
                "assigned_lat": o["assigned_lat"],
                "assigned_lon": o["assigned_lon"],
                "gps_offset_m": round(off_m, 1),
                "in_ward": True,
                "start_ts": o["start_ts"] + 86_400,
                "end_ts": o["start_ts"] + 86_400 + 600,
                "duration_min": round(rng.gauss(9, 2), 1),
                "evidence_photo": o["evidence_photo"],  # not a back-check variable; carried, not re-drawn
                "child_present": present,
                "child_sex": sex,
                "child_age_months": age,
                "roof_type": roof,
                "eligible": o["eligible"],
                "vitamin_a_received": outcome,
                "dose_source": o["dose_source"],
                "original_record_id": o["record_id"],
                "original_enumerator_id": o["enumerator_id"],
            }
        )
    return out
