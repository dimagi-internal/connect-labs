"""Layer 1 (survey data-quality) + Layer 2 (back-check) algorithms.

Indicators chosen from the established field-survey literature — DHS/MICS data
quality, World Bank LSMS/DIME high-frequency checks, J-PAL & IPA back-check
methodology (``bcstats``). Each operates on one round's records: a list of
canonical record dicts where ``form_type`` is ``"primary"`` or ``"back_check"``
(back-checks carry ``original_record_id`` linking to the primary they re-survey).

Importing this module registers the metrics. See ``registry.py`` for the contract.
"""

from __future__ import annotations

from collections import Counter

from .registry import register_metric
from .stats import haversine_m, iqr_bounds, two_proportion_z

# ---------------------------------------------------------------- helpers


def _primary(recs):
    return [r for r in recs if r.get("form_type") == "primary"]


def _backchecks(recs):
    return [r for r in recs if r.get("form_type") == "back_check"]


def _pct(num, den):
    return None if not den else round(100.0 * num / den, 1)


def _pairs(recs):
    """(original_primary, back_check) pairs joined on original_record_id."""
    prim = {r.get("record_id"): r for r in _primary(recs)}
    out = []
    for b in _backchecks(recs):
        o = prim.get(b.get("original_record_id"))
        if o:
            out.append((o, b))
    return out


# ===================================================== Layer 1: survey quality

REQUIRED_FIELDS = ["lat", "lon", "start_ts", "enumerator_id", "vitamin_a_received"]


@register_metric(
    "evidence_capture",
    "Evidence capture on positive outcome",
    "survey_quality",
    threshold=95.0,
)
def evidence_capture(recs, cfg):
    """Share of 'received vitamin-A = yes' records that carry the proof photo.
    The central trust claim for an evidence-backed coverage figure."""
    pos = [r for r in _primary(recs) if r.get("vitamin_a_received")]
    ok = sum(1 for r in pos if r.get("evidence_photo"))
    return {"value": _pct(ok, len(pos)), "n": len(pos), "detail": {"with_photo": ok}}


@register_metric("gps_within_15m", "GPS within 15 m of household", "survey_quality", threshold=95.0)
def gps_within_15m(recs, cfg):
    """Captured location within 15 m of the household's assigned location —
    proves the enumerator was physically present."""
    p = _primary(recs)
    ok = sum(1 for r in p if r.get("gps_offset_m") is not None and r["gps_offset_m"] <= 15)
    return {"value": _pct(ok, len(p)), "n": len(p)}


@register_metric("gps_in_ward", "GPS inside assigned ward", "survey_quality", threshold=95.0)
def gps_in_ward(recs, cfg):
    p = _primary(recs)
    ok = sum(1 for r in p if r.get("in_ward"))
    return {"value": _pct(ok, len(p)), "n": len(p)}


@register_metric("field_completeness", "Required-field completeness", "survey_quality", threshold=98.0)
def field_completeness(recs, cfg):
    """Records with every required field present, plus per-field missingness
    (DHS surfaces any field whose 'not stated' share reaches ~1%)."""
    p = _primary(recs)
    ok = sum(1 for r in p if all(r.get(k) is not None for k in REQUIRED_FIELDS))
    miss = {k: _pct(sum(1 for r in p if r.get(k) is None), len(p)) for k in REQUIRED_FIELDS}
    return {"value": _pct(ok, len(p)), "n": len(p), "detail": {"missing_by_field": miss}}


@register_metric("duration_plausibility", "Interview-duration plausibility", "survey_quality", threshold=95.0)
def duration_plausibility(recs, cfg):
    """Share of interviews inside a plausible length band: above a hard floor and
    within Tukey fences of the round's own distribution. Catches speeding."""
    p = _primary(recs)
    durs = [r.get("duration_min") for r in p if r.get("duration_min") is not None]
    floor = ((cfg.get("quality") or {}).get("duration_min") or {}).get("floor", 4)
    lo, hi = iqr_bounds(durs) if len(durs) >= 4 else (None, None)

    def ok(d):
        if d is None or d < floor:
            return False
        if lo is not None and (d < lo or d > hi):
            return False
        return True

    good = sum(1 for r in p if ok(r.get("duration_min")))
    return {"value": _pct(good, len(p)), "n": len(p), "detail": {"iqr_lo": lo, "iqr_hi": hi, "floor": floor}}


@register_metric("consistency_pass", "Internal-consistency edit checks", "survey_quality", threshold=99.0)
def consistency_pass(recs, cfg):
    """Logical edit rules that must hold on every record. Catches both bugs and
    fabrication (e.g. a 'received' with no child present)."""
    p = _primary(recs)

    def ok(r):
        if r.get("vitamin_a_received") and not r.get("child_present"):
            return False
        if r.get("vitamin_a_received") and not r.get("eligible"):
            return False
        if r.get("child_present") and r.get("child_age_months") is None:
            return False
        return True

    good = sum(1 for r in p if ok(r))
    return {"value": _pct(good, len(p)), "n": len(p)}


@register_metric(
    "duplicate_integrity",
    "Duplicate records",
    "survey_quality",
    unit="count",
    threshold=0,
    direction="lower_better",
)
def duplicate_integrity(recs, cfg):
    """Duplicated household ids, and duplicated (GPS, timestamp) signatures.
    Target zero."""
    p = _primary(recs)
    hh = Counter(r.get("household_id") for r in p)
    dup_hh = sum(c - 1 for c in hh.values() if c > 1)
    sig = Counter((round(r.get("lat") or 0, 5), round(r.get("lon") or 0, 5), r.get("start_ts")) for r in p)
    dup_sig = sum(c - 1 for c in sig.values() if c > 1)
    return {
        "value": dup_hh + dup_sig,
        "n": len(p),
        "detail": {"dup_household_id": dup_hh, "dup_gps_time": dup_sig},
    }


# ========================================================= Layer 2: back-check
#
# J-PAL / IPA back-check methodology. Variables are classified:
#   Type 1 — stable identifiers; should never change; a mismatch implies the
#            visit didn't happen as recorded (fraud signal). No tolerance.
#   Type 2 — administration/judgement items (training signal).
#   Type 3 — the key outcome; may legitimately drift; tested statistically.

# (field, tolerance) — tolerance 0 means exact match required.
TYPE1_FIELDS = [("child_sex", 0), ("child_present", 0), ("child_age_months", 2)]


@register_metric("backcheck_coverage", "Back-check coverage", "backcheck", threshold=10.0)
def backcheck_coverage(recs, cfg):
    """Share of primary surveys that were independently re-surveyed, overall and
    per enumerator (J-PAL: >=10%, every enumerator covered)."""
    p = _primary(recs)
    pairs = _pairs(recs)
    pc = Counter(r.get("enumerator_id") for r in p)
    bc = Counter(o.get("enumerator_id") for o, _ in pairs)
    per = {e: _pct(bc.get(e, 0), pc[e]) for e in pc}
    return {
        "value": _pct(len(pairs), len(p)),
        "n": len(pairs),
        "detail": {"per_enumerator": per, "n_primary": len(p)},
    }


@register_metric(
    "backcheck_type1_error",
    "Type-1 discordance (verification)",
    "backcheck",
    threshold=10.0,
    direction="lower_better",
)
def backcheck_type1_error(recs, cfg):
    """Per-pair: does any Type-1 field disagree between survey and re-survey?
    Overall + per-enumerator error rate. >10% is the J-PAL red flag."""
    pairs = _pairs(recs)
    if not pairs:
        return {"value": None, "n": 0}
    bad = 0
    per = {}  # enumerator -> [errors, total]
    for o, b in pairs:
        mismatch = False
        for f, tol in TYPE1_FIELDS:
            ov, bv = o.get(f), b.get(f)
            if tol and isinstance(ov, (int, float)) and isinstance(bv, (int, float)):
                if abs(ov - bv) > tol:
                    mismatch = True
            elif ov != bv:
                mismatch = True
        e = o.get("enumerator_id")
        per.setdefault(e, [0, 0])
        per[e][1] += 1
        if mismatch:
            bad += 1
            per[e][0] += 1
    per_rate = {e: round(100.0 * x[0] / x[1], 1) for e, x in per.items()}
    return {
        "value": round(100.0 * bad / len(pairs), 1),
        "n": len(pairs),
        "detail": {"per_enumerator_error": per_rate, "n_mismatch": bad},
    }


@register_metric("backcheck_outcome_agreement", "Back-check outcome agreement", "backcheck", threshold=95.0)
def backcheck_outcome_agreement(recs, cfg):
    """Share of back-checked households where the vitamin-A outcome matches the
    original. The headline 'back-check pass' number."""
    pairs = _pairs(recs)
    if not pairs:
        return {"value": None, "n": 0}
    agree = sum(1 for o, b in pairs if bool(o.get("vitamin_a_received")) == bool(b.get("vitamin_a_received")))
    return {"value": round(100.0 * agree / len(pairs), 1), "n": len(pairs), "detail": {"n_agree": agree}}


@register_metric(
    "backcheck_outcome_prtest",
    "Outcome reproducibility (proportion test)",
    "backcheck",
    unit="pvalue",
    threshold=0.05,
    direction="higher_better",
)
def backcheck_outcome_prtest(recs, cfg):
    """Two-proportion test (``prtest``) of original vs re-survey coverage among
    back-checked households. p > 0.05 => the coverage number reproduces."""
    pairs = _pairs(recs)
    if len(pairs) < 2:
        return {"value": None, "n": len(pairs)}
    n = len(pairs)
    s1 = sum(1 for o, _ in pairs if o.get("vitamin_a_received"))
    s2 = sum(1 for _, b in pairs if b.get("vitamin_a_received"))
    z, pval = two_proportion_z(s1, n, s2, n)
    return {
        "value": (round(pval, 3) if pval is not None else None),
        "n": n,
        "passed": (pval is None or pval > 0.05),
        "detail": {
            "orig_pct": _pct(s1, n),
            "backcheck_pct": _pct(s2, n),
            "z": (round(z, 2) if z is not None else None),
        },
    }


# Fields compared in the back-check (the re-asked variables), tagged by type.
# Quality fields like the evidence photo are NOT back-check variables — they're
# Layer-1 survey-quality, scored separately — so they don't drive discordance.
COMPARE_FIELDS = [
    ("vitamin_a_received", "Vitamin-A received", "outcome"),
    ("child_present", "Child present", "type1"),
    ("child_sex", "Child sex", "type1"),
    ("child_age_months", "Child age (mo)", "type1"),
]


@register_metric("backcheck_comparison", "Back-check comparison rows", "backcheck", unit="count", direction="none")
def backcheck_comparison(recs, cfg):
    """The auditable drill-down: one row per back-checked household with each
    field's original vs re-survey value and a per-field match flag. Mismatched
    rows sort first. ``value`` = number of rows with any mismatch."""
    rows = []
    mismatch_total = 0
    for o, b in _pairs(recs):
        fields = []
        row_mismatch = False
        for key, label, vtype in COMPARE_FIELDS:
            ov, bv = o.get(key), b.get(key)
            if key == "child_age_months":
                m = ov is not None and bv is not None and abs(ov - bv) > 2
            else:
                m = ov != bv
            row_mismatch = row_mismatch or m
            fields.append({"key": key, "label": label, "type": vtype, "original": ov, "backcheck": bv, "match": not m})
        if row_mismatch:
            mismatch_total += 1
        gps_delta = None
        if all(o.get(k) is not None for k in ("lat", "lon")) and all(b.get(k) is not None for k in ("lat", "lon")):
            gps_delta = round(haversine_m(o["lat"], o["lon"], b["lat"], b["lon"]), 1)
        rows.append(
            {
                "household_id": o.get("household_id"),
                "ward": o.get("ward"),
                "enumerator": o.get("enumerator_id"),
                "backcheck_enumerator": b.get("enumerator_id"),
                "gps_delta_m": gps_delta,
                "flagged": row_mismatch,
                "fields": fields,
            }
        )
    rows.sort(key=lambda r: (not r["flagged"], str(r["household_id"])))
    return {"value": mismatch_total, "n": len(rows), "detail": {"rows": rows}}
