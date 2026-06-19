"""Score generated fixtures against the manifest they were generated from, so a
synthetic dataset can be trusted before an analytics system is tested on it."""

from __future__ import annotations

import numpy as np

from .manifest import CategoricalDistribution, Manifest, NormalDistribution
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


def _tvd(target: dict, observed: list[str]) -> float:
    total = sum(target.values())
    tgt = {k: v / total for k, v in target.items()}
    obs_total = len(observed) or 1
    obs = {}
    for x in observed:
        obs[x] = obs.get(x, 0) + 1 / obs_total
    keys = set(tgt) | set(obs)
    return 0.5 * sum(abs(tgt.get(k, 0.0) - obs.get(k, 0.0)) for k in keys)
