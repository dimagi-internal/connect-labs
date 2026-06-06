"""Parameterized synthetic survey generator for the Verified Monitoring demo.

Robust + reusable: drive it from a per-program JSON config (see
``demo_config.json``) and it emits, **for each round** (each round modelled as its
own opportunity), row-level household survey records — a ``primary`` form and a
``back_check`` form per the design — then computes every dashboard KPI from those
records via the shared ``commcare_connect.labs.survey_quality`` algorithm library.
Nothing is hand-entered: coverage, the QA strip, the back-check drill-down, and
the map pins all roll up from the generated rows, so "let the numbers be
computed" is honest and internally consistent. Point it at a different geojson +
config and you get the same dashboard for any program.

The output is the workflow ``instance.state`` payload the render reads.
"""

from __future__ import annotations

import math
import random

# Make the repo root importable so the shared algorithm library resolves whether
# this runs from a checkout or a management context.
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from commcare_connect.labs.survey_quality import results_to_map, run_metrics  # noqa: E402
from commcare_connect.labs.survey_quality.stats import bbox, point_in_geom  # noqa: E402

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_M_PER_DEG = 111_320.0  # metres per degree latitude (good enough at this scale)
# Stable household attribute used as a Type-1 back-check identifier (a roof can't
# change between two visits a week apart — a mismatch is a fabrication signal).
_ROOF_TYPES = ["thatch", "metal sheet", "mud", "tile"]
_ROOF_WEIGHTS = [0.42, 0.34, 0.16, 0.08]


# --------------------------------------------------------------- geometry utils


def _load_wards(path: Path) -> dict:
    import json

    fc = json.loads(Path(path).read_text())
    return {f["properties"]["name"]: f for f in fc["features"]}


def _sample_in_geom(rng: random.Random, geom: dict, n: int) -> list:
    x0, y0, x1, y1 = bbox(geom)
    pts, guard = [], 0
    while len(pts) < n and guard < n * 200:
        guard += 1
        lon, lat = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if point_in_geom(geom, lat, lon):
            pts.append((lat, lon))
    return pts


def _offset(rng: random.Random, lat: float, lon: float, meters: float) -> tuple:
    """Move a point by ``meters`` in a random bearing (small-distance approx)."""
    bearing = rng.uniform(0, 2 * math.pi)
    dlat = (meters * math.cos(bearing)) / _M_PER_DEG
    dlon = (meters * math.sin(bearing)) / (_M_PER_DEG * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def _interp(a: float, b: float, i: int, n: int) -> float:
    return a if n <= 1 else a + (b - a) * (i / (n - 1))


def _round_label(round0: str, i: int) -> tuple:
    y, m = (int(x) for x in round0.split("-"))
    total = (y * 12 + (m - 1)) + 2 * i  # bi-monthly
    yy, mm = divmod(total, 12)
    return f"{_MONTHS[mm]} {yy}", f"{yy:04d}-{mm + 1:02d}-15"


# --------------------------------------------------------------- record gen


def _gen_arm_round(rng, cfg, arm_key, arm_cfg, geom, round_idx, n_rounds, base_id):
    """Generate one arm's primary records for one round."""
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


def _gen_backchecks(rng, cfg, primaries, round_idx, base_id):
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


# --------------------------------------------------------------- assembly


def _coverage(records, arm):
    elig = [r for r in records if r["form_type"] == "primary" and r["arm"] == arm and r["eligible"]]
    if not elig:
        return None, 0
    got = sum(1 for r in elig if r["vitamin_a_received"])
    return round(100.0 * got / len(elig), 1), len(elig)


_REQUIRED_FIELDS = ["lat", "lon", "start_ts", "enumerator_id", "vitamin_a_received"]


def _quality_record_sample(cfg, surveyor_prims, all_prims, max_rows=None):
    """One row per survey for the metric drill-through — EVERY one of a
    surveyor's primary records with the per-record values + flags the scorecard
    rolls up (so clicking a quality cell shows the full census of surveys, not a
    sample). Flagged rows sort first so the interesting cases surface."""
    from collections import Counter

    floor = ((cfg.get("quality") or {}).get("duration_min") or {}).get("floor", 4)
    hh_counts = Counter(r["household_id"] for r in all_prims)
    sig_counts = Counter((r.get("lat"), r.get("lon"), r.get("start_ts")) for r in all_prims)
    out = []
    for r in surveyor_prims:
        recv = bool(r.get("vitamin_a_received"))
        photo = r.get("evidence_photo")
        dur = r.get("duration_min")
        miss = [k for k in _REQUIRED_FIELDS if r.get(k) is None]
        cons = not (
            (recv and not r.get("child_present"))
            or (recv and not r.get("eligible"))
            or (r.get("child_present") and r.get("child_age_months") is None)
        )
        dup = hh_counts[r["household_id"]] > 1 or sig_counts[(r.get("lat"), r.get("lon"), r.get("start_ts"))] > 1
        gps = r.get("gps_offset_m")
        rec = {
            "hh": r["household_id"],
            "recv": recv,
            "photo": (bool(photo) if photo is not None else None),
            "gps": (round(gps, 1) if gps is not None else None),
            "dur": dur,
            "short": (dur is not None and dur < floor),
            "miss": miss,
            "cons": cons,
            "dup": dup,
        }
        rec["_flagged"] = (
            (recv and rec["photo"] is not True)
            or (gps is not None and gps > 15)
            or rec["short"]
            or bool(miss)
            or (not cons)
            or dup
        )
        out.append(rec)
    out.sort(key=lambda x: (not x["_flagged"], str(x["hh"])))
    for x in out:
        x.pop("_flagged", None)
    return out if max_rows is None else out[:max_rows]


def _surveyor_scorecard(cfg, records):
    """Per-surveyor quality KPIs for the program ward — one row per surveyor for
    the scorecard. Each surveyor's metrics are computed from their OWN records via
    the shared library (the same algorithms as the round-level KPIs)."""
    tw = [r for r in records if r.get("arm") == "treatment"]
    all_prims = [r for r in tw if r["form_type"] == "primary"]
    surveyors = sorted({r["enumerator_id"] for r in all_prims})
    rows = []
    for s in surveyors:
        prims = [r for r in all_prims if r["enumerator_id"] == s]
        sub = prims + [r for r in tw if r["form_type"] == "back_check" and r.get("original_enumerator_id") == s]
        qm = results_to_map(run_metrics(sub, layers=["survey_quality"], config=cfg))
        bm = results_to_map(run_metrics(sub, layers=["backcheck"], config=cfg))

        def _v(m, key):
            return (m.get(key) or {}).get("value")

        rows.append(
            {
                "surveyor": s,
                "n": (qm.get("field_completeness") or {}).get("n"),
                "evidence": _v(qm, "evidence_capture"),
                "gps": _v(qm, "gps_within_15m"),
                "completeness": _v(qm, "field_completeness"),
                "duration": _v(qm, "duration_plausibility"),
                "consistency": _v(qm, "consistency_pass"),
                "duplicates": _v(qm, "duplicate_integrity"),
                "backcheck": _v(bm, "backcheck_outcome_agreement"),
                "backcheck_n": (bm.get("backcheck_coverage") or {}).get("n"),
                "records": _quality_record_sample(cfg, prims, all_prims),
            }
        )
    return rows


def _surveyor_backcheck(cfg, all_records, t2_thresh_m=25.0, max_rows=15):
    """Per-surveyor back-check profile across ALL cycles, by J-PAL type.

    A single cycle's per-surveyor back-check sample is too small for the binary
    signals (identity, outcome) to settle — the back-check is designed to
    accumulate. So when a surveyor is selected, the dashboard shows their
    cumulative profile (n ~ sample_pct x rounds), where the three types separate:
      Type 1 (identity, zero-tolerance) = 100 - Type-1 discordance rate
      Type 2 (location / protocol)      = % re-surveys co-located (<= t2 m of original)
      Type 3 (outcome)                  = vitamin-A agreement (paired match)
    Each is computed from the surveyor's own records via the shared library."""
    tw = [r for r in all_records if r.get("arm") == "treatment"]
    surveyors = sorted({r["enumerator_id"] for r in tw if r["form_type"] == "primary"})
    out = {}
    for s in surveyors:
        sub = [
            r
            for r in tw
            if (r["form_type"] == "primary" and r["enumerator_id"] == s)
            or (r["form_type"] == "back_check" and r.get("original_enumerator_id") == s)
        ]
        m = results_to_map(run_metrics(sub, layers=["backcheck"], config=cfg))
        rows = (m.get("backcheck_comparison", {}).get("detail", {}) or {}).get("rows", [])
        n = len(rows)
        if not n:
            continue
        t1_err = (m.get("backcheck_type1_error") or {}).get("value")
        t2_ok = sum(1 for r in rows if r.get("gps_delta_m") is not None and r["gps_delta_m"] <= t2_thresh_m)
        out[s] = {
            "n": n,
            "type1_pct": round(100.0 - t1_err, 1) if t1_err is not None else None,
            "type2_pct": round(100.0 * t2_ok / n, 1),
            "type3_pct": (m.get("backcheck_outcome_agreement") or {}).get("value"),
            "prtest_p": (m.get("backcheck_outcome_prtest") or {}).get("value"),
            "t2_thresh_m": t2_thresh_m,
            "rows": rows if max_rows is None else rows[:max_rows],
        }
    return out


def _round_summary(cfg, records, round_idx, label, as_of, tw_name, cw_name):
    # The dashboard hero + QA strip + back-check defend the PROGRAM ward's
    # coverage claim, so compute those metrics over the treatment arm only (the
    # comparison ward is a descriptive reference, summarised via _coverage).
    tw_records = [r for r in records if r.get("arm") == "treatment"]
    qmap = results_to_map(run_metrics(tw_records, layers=["survey_quality"], config=cfg))
    bmap = results_to_map(run_metrics(tw_records, layers=["backcheck"], config=cfg))
    t_pct, t_n = _coverage(records, "treatment")
    c_pct, c_n = _coverage(records, "comparison")
    infl = _interp(
        cfg["self_report"]["inflation_start"], cfg["self_report"]["inflation_end"], round_idx, cfg["rounds"]
    )
    sr_noise = cfg["self_report"].get("noise", 0.0)
    self_report = round(min(100.0, (t_pct or 0) * infl * (1 + random_jitter(cfg, round_idx, sr_noise))), 1)
    premium = round(self_report - (t_pct or 0), 1)
    bc = bmap.get("backcheck_comparison", {}).get("detail", {})
    return {
        "round": round_idx + 1,
        "label": label,
        "as_of": as_of,
        "treatment_ward": tw_name,
        "comparison_ward": cw_name,
        "intervention_pct": t_pct,
        "comparison_pct": c_pct,
        "gap_pp": round((t_pct or 0) - (c_pct or 0), 1),
        "intervention_n": t_n,
        "comparison_n": c_n,
        "self_report_pct": self_report,
        "premium_pp": premium,
        "quality": qmap,
        "surveyor_scorecard": _surveyor_scorecard(cfg, records),
        "backcheck": {
            "coverage_pct": bmap.get("backcheck_coverage", {}).get("value"),
            "n_backchecked": bmap.get("backcheck_coverage", {}).get("n"),
            "type1_error_pct": bmap.get("backcheck_type1_error", {}).get("value"),
            "type1_per_enumerator": bmap.get("backcheck_type1_error", {})
            .get("detail", {})
            .get("per_enumerator_error", {}),
            "outcome_agreement_pct": bmap.get("backcheck_outcome_agreement", {}).get("value"),
            "prtest_p": bmap.get("backcheck_outcome_prtest", {}).get("value"),
            "prtest_passed": bmap.get("backcheck_outcome_prtest", {}).get("passed"),
            "orig_pct": bmap.get("backcheck_outcome_prtest", {}).get("detail", {}).get("orig_pct"),
            "bc_pct": bmap.get("backcheck_outcome_prtest", {}).get("detail", {}).get("backcheck_pct"),
            "n_mismatch": bmap.get("backcheck_comparison", {}).get("value"),
            "rows": bc.get("rows", []),
        },
    }


# deterministic per-round jitter for self-report (seeded so reruns match)
_JITTER_CACHE = {}


def random_jitter(cfg, round_idx, noise):
    if noise <= 0:
        return 0.0
    key = (cfg.get("rng_seed"), round_idx)
    if key not in _JITTER_CACHE:
        r = random.Random(hash(key) & 0xFFFFFFFF)
        _JITTER_CACHE[key] = r.uniform(-noise, noise)
    return _JITTER_CACHE[key]


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _pt(lat, lon, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def _pins_sample(rng, records, cap_per_ward):
    """A legible per-ward sample of survey-pin features from the primary records."""
    by_ward = {}
    for r in records:
        if r["form_type"] != "primary" or r.get("lat") is None:
            continue
        by_ward.setdefault(r["ward"], []).append(r)
    feats = []
    for _ward, rs in by_ward.items():
        pick = rs if len(rs) <= cap_per_ward else rng.sample(rs, cap_per_ward)
        for r in pick:
            feats.append(_pt(r["lat"], r["lon"], {"confirmed": bool(r["vitamin_a_received"]), "ward": r["ward"]}))
    return _fc(feats)


def build_state(cfg: dict, here: Path) -> tuple:
    """Generate all rounds and assemble the workflow instance.state payload.

    Rotating wards: each round surveys a DIFFERENT (program, comparison) ward
    pair (``cfg["rounds_wards"]``), so the map moves cycle to cycle. Every round
    carries its own ward names + map overlay; the render reads them per round.

    Returns (state, all_records)."""
    rng = random.Random(cfg["rng_seed"])
    wards = _load_wards(here / cfg["wards_geojson"])  # ALL wards, keyed by name
    pairs = cfg["rounds_wards"]
    n_rounds = cfg["rounds"]
    sd_cfg = cfg.get("service_delivery", {})
    map_pin_cap = cfg.get("map_pin_cap", 160)

    all_records, rounds = [], []
    for ri in range(n_rounds):
        pair = pairs[ri % len(pairs)]
        tw, cw = pair["treatment"], pair["comparison"]
        tgeom, cgeom = wards[tw]["geometry"], wards[cw]["geometry"]
        label, as_of = _round_label(cfg["round0"], ri)
        base_id = f"r{ri + 1}"
        arm_cfg = {
            "treatment": {**cfg["arms"]["treatment"], "ward": tw},
            "comparison": {**cfg["arms"]["comparison"], "ward": cw},
        }
        recs = _gen_arm_round(rng, cfg, "treatment", arm_cfg["treatment"], tgeom, ri, n_rounds, f"{base_id}t")
        recs += _gen_arm_round(rng, cfg, "comparison", arm_cfg["comparison"], cgeom, ri, n_rounds, f"{base_id}c")
        recs += _gen_backchecks(rng, cfg, [r for r in recs if r["form_type"] == "primary"], ri, base_id)

        summary = _round_summary(cfg, recs, ri, label, as_of, tw, cw)
        sd_pts = _sample_in_geom(rng, tgeom, sd_cfg.get("sample_points", 0))
        summary["overlay"] = {
            "ward_boundaries": _fc(
                [
                    {"type": "Feature", "geometry": tgeom, "properties": {"ward": tw, "role": "program"}},
                    {"type": "Feature", "geometry": cgeom, "properties": {"ward": cw, "role": "comparison"}},
                ]
            ),
            "service_delivery": _fc([_pt(lat, lon, {}) for lat, lon in sd_pts]),
            "survey_pins": _pins_sample(rng, recs, map_pin_cap),
        }
        summary["service_delivery_counts"] = {tw: sd_cfg.get("treatment", 0), cw: sd_cfg.get("comparison", 0)}
        rounds.append(summary)
        all_records += recs

    trend = {
        "rounds": [r["round"] for r in rounds],
        "intervention": [r["intervention_pct"] for r in rounds],
        "comparison": [r["comparison_pct"] for r in rounds],
        "self_report": [r["self_report_pct"] for r in rounds],
    }

    state = {
        "program": {
            "name": cfg["program"]["name"],
            "cadence": cfg["program"].get("cadence", "bi-monthly"),
            "rotating": True,
        },
        "current_round": n_rounds,
        "rounds": rounds,
        "trend": trend,
        "surveyor_backcheck": _surveyor_backcheck(cfg, all_records),
        "generated": {
            "seed": cfg["rng_seed"],
            "n_records": len(all_records),
            "n_rounds": n_rounds,
            "rotating_wards": True,
        },
    }
    return state, all_records


def summarize(state: dict) -> str:
    last = state["rounds"][-1]
    q = last["quality"]
    b = last["backcheck"]
    lines = [
        f"R{last['round']} ({last['label']}) · {last['treatment_ward']} vs {last['comparison_ward']}: "
        f"verified {last['intervention_pct']}% vs {last['comparison_pct']}% "
        f"(gap {last['gap_pp']}pp) · self-report {last['self_report_pct']}% (+{last['premium_pp']})",
        f"  QA: evidence {q['evidence_capture']['value']}% · GPS<=15m {q['gps_within_15m']['value']}% · "
        f"complete {q['field_completeness']['value']}% · duration-ok {q['duration_plausibility']['value']}% · "
        f"dupes {q['duplicate_integrity']['value']}",
        f"  Back-check: {b['n_backchecked']} checked ({b['coverage_pct']}%) · "
        f"Type-1 err {b['type1_error_pct']}% · outcome agree {b['outcome_agreement_pct']}% · "
        f"prtest p={b['prtest_p']} (orig {b['orig_pct']} vs bc {b['bc_pct']}) · {b['n_mismatch']} mismatched rows",
    ]
    return "\n".join(lines)
