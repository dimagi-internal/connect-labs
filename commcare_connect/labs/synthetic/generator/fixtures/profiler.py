"""Derive a Manifest from real production export data.

Reads the five export endpoints for an opportunity, computes statistical
profiles (distributions, rates, counts), and outputs a Manifest YAML
that reproduces the same statistical shape when fed to the generator
engine. No PII appears in the output — only aggregate statistics.

The profiler runs entirely server-side. Real data is fetched into memory,
reduced to numbers, and discarded.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict
from typing import Any

import yaml

from .manifest import Manifest, ManifestValidationError


def _mean_std(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return (values[0] if values else 0.0, 0.0)
    return (statistics.mean(values), statistics.stdev(values))


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator > 0 else 0.0


def _extract_nested(obj: dict, dotted_path: str) -> Any:
    parts = dotted_path.split(".")
    cur = obj
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _classify_archetype(approval_rate: float, flag_rate: float) -> str:
    if approval_rate >= 0.90 and flag_rate <= 0.05:
        return "rockstar"
    if approval_rate >= 0.80:
        return "steady"
    if approval_rate >= 0.65:
        return "struggling"
    return "new_hire"


def _profile_flw_personas(
    visits_by_flw: dict[str, list[dict]],
) -> list[dict[str, Any]]:
    personas = []
    for i, (username, visits) in enumerate(sorted(visits_by_flw.items(), key=lambda kv: -len(kv[1]))):
        total = len(visits)
        approved = sum(1 for v in visits if v.get("status") == "approved")
        flagged = sum(1 for v in visits if v.get("flagged"))

        approval_rate = _safe_rate(approved, total)
        flag_rate = _safe_rate(flagged, total)
        archetype = _classify_archetype(approval_rate, flag_rate)

        acc_mean = approval_rate
        acc_std = min(0.08, acc_mean * 0.1)
        comp_mean = min(1.0, acc_mean + 0.05)
        comp_std = min(0.08, comp_mean * 0.1)

        personas.append(
            {
                "id": f"flw_{i + 1:03d}",
                "display_name": f"Worker {i + 1}",
                "archetype": archetype,
                "accuracy_distribution": {"mean": round(acc_mean, 3), "stddev": round(acc_std, 3)},
                "completeness_distribution": {"mean": round(comp_mean, 3), "stddev": round(comp_std, 3)},
                "flag_rate": round(flag_rate, 3),
            }
        )
    return personas


def _profile_timeline(
    all_visits: list[dict],
    visits_by_flw: dict[str, list[dict]],
) -> dict[str, Any]:
    dates = []
    for v in all_visits:
        vd = v.get("visit_date")
        if vd:
            try:
                dates.append(dt.date.fromisoformat(vd[:10]))
            except (ValueError, TypeError):
                pass

    if not dates:
        today = dt.date.today()
        return {
            "start_date": (today - dt.timedelta(days=28)).isoformat(),
            "end_date": today.isoformat(),
            "weeks": 4,
            "visit_cadence_per_week_per_flw": {"mean": 8, "stddev": 2},
        }

    start = min(dates)
    end = max(dates)
    span_days = max((end - start).days, 7)
    weeks = max(1, round(span_days / 7))

    per_flw_weekly: list[float] = []
    for username, visits in visits_by_flw.items():
        per_flw_weekly.append(len(visits) / weeks)

    cadence_mean, cadence_std = _mean_std(per_flw_weekly)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "weeks": weeks,
        "visit_cadence_per_week_per_flw": {
            "mean": round(cadence_mean, 1),
            "stddev": round(max(cadence_std, 0.5), 1),
        },
    }


def _profile_field_distributions(
    all_visits: list[dict],
    form_json_paths: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if not form_json_paths:
        form_json_paths = _discover_numeric_paths(all_visits)

    distributions: dict[str, dict[str, Any]] = {}
    for path in form_json_paths:
        values = []
        for v in all_visits:
            fj = v.get("form_json") or {}
            raw = _extract_nested(fj, path)
            if raw is None:
                continue
            try:
                values.append(float(raw))
            except (ValueError, TypeError):
                pass

        if len(values) < 5:
            continue

        mean, std = _mean_std(values)
        if std < 0.001:
            continue

        distributions[path] = {
            "distribution": "normal",
            "mean": round(mean, 3),
            "stddev": round(std, 3),
        }

    return distributions


def _discover_numeric_paths(
    visits: list[dict],
    sample_size: int = 200,
) -> list[str]:
    sample = visits[:sample_size]
    path_counts: Counter[str] = Counter()
    path_numeric: Counter[str] = Counter()

    for v in sample:
        fj = v.get("form_json") or {}
        _walk_paths(fj, "", path_counts, path_numeric)

    paths = []
    for path, count in path_counts.items():
        if count < len(sample) * 0.3:
            continue
        if path_numeric[path] > count * 0.5:
            paths.append(path)
    return sorted(paths)


def _walk_paths(
    obj: dict,
    prefix: str,
    counts: Counter,
    numeric: Counter,
    depth: int = 0,
) -> None:
    if depth > 6:
        return
    for key, val in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            _walk_paths(val, path, counts, numeric, depth + 1)
        else:
            counts[path] += 1
            if val is not None:
                try:
                    float(val)
                    numeric[path] += 1
                except (ValueError, TypeError):
                    pass


def _profile_kpis(
    field_distributions: dict[str, dict[str, Any]],
    all_visits: list[dict],
) -> list[dict[str, Any]]:
    if not field_distributions:
        return [
            {
                "kpi": "accuracy",
                "field_path": "form.meta.instanceID",
                "aggregation": "validated_rate",
                "threshold_underperform": 0.75,
                "threshold_target": 0.90,
            }
        ]

    kpis = []
    for i, (path, dist) in enumerate(list(field_distributions.items())[:3]):
        mean = dist.get("mean", 0)
        std = dist.get("stddev", 1)
        kpis.append(
            {
                "kpi": f"metric_{i + 1}",
                "field_path": path,
                "aggregation": "mean",
                "threshold_underperform": round(mean - std, 3),
                "threshold_target": round(mean + 0.5 * std, 3),
            }
        )

    if not kpis:
        kpis.append(
            {
                "kpi": "accuracy",
                "field_path": "form.meta.instanceID",
                "aggregation": "validated_rate",
                "threshold_underperform": 0.75,
                "threshold_target": 0.90,
            }
        )
    return kpis


def profile(
    *,
    opportunity_id: int,
    user_visits: list[dict],
    user_data: list[dict],
    opportunity_detail: dict,
    form_json_paths: list[str] | None = None,
) -> str:
    """Analyze real export data and return a Manifest YAML string.

    Args:
        opportunity_id: The opportunity ID.
        user_visits: Rows from /export/opportunity/<id>/user_visits/.
        user_data: Rows from /export/opportunity/<id>/user_data/.
        opportunity_detail: Dict from /export/opportunity/<id>/.
        form_json_paths: Optional explicit list of form_json dot-paths to
            profile. If omitted, auto-discovers numeric fields from a sample.

    Returns:
        YAML string that validates against Manifest.from_yaml().
    """
    visits_by_flw: dict[str, list[dict]] = defaultdict(list)
    for v in user_visits:
        username = v.get("username")
        if username:
            visits_by_flw[username].append(v)

    opp_name = opportunity_detail.get("name", f"Opportunity {opportunity_id}")

    personas = _profile_flw_personas(visits_by_flw)
    timeline = _profile_timeline(user_visits, visits_by_flw)
    field_dists = _profile_field_distributions(user_visits, form_json_paths)

    entity_ids = {v.get("entity_id") for v in user_visits if v.get("entity_id")}
    cohort_size = max(len(entity_ids), 10)

    kpis = _profile_kpis(field_dists, user_visits)

    manifest_dict = {
        "opportunity_id": opportunity_id,
        "opportunity_name": opp_name,
        "random_seed": 42,
        "timeline": timeline,
        "flw_personas": personas,
        "beneficiary_cohorts": [
            {
                "id": "primary",
                "size": cohort_size,
                "field_distributions": field_dists,
                "progression": "flat",
            }
        ],
        "anomalies": [],
        "kpi_config": kpis,
        "coaching_arcs": [],
    }

    manifest_yaml = yaml.dump(manifest_dict, default_flow_style=False, sort_keys=False)

    try:
        Manifest.from_yaml(manifest_yaml)
    except ManifestValidationError as exc:
        raise ManifestValidationError(f"Profiler produced an invalid manifest (this is a bug): {exc}") from exc

    return manifest_yaml
