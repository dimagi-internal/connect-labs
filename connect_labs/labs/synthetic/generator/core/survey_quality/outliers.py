"""Layer 3 — enumerator outlier / fabrication screening algorithms.

The consensus design from the falsification-detection literature (IPA HFCs,
World Bank DIME, Kuriakose & Robbins, Schäfer et al.): compute many *relative*
signals per enumerator, flag those that deviate from the pool, then roll them
into a composite suspicion score — and confirm with back-checks. Thresholds are
deliberately project-tunable via ``cfg["outlier"]``.

This module is the extensible seam an internal outlier tool plugs into: keep the
record shape and the ``per_enumerator`` result contract, and register new
algorithms with ``@register_metric(..., layer="outlier")``. The composite
scorecard automatically picks up any per-enumerator metric listed in
``cfg["outlier"]["scorecard"]`` (defaults to the three below).

Importing this module registers the metrics.
"""

from __future__ import annotations

from collections import Counter

from .registry import register_metric
from .stats import haversine_m, mad_modified_z, mean, median


def _by_enum(recs):
    groups = {}
    for r in recs:
        if r.get("form_type") == "primary":
            groups.setdefault(r.get("enumerator_id"), []).append(r)
    return groups


def _z_threshold(cfg):
    return (cfg.get("outlier") or {}).get("z_threshold", 3.5)


@register_metric(
    "enum_yes_rate_outlier",
    "Outcome-rate outliers (vs peers)",
    "outlier",
    unit="count",
    threshold=0,
    direction="lower_better",
)
def enum_yes_rate_outlier(recs, cfg):
    """Per-enumerator positive-outcome rate, flagged when it sits >z from the
    pool (robust MAD z). Catches enumerators inventing suspiciously high/uniform
    'yes' rates."""
    groups = _by_enum(recs)
    enums = list(groups)
    rates = {e: (mean([1.0 if x.get("vitamin_a_received") else 0.0 for x in rs]) or 0.0) for e, rs in groups.items()}
    zs = mad_modified_z([rates[e] for e in enums])
    thr = _z_threshold(cfg)
    per = {
        e: {
            "yes_rate": round(rates[e] * 100, 1),
            "z": (round(z, 2) if z is not None else None),
            "flag": bool(z is not None and abs(z) > thr),
        }
        for e, z in zip(enums, zs)
    }
    return {
        "value": sum(1 for v in per.values() if v["flag"]),
        "n": len(enums),
        "detail": {"per_enumerator": per, "threshold_z": thr},
    }


@register_metric(
    "enum_speed_outlier",
    "Speeding outliers (short interviews)",
    "outlier",
    unit="count",
    threshold=0,
    direction="lower_better",
)
def enum_speed_outlier(recs, cfg):
    """Per-enumerator median interview duration, flagged when far below the pool
    (rushed = a leading curbstoning signal)."""
    groups = _by_enum(recs)
    med = {
        e: median([x.get("duration_min") for x in rs if x.get("duration_min") is not None]) for e, rs in groups.items()
    }
    enums = [e for e in med if med[e] is not None]
    zs = mad_modified_z([med[e] for e in enums])
    thr = _z_threshold(cfg)
    per = {
        e: {
            "median_min": round(med[e], 1),
            "z": (round(z, 2) if z is not None else None),
            "flag": bool(z is not None and z < -thr),
        }
        for e, z in zip(enums, zs)
    }
    return {
        "value": sum(1 for v in per.values() if v["flag"]),
        "n": len(enums),
        "detail": {"per_enumerator": per, "threshold_z": thr},
    }


@register_metric(
    "enum_gps_cluster",
    "GPS clustering (sit-and-fabricate)",
    "outlier",
    unit="count",
    threshold=0,
    direction="lower_better",
)
def enum_gps_cluster(recs, cfg):
    """Per-enumerator count of records implausibly co-located with another of
    their records (within ``gps_cluster_m``) — distinct households should not
    share a point."""
    groups = _by_enum(recs)
    radius = (cfg.get("outlier") or {}).get("gps_cluster_m", 8.0)
    per = {}
    for e, rs in groups.items():
        pts = [(r.get("lat"), r.get("lon")) for r in rs if r.get("lat") is not None and r.get("lon") is not None]
        clustered = 0
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                if haversine_m(pts[i][0], pts[i][1], pts[j][0], pts[j][1]) <= radius:
                    clustered += 1
                    break
        per[e] = {"clustered": clustered, "flag": clustered > 0}
    return {
        "value": sum(1 for v in per.values() if v["flag"]),
        "n": len(groups),
        "detail": {"per_enumerator": per, "radius_m": radius},
    }


@register_metric(
    "enum_answer_uniformity",
    "Answer-distribution uniformity (vs peers)",
    "outlier",
    unit="count",
    threshold=0,
    direction="lower_better",
)
def enum_answer_uniformity(recs, cfg):
    """Per-enumerator concentration of a categorical answer (Herfindahl index =
    Σ share²), flagged when it sits far ABOVE the pool (robust MAD z). Real
    neighbourhoods vary house to house, so an enumerator whose answers collapse
    onto one value is over-uniform — the "the right mix is hard to fake" signal.

    Unlike GPS co-location this is robust on plan-grounded data (where every
    survey lands on a distinct real footprint): it screens the *answers*, not the
    coordinates. The field is configurable via ``cfg['outlier']['uniformity_field']``
    (default ``roof_type``)."""
    conf = cfg.get("outlier") or {}
    fieldname = conf.get("uniformity_field", "roof_type")
    groups = _by_enum(recs)

    def _hhi(rs):
        c = Counter(r.get(fieldname) for r in rs if r.get(fieldname) is not None)
        tot = sum(c.values())
        return (sum((v / tot) ** 2 for v in c.values()) if tot else None), tot

    hhi = {e: _hhi(rs) for e, rs in groups.items()}
    enums = [e for e in groups if hhi[e][0] is not None]
    zs = mad_modified_z([hhi[e][0] for e in enums])
    thr = _z_threshold(cfg)
    per = {
        e: {
            "hhi": round(hhi[e][0], 3),
            "n": hhi[e][1],
            "z": (round(z, 2) if z is not None else None),
            # higher concentration than peers is the suspicious direction
            "flag": bool(z is not None and z > thr),
        }
        for e, z in zip(enums, zs)
    }
    return {
        "value": sum(1 for v in per.values() if v["flag"]),
        "n": len(enums),
        "detail": {"per_enumerator": per, "threshold_z": thr, "field": fieldname},
    }


@register_metric(
    "enum_scorecard",
    "Enumerator quality scorecard",
    "outlier",
    unit="count",
    threshold=0,
    direction="lower_better",
)
def enum_scorecard(recs, cfg):
    """Composite: weighted sum of the per-enumerator flags above into a
    red/amber/green band. The two 'hard' signals (speeding, answer over-uniformity)
    are weighted highest — two of them together push a surveyor to red; a single
    signal reads amber. ``value`` = number of non-green enumerators.

    GPS co-location (``enum_gps_cluster``) is still registered but is left OUT of
    the default composite: on plan-grounded data every survey lands on a distinct
    real footprint, so distinct households can never share a point and the signal
    is structurally always-zero. Projects with un-grounded GPS can add it back via
    ``cfg['outlier']['scorecard']``."""
    conf = cfg.get("outlier") or {}
    components = conf.get(
        "scorecard",
        {"enum_speed_outlier": 2, "enum_answer_uniformity": 2, "enum_yes_rate_outlier": 1},
    )
    parts = {name: globals()[name](recs, cfg) for name in components}
    enums = set()
    for pr in parts.values():
        enums |= set(pr["detail"]["per_enumerator"].keys())
    score = {}
    for e in enums:
        s = 0
        flags = []
        for name, weight in components.items():
            if parts[name]["detail"]["per_enumerator"].get(e, {}).get("flag"):
                s += weight
                flags.append(name)
        band = "red" if s >= 3 else ("amber" if s >= 1 else "green")
        score[e] = {"score": s, "flags": flags, "band": band}
    flagged = sum(1 for v in score.values() if v["band"] != "green")
    return {"value": flagged, "n": len(enums), "detail": {"per_enumerator": score, "weights": components}}
