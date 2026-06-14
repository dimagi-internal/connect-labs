"""Parameterized synthetic survey generator for the Verified Monitoring demo.

Robust + reusable: drive it from a per-program JSON config (see
``demo_config.json``) and it emits, **for each round** (each round modelled as its
own opportunity), row-level household survey records — a ``primary`` form and a
``back_check`` form per the design — then computes every dashboard KPI from those
records via the shared ``commcare_connect.labs.synthetic.generator.core.survey_quality`` algorithm library.
Nothing is hand-entered: coverage, the QA strip, the back-check drill-down, and
the map pins all roll up from the generated rows, so "let the numbers be
computed" is honest and internally consistent. Point it at a different geojson +
config and you get the same dashboard for any program.

The output is the workflow ``instance.state`` payload the render reads.
"""

from __future__ import annotations

import random

# Make the repo root importable so the shared algorithm library resolves whether
# this runs from a checkout or a management context.
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from commcare_connect.labs.synthetic.generator.core.survey_quality import results_to_map, run_metrics  # noqa: E402
from commcare_connect.labs.synthetic.generator.core.survey_sim import (  # noqa: E402
    SimParams,
    scatter_primaries,
    simulate_backchecks,
    simulate_plan,
)
from commcare_connect.labs.synthetic.generator.core.survey_sim.geo import interp, sample_in_geom  # noqa: E402

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------- geometry utils


def _load_wards(path: Path) -> dict:
    import json

    fc = json.loads(Path(path).read_text())
    return {f["properties"]["name"]: f for f in fc["features"]}


def _round_label(round0: str, i: int) -> tuple:
    y, m = (int(x) for x in round0.split("-"))
    total = (y * 12 + (m - 1)) + 2 * i  # bi-monthly
    yy, mm = divmod(total, 12)
    return f"{_MONTHS[mm]} {yy}", f"{yy:04d}-{mm + 1:02d}-15"


# --------------------------------------------------------------- record gen


def _sim_params(cfg, arm_key, arm_cfg, round_idx, n_rounds):
    """Translate the demo config into a generic ``SimParams`` for one arm-round.

    The flagged surveyor's degraded quality + lower primary rate apply only on
    their own arm; the comparison arm draws everyone from the program mean."""
    q = cfg["quality"]
    n_enum = arm_cfg.get("enumerators", 5)
    enum_ids = [f"{arm_key[0].upper()}{k + 1}" for k in range(n_enum)]
    flagged = cfg.get("flagged_surveyor") or {}
    on_arm = flagged.get("arm") == arm_key
    pr = dict(cfg.get("primary_rate") or {})
    if not on_arm:  # the flagged surveyor's substitution story is on their arm only
        pr.pop("flagged_id", None)
        pr.pop("flagged_mean", None)
    return SimParams.from_dict(
        {
            "enumerators": enum_ids,
            "coverage_start": arm_cfg["coverage_start"],
            "coverage_end": arm_cfg["coverage_end"],
            "coverage_noise": arm_cfg.get("coverage_noise", 0.0),
            "round_idx": round_idx,
            "n_rounds": n_rounds,
            "arm": arm_key,
            "primary_rate": pr,
            "gps_within_15m": q["gps_within_15m"],
            "gps_near_m": q.get("gps_offset_near_m", [1, 13]),
            "gps_far_m": q.get("gps_offset_far_m", [16, 55]),
            "evidence_complete": q["evidence_complete"],
            "field_complete": q.get("field_complete", 1.0),
            "duration": q["duration_min"],
            "eligibility": cfg.get("eligibility", {}),
            "roof_types": cfg.get("roof_types") or ["thatch", "metal sheet", "mud", "tile"],
            "roof_weights": cfg.get("roof_weights") or [0.42, 0.34, 0.16, 0.08],
            "surveyor_heterogeneity": cfg.get("surveyor_heterogeneity", 0.0),
            "flagged": (
                {
                    "id": flagged.get("id"),
                    "evidence": flagged.get("evidence"),
                    "gps_within_15m": flagged.get("gps_within_15m"),
                    # Layer-3 fabrication signature: short interviews + answers
                    # collapsed onto the modal value (caught by the distributions screen).
                    "duration_mean": flagged.get("duration_mean"),
                    "duration_sd": flagged.get("duration_sd"),
                    "roof_concentration": flagged.get("roof_concentration"),
                }
                if on_arm
                else None
            ),
        }
    )


def _arm_records(rng, cfg, arm_key, arm_cfg, geom, round_idx, n_rounds, base_id, work_areas=None):
    """One arm's primary records for one round.

    When the round's plan ``work_areas`` are supplied (the live, grounded path),
    delegate to the generic ``simulate_plan`` so the captured GPS sits on the real
    sampled primary/alternate footprints. Without them (offline / no live plan),
    fall back to the legacy random-in-ward generator."""
    params = _sim_params(cfg, arm_key, arm_cfg, round_idx, n_rounds)
    if work_areas:
        return simulate_plan(work_areas, params, rng, ward_name=arm_cfg["ward"], ward_geom=geom, base_id=base_id)
    return scatter_primaries(rng, cfg, arm_key, arm_cfg, geom, round_idx, n_rounds, base_id, params=params)


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
                # Share of this surveyor's surveys on the PRIMARY (first-choice) unit
                # vs a substituted ALTERNATE; null when the round isn't plan-grounded.
                "primary_rate": _v(qm, "primary_rate"),
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


def _surveyor_distributions(cfg, records):
    """Layer-3 statistical fabrication screen — one row per program-ward surveyor.

    Robust (median/MAD) z-scores vs peers on three signals that need no second
    field visit: dose yes-rate, interview speed, and answer-distribution
    uniformity, plus a composite red/amber/green band. All computed from the
    surveyors' own records via the shared ``outlier`` layer. (GPS co-location is
    intentionally omitted — on plan-grounded data every survey lands on a distinct
    real footprint, so that signal is structurally always-zero here.)"""
    tw = [r for r in records if r.get("arm") == "treatment" and r["form_type"] == "primary"]
    om = results_to_map(run_metrics(tw, layers=["outlier"], config=cfg))

    def _per(key):
        return ((om.get(key) or {}).get("detail") or {}).get("per_enumerator", {})

    yr, sp, un, sc = (
        _per("enum_yes_rate_outlier"),
        _per("enum_speed_outlier"),
        _per("enum_answer_uniformity"),
        _per("enum_scorecard"),
    )
    surveyors = sorted(set(yr) | set(sp) | set(un) | set(sc))
    rows = []
    for s in surveyors:
        rows.append(
            {
                "surveyor": s,
                "yes_rate": (yr.get(s) or {}).get("yes_rate"),
                "yes_z": (yr.get(s) or {}).get("z"),
                "speed_med": (sp.get(s) or {}).get("median_min"),
                "speed_z": (sp.get(s) or {}).get("z"),
                "uniformity_hhi": (un.get(s) or {}).get("hhi"),
                "uniformity_z": (un.get(s) or {}).get("z"),
                "band": (sc.get(s) or {}).get("band"),
                "flags": (sc.get(s) or {}).get("flags", []),
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
    infl = interp(cfg["self_report"]["inflation_start"], cfg["self_report"]["inflation_end"], round_idx, cfg["rounds"])
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
        "surveyor_distributions": _surveyor_distributions(cfg, records),
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
            feats.append(
                _pt(
                    r["lat"],
                    r["lon"],
                    {
                        "confirmed": bool(r["vitamin_a_received"]),
                        "ward": r["ward"],
                        # primary (first-choice) vs alternate (substituted backup) —
                        # the map styles the two so the substitution mix is visible.
                        "sample_type": r.get("sample_type"),
                    },
                )
            )
    return _fc(feats)


def build_state(cfg: dict, here: Path, rounds_plans: dict | None = None) -> tuple:
    """Generate all rounds and assemble the workflow instance.state payload.

    Rotating wards: each round surveys a DIFFERENT (program, comparison) ward
    pair (``cfg["rounds_wards"]``), so the map moves cycle to cycle. Every round
    carries its own ward names + map overlay; the render reads them per round.

    ``rounds_plans`` (optional) maps a 0-based round index to that round's live
    sampled plans, ``{ri: {"treatment": {"work_areas": [...]}, "comparison":
    {...}}}``. When present, survey GPS is grounded on the real primary/alternate
    footprints (the generic ``simulate_plan``); when absent, the round falls back
    to the legacy random-in-ward generator. Mixed is fine — ground the rounds
    that have a live study, scatter the rest.

    Returns (state, all_records)."""
    rounds_plans = rounds_plans or {}
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
        rp = rounds_plans.get(ri) or {}
        tw_was = (rp.get("treatment") or {}).get("work_areas")
        cw_was = (rp.get("comparison") or {}).get("work_areas")
        recs = _arm_records(
            rng, cfg, "treatment", arm_cfg["treatment"], tgeom, ri, n_rounds, f"{base_id}t", work_areas=tw_was
        )
        recs += _arm_records(
            rng, cfg, "comparison", arm_cfg["comparison"], cgeom, ri, n_rounds, f"{base_id}c", work_areas=cw_was
        )
        recs += simulate_backchecks(rng, cfg, [r for r in recs if r["form_type"] == "primary"], ri, base_id)

        summary = _round_summary(cfg, recs, ri, label, as_of, tw, cw)
        sd_pts = sample_in_geom(rng, tgeom, sd_cfg.get("sample_points", 0))
        summary["overlay"] = {
            "ward_boundaries": _fc(
                [
                    {"type": "Feature", "geometry": tgeom, "properties": {"ward": tw, "role": "program"}},
                    {"type": "Feature", "geometry": cgeom, "properties": {"ward": cw, "role": "comparison"}},
                ]
            ),
            "service_delivery": _fc([_pt(lat, lon, {}) for lat, lon in sd_pts]),
            "survey_pins": _pins_sample(rng, recs, map_pin_cap),
            # The DESIGNED plan's selected-PSU cluster hulls (arm-tagged), so the
            # render can draw the plan via the shared PlanLayers — same as the editor.
            # Baked into state (never fetched); empty when the round isn't plan-grounded.
            "plan_hulls": (rp.get("psu_hulls") or {"type": "FeatureCollection", "features": []}),
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
