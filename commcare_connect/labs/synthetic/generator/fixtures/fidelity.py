"""Score generated fixtures against the manifest they were generated from, so a
synthetic dataset can be trusted before an analytics system is tested on it.

``compare_to_source`` additionally scores a *clone* against its real *source* on
the dimensions that matter for a high-fidelity 'close mirror' (issue #713 #3):
marginal distance, structural ratios (visits/case, cases/FLW), per-entity
trajectory shape, and out-of-range leakage — so close-mirror is measurable, not
vibes."""

from __future__ import annotations

import numpy as np
from scipy.stats import wasserstein_distance

from .manifest import CategoricalDistribution, Manifest, NormalDistribution
from .mirror import profile_entity_structure
from .profiler import _profile_correlation  # reuse the same estimator


def _extract(visit, path):
    cur = visit.get("form_json") or {}
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def compare(manifest: Manifest, synthetic_visits: list[dict]) -> dict:
    cohort = manifest.beneficiary_cohorts[0]
    fields_report = {}
    for path, dist in cohort.field_distributions.items():
        present = [_extract(v, path) for v in synthetic_visits]
        nn = [p for p in present if p not in (None, "")]
        null_rate = 1.0 - (len(nn) / len(present)) if present else 0.0
        entry = {"target_null_rate": getattr(dist, "null_rate", 0.0), "synthetic_null_rate": round(null_rate, 4)}
        if isinstance(dist, NormalDistribution):
            nums = [float(x) for x in nn if _is_num(x)]
            if nums:
                entry["mean_delta"] = round(abs(float(np.mean(nums)) - dist.mean), 4)
                entry["std_delta"] = round(abs(float(np.std(nums)) - dist.stddev), 4)
        elif isinstance(dist, CategoricalDistribution):
            entry["tvd"] = round(_tvd(dist.values, [str(x) for x in nn]), 4)
        fields_report[path] = entry

    frob = None
    if cohort.correlation is not None:
        kinds = {
            p: ("select" if isinstance(cohort.field_distributions.get(p), CategoricalDistribution) else "decimal")
            for p in cohort.correlation.fields
        }
        recomputed = _profile_correlation(synthetic_visits, cohort.correlation.fields, kinds)
        if recomputed is not None and recomputed["fields"] == cohort.correlation.fields:
            a = np.array(cohort.correlation.matrix)
            b = np.array(recomputed["matrix"])
            frob = round(float(np.linalg.norm(a - b)), 4)
    return {"fields": fields_report, "correlation_frobenius": frob}


def _is_num(x):
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def _values_for(visits: list[dict], path: str) -> list[float]:
    out = []
    for v in visits:
        raw = _extract(v, path)
        if raw not in (None, "") and _is_num(raw):
            out.append(float(raw))
    return out


def _tvd_hist(a: dict, b: dict) -> float:
    """Total-variation distance between two integer-keyed count histograms."""
    pa, pb = _norm_hist(a), _norm_hist(b)
    keys = set(pa) | set(pb)
    return round(0.5 * sum(abs(pa.get(k, 0.0) - pb.get(k, 0.0)) for k in keys), 4)


def _norm_hist(h: dict) -> dict:
    total = sum(h.values()) or 1
    return {k: v / total for k, v in h.items()}


def _cases_per_flw_hist(owner_visit_counts: dict[str, list[int]]) -> dict[int, int]:
    """Distribution of how many cases each FLW owns (count -> number of FLWs)."""
    out: dict[int, int] = {}
    for counts in owner_visit_counts.values():
        n = len(counts)
        out[n] = out.get(n, 0) + 1
    return out


def _mean_per_entity_slope(transplant_pool: list[dict], path: str) -> float | None:
    """Mean least-squares slope of ``path`` vs day-offset across entities with >=2
    distinct-day observations of it — the trajectory's shape, summarized."""
    slopes = []
    for series in transplant_pool:
        pts = [(v["day"], v["values"][path]) for v in series["visits"] if path in v["values"]]
        days = sorted({d for d, _ in pts})
        if len(pts) < 2 or len(days) < 2:
            continue
        xs = np.array([d for d, _ in pts], dtype=float)
        ys = np.array([y for _, y in pts], dtype=float)
        slopes.append(float(np.polyfit(xs, ys, 1)[0]))
    return float(np.mean(slopes)) if slopes else None


def compare_to_source(source_visits: list[dict], clone_visits: list[dict], *, numeric_paths: set[str]) -> dict:
    """Score a clone against its real source. Returns per-field marginal +
    out-of-range metrics, structural-ratio distances, per-field trajectory slopes,
    and an overall 0–1 fidelity score (1 = indistinguishable on these axes)."""
    src = profile_entity_structure(source_visits, numeric_paths=numeric_paths)
    cln = profile_entity_structure(clone_visits, numeric_paths=numeric_paths)

    fields: dict[str, dict] = {}
    marginal_scores, range_scores, traj_scores = [], [], []
    trajectory: dict[str, dict] = {}
    for path in sorted(numeric_paths):
        s_vals, c_vals = _values_for(source_visits, path), _values_for(clone_visits, path)
        if not s_vals or not c_vals:
            continue
        lo, hi = min(s_vals), max(s_vals)
        span = (hi - lo) or 1.0
        wnorm = round(wasserstein_distance(s_vals, c_vals) / span, 4)
        oor = round(sum(1 for x in c_vals if x < lo or x > hi) / len(c_vals), 4)
        fields[path] = {"wasserstein_norm": wnorm, "out_of_range_rate": oor, "source_range": [lo, hi]}
        marginal_scores.append(max(0.0, 1.0 - wnorm))
        range_scores.append(1.0 - oor)

        s_slope = _mean_per_entity_slope(src.transplant_pool, path)
        c_slope = _mean_per_entity_slope(cln.transplant_pool, path)
        if s_slope is not None and c_slope is not None:
            delta = abs(s_slope - c_slope)
            trajectory[path] = {
                "source_slope": round(s_slope, 4),
                "clone_slope": round(c_slope, 4),
                "slope_delta": round(delta, 4),
            }
            traj_scores.append(max(0.0, 1.0 - delta / (abs(s_slope) + 1e-9)))

    vpe_tvd = _tvd_hist(src.visits_per_entity, cln.visits_per_entity)
    cpf_tvd = _tvd_hist(_cases_per_flw_hist(src.owner_visit_counts), _cases_per_flw_hist(cln.owner_visit_counts))
    ratio_score = 1.0 - (vpe_tvd + cpf_tvd) / 2.0

    components = []
    for group in (marginal_scores, range_scores, traj_scores):
        if group:
            components.append(float(np.mean(group)))
    components.append(ratio_score)
    score = round(max(0.0, min(1.0, float(np.mean(components)))), 4)

    return {
        "score": score,
        "fields": fields,
        "trajectory": trajectory,
        "visits_per_case_tvd": vpe_tvd,
        "cases_per_flw_tvd": cpf_tvd,
    }


def _tvd(target: dict, observed: list[str]) -> float:
    total = sum(target.values())
    tgt = {k: v / total for k, v in target.items()}
    obs_total = len(observed) or 1
    obs = {}
    for x in observed:
        obs[x] = obs.get(x, 0) + 1 / obs_total
    keys = set(tgt) | set(obs)
    return 0.5 * sum(abs(tgt.get(k, 0.0) - obs.get(k, 0.0)) for k in keys)
