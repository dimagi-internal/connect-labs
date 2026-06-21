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
import random
import statistics
from collections import Counter, defaultdict
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .manifest import Manifest, ManifestValidationError
from .mirror import profile_entity_structure
from .schema_loader import FormSchema, parse_form_schema_from_app_json


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


# Per-archetype minimum flag rate applied only when curate=True. Real KMC opps are
# ~100% approved, which leaves approval_rate with no variance for analytics; flooring
# (scaled per-opp) gives a realistic approved/pending/rejected mix without inventing
# a story per FLW. Floors stay modest so most visits remain approved.
_ARCHETYPE_FLAG_FLOOR = {"rockstar": 0.03, "steady": 0.08, "struggling": 0.16, "new_hire": 0.27}


def _profile_flw_personas(
    visits_by_flw: dict[str, list[dict]],
    *,
    curate: bool = False,
    opp_jitter: float = 1.0,
) -> list[dict[str, Any]]:
    personas = []
    for i, (username, visits) in enumerate(sorted(visits_by_flw.items(), key=lambda kv: -len(kv[1]))):
        total = len(visits)
        approved = sum(1 for v in visits if v.get("status") == "approved")
        flagged = sum(1 for v in visits if v.get("flagged"))

        approval_rate = _safe_rate(approved, total)
        flag_rate = _safe_rate(flagged, total)
        archetype = _classify_archetype(approval_rate, flag_rate)

        if curate:
            # Floor the flag rate (scaled by the per-opp jitter so opps differ) and
            # keep approval consistent with it.
            floor = min(0.6, _ARCHETYPE_FLAG_FLOOR[archetype] * opp_jitter)
            flag_rate = round(max(flag_rate, floor), 3)
            approval_rate = round(1.0 - flag_rate, 3)

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

        if not values:
            continue

        mean, std = _mean_std(values)
        if len(values) >= 5 and std >= 0.001:
            # Robust observed bounds (central 98%) so generated draws stay in the real
            # range — an unbounded Normal otherwise emits impossible values (e.g. a
            # negative child_age from N(13.5, 12.9)). p1/p99 also trims data-entry
            # outliers rather than reproducing them.
            lo = round(float(np.percentile(values, 1)), 3)
            hi = round(float(np.percentile(values, 99)), 3)
            distributions[path] = {
                "distribution": "normal",
                "mean": round(mean, 3),
                "stddev": round(std, 3),
                "lo": lo,
                "hi": hi,
            }
        else:
            # Too few samples for a Normal, or near-constant: still model the field as
            # a uniform over its observed range (degenerate when constant). This keeps
            # a real numeric field from being left unmodeled and silently filled with
            # the randint(0,10) stub — the "~5 g weight" leak (issue #713 #4).
            distributions[path] = {
                "distribution": "uniform",
                "low": round(min(values), 3),
                "high": round(max(values), 3),
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


def _classify_paths(form_schema: FormSchema) -> dict[str, str]:
    """Return a mapping of json_path -> kind for all questions in the schema."""
    return {q.json_path: q.kind for q in form_schema.questions}


def _profile_categorical(visits: list[dict], path: str) -> dict[str, float]:
    """Return value -> rate mapping for a categorical field at dotted path."""
    counts: Counter[str] = Counter()
    for v in visits:
        raw = _extract_nested(v.get("form_json") or {}, path)
        if raw in (None, ""):
            continue
        counts[str(raw)] += 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: round(c / total, 4) for k, c in counts.items()}


_NEGATIVE_TOKENS = {"no", "0", "false", "absent", "none", "negative", "n", "normal"}
_AFFIRMATIVE_FOR = {
    "no": "yes",
    "0": "1",
    "false": "true",
    "absent": "present",
    "none": "present",
    "negative": "positive",
    "n": "y",
    "normal": "abnormal",
}


def _curate_categorical(values: dict[str, float], target_minority: float) -> dict[str, float]:
    """Give a degenerate categorical realistic minority mass so it carries signal.

    Real KMC clinical flags (danger signs, referrals) are ~0% in the export, so the
    derived rates have no variance. When one value dominates (>= 95%):
    - multi-value field: rebalance so the minority values share ``target_minority``;
    - single binary-negative field (all 'no'/'0'): inject the affirmative outcome at
      ``target_minority`` (e.g. danger_sign 'yes' ~ 10%).
    A single non-binary value (e.g. an 'ok' label) is left unchanged — we won't invent
    a meaningless second category.
    """
    if not values:
        return values
    items = sorted(values.items(), key=lambda kv: -kv[1])
    top, top_rate = items[0]
    if top_rate < 0.95:
        return values  # already has signal
    keep_top = round(1.0 - target_minority, 4)
    if len(items) == 1:
        aff = _AFFIRMATIVE_FOR.get(top.lower())
        if aff is None:
            return values  # single non-binary value -> nothing meaningful to add
        return {top: keep_top, aff: round(target_minority, 4)}
    minority = dict(items[1:])
    msum = sum(minority.values()) or 1.0
    out = {top: keep_top}
    for k, v in minority.items():
        out[k] = round(target_minority * v / msum, 4)
    return out


# Worst performers carry seeded anomalies most plausibly — order archetypes weakest-first.
_ARCHETYPE_RANK = {"new_hire": 0, "struggling": 1, "steady": 2, "rockstar": 3}


def _seed_anomalies(
    personas: list[dict[str, Any]],
    field_distributions: dict[str, dict[str, Any]],
    *,
    weeks: int,
    opp_id: int,
    jitter: float,
) -> list[dict[str, Any]]:
    """Plant a handful of deliberate QA anomalies so audit dashboards and Scout's
    eval flows have something to find (issue #670 item #10). Only called under
    ``curate=True``; faithful profiling invents nothing.

    Seeds at least a duplicate_submission and a missing_visits (the coverage/dedup
    signals), plus field_outliers on a real numeric field when one exists. Carriers
    skew toward weaker FLWs. A dedicated ``opp_id``-seeded RNG keeps the set
    deterministic per opp yet distinct opp-to-opp (so cross-opp comparison sees
    different QA stories), and does NOT disturb the curation RNG stream above.
    """
    if not personas:
        return []
    rng = random.Random(opp_id * 7919 + 1)
    ranked = sorted(personas, key=lambda p: _ARCHETYPE_RANK.get(p.get("archetype", ""), 2))
    carriers = [p["id"] for p in ranked[: max(1, len(ranked) // 2 + 1)]]
    numeric_paths = sorted(p for p, d in field_distributions.items() if d.get("distribution") == "normal")
    span = max(1, weeks)

    out: list[dict[str, Any]] = []

    def add(atype: str, **extra: Any) -> None:
        out.append(
            {
                "id": f"seed_{atype}_{len(out) + 1}",
                "type": atype,
                "flw_ids": [rng.choice(carriers)],
                "week": rng.randint(1, span),
                **extra,
            }
        )

    if numeric_paths:
        add("field_outlier", field_path=rng.choice(numeric_paths))
    add("duplicate_submission")
    add("missing_visits")

    # A per-opp number of extra anomalies (0–2), scaled by the opp jitter, so opps
    # differ in how much QA noise they carry without ever burying the data in it.
    for _ in range(min(2, int(jitter * rng.uniform(0.4, 1.8)))):
        if numeric_paths and rng.random() < 0.6:
            add("field_outlier", field_path=rng.choice(numeric_paths))
        else:
            add(rng.choice(["duplicate_submission", "missing_visits"]))
    return out


def _profile_null_rate(visits: list[dict], path: str) -> float:
    """Return the fraction of visits where the field at path is None or ''."""
    present = 0
    for v in visits:
        raw = _extract_nested(v.get("form_json") or {}, path)
        if raw not in (None, ""):
            present += 1
    n = len(visits)
    return round(1.0 - present / n, 4) if n else 0.0


def _walk_repeat_lists(obj: dict, prefix: str, found: dict[str, list]) -> None:
    """Collect every list-of-dict value (a CommCare repeat group) keyed by dotted path.

    A list of plain scalars (e.g. a multi-select) is not a repeat — only lists whose
    entries are all dicts qualify (an empty list is recorded but, on its own, is not
    enough to call a path a repeat; see ``_profile_repeat_groups``)."""
    for key, val in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, list):
            if all(isinstance(el, dict) for el in val):
                found.setdefault(path, []).append(val)
        elif isinstance(val, dict):
            _walk_repeat_lists(val, path, found)


def _flatten_element(obj: dict, prefix: str, acc: dict[str, list]) -> None:
    """Collect a repeat instance's scalar leaf values by path relative to the element."""
    for key, val in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            _flatten_element(val, path, acc)
        elif isinstance(val, list):
            continue  # nested repeats are out of scope for v1
        elif val not in (None, ""):
            acc.setdefault(path, []).append(val)


def _distribution_for_values(values: list) -> dict[str, Any] | None:
    """Pick a normal (numeric, with robust bounds) or categorical distribution for a
    repeat child's observed values — mirrors the scalar profiler's typing logic."""
    nums: list[float] = []
    numeric = True
    for x in values:
        try:
            nums.append(float(x))
        except (ValueError, TypeError):
            numeric = False
            break
    if numeric and len(nums) >= 5:
        mean, std = _mean_std(nums)
        if std >= 0.001:
            return {
                "distribution": "normal",
                "mean": round(mean, 3),
                "stddev": round(std, 3),
                "lo": round(float(np.percentile(nums, 1)), 3),
                "hi": round(float(np.percentile(nums, 99)), 3),
            }
    counts = Counter(str(x) for x in values)
    total = sum(counts.values())
    if total == 0:
        return None
    return {"distribution": "categorical", "values": {k: round(c / total, 4) for k, c in counts.items()}}


def _profile_repeat_groups(all_visits: list[dict]) -> dict[str, dict[str, Any]]:
    """Detect repeat groups in real form_json and reproduce their shape (issue #670 #6).

    For each path that appears as a list-of-dicts, capture the instance-count
    distribution and a per-relative-child-field distribution, so the generator can
    emit faithful JSON arrays of 0–N sub-records instead of a single object. Inert
    when no repeats exist — purely additive and data-driven."""
    occurrences: dict[str, list] = {}
    for v in all_visits:
        _walk_repeat_lists(v.get("form_json") or {}, "", occurrences)

    out: dict[str, dict[str, Any]] = {}
    for path, lists in occurrences.items():
        if not any(len(lst) > 0 for lst in lists):
            continue  # only ever empty -> no evidence of a dict-structured repeat
        counts = [len(lst) for lst in lists]
        total = len(counts)
        count_dist = {int(k): round(c / total, 4) for k, c in Counter(counts).items()}

        child_values: dict[str, list] = {}
        for lst in lists:
            for el in lst:
                _flatten_element(el, "", child_values)
        field_dists: dict[str, Any] = {}
        for rel_path, vals in child_values.items():
            dist = _distribution_for_values(vals)
            if dist is not None:
                field_dists[rel_path] = dist

        out[path] = {"count": count_dist, "field_distributions": field_dists}
    return out


def _profile_temporal(visits: list[dict]) -> dict:
    """Return day-of-week (7) and hour-of-day (24) weight vectors from visit timestamps."""
    dow = [0.0] * 7
    hod = [0.0] * 24
    for v in visits:
        ds = v.get("visit_date")
        if not ds:
            continue
        try:
            d = dt.date.fromisoformat(ds[:10])
        except ValueError:
            continue
        dow[d.weekday()] += 1
        # hour: use date_created if it's a datetime string; otherwise skip
        created = v.get("date_created")
        if isinstance(created, str) and "T" in created:
            try:
                hod[dt.datetime.fromisoformat(created).hour] += 1
            except ValueError:
                pass
    if sum(hod) == 0:
        hod = [1.0] * 24
    if sum(dow) == 0:
        dow = [1.0] * 7
    return {"day_of_week": dow, "hour_of_day": hod}


def _profile_flag_reasons(visits: list[dict]) -> dict[str, float]:
    """Return flag_reason -> rate map for flagged visits that carry a reason."""
    counts: Counter[str] = Counter()
    for v in visits:
        if v.get("flagged") and v.get("flag_reason"):
            counts[str(v["flag_reason"])] += 1
    total = sum(counts.values())
    return {k: round(c / total, 4) for k, c in counts.items()} if total else {}


def _profile_weekly_volume(visits: list[dict], start_date: dt.date, weeks: int) -> list[float] | None:
    """Return a per-week relative-volume list (avg = 1.0), or None if < 2 weeks."""
    if weeks < 2:
        return None
    per_week = [0] * weeks
    for v in visits:
        ds = v.get("visit_date")
        if not ds:
            continue
        try:
            d = dt.date.fromisoformat(ds[:10])
        except ValueError:
            continue
        idx = (d - start_date).days // 7
        if 0 <= idx < weeks:
            per_week[idx] += 1
    avg = sum(per_week) / weeks
    if avg <= 0:
        return None
    return [round(c / avg, 3) for c in per_week]


_MIN_CORR_COVERAGE = 0.5


def _profile_correlation(visits: list[dict], paths: list[str], kinds: dict[str, str]) -> dict | None:
    """Spearman rank-correlation over numeric + ordinal-encoded categorical paths.

    Categoricals are encoded by frequency rank so a copula can reproduce their
    rank-association with numeric fields. Returns None if < 2 usable columns.
    """
    cols: dict[str, list] = {}
    n = len(visits)
    for path in paths:
        kind = kinds.get(path, "decimal")
        raw_vals = [_extract_nested(v.get("form_json") or {}, path) for v in visits]
        present = [r for r in raw_vals if r not in (None, "")]
        if n == 0 or len(present) / n < _MIN_CORR_COVERAGE:
            continue
        if kind in {"select", "multiselect", "text"}:
            order = {
                val: i
                for i, (val, _) in enumerate(
                    sorted(
                        Counter(str(r) for r in present).items(),
                        key=lambda kv: -kv[1],
                    )
                )
            }
            cols[path] = [order.get(str(r), np.nan) if r not in (None, "") else np.nan for r in raw_vals]
        else:
            out = []
            for r in raw_vals:
                try:
                    out.append(float(r))
                except (TypeError, ValueError):
                    out.append(np.nan)
            cols[path] = out
    if len(cols) < 2:
        return None
    df = pd.DataFrame(cols)
    corr = df.corr(method="spearman").fillna(0.0)
    mat = corr.to_numpy(copy=True)
    np.fill_diagonal(mat, 1.0)
    return {
        "fields": list(corr.columns),
        "matrix": [[round(float(x), 4) for x in row] for row in mat.tolist()],
        "method": "spearman",
    }


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
    app_structure: dict | None = None,
    curate: bool = False,
    mirror: bool = False,
) -> str:
    """Analyze real export data and return a Manifest YAML string.

    Args:
        opportunity_id: The opportunity ID.
        user_visits: Rows from /export/opportunity/<id>/user_visits/.
        user_data: Rows from /export/opportunity/<id>/user_data/.
        opportunity_detail: Dict from /export/opportunity/<id>/.
        form_json_paths: Optional explicit list of form_json dot-paths to
            profile. If omitted, auto-discovers numeric fields from a sample.
        app_structure: Optional dict from /export/opportunity/<id>/app_structure/.
            When provided, used to type fields — select/multiselect paths get
            categorical distributions and all profiled paths get null_rate.
            Callers that omit this arg get identical prior behaviour.

    Returns:
        YAML string that validates against Manifest.from_yaml().
    """
    visits_by_flw: dict[str, list[dict]] = defaultdict(list)
    for v in user_visits:
        username = v.get("username")
        if username:
            visits_by_flw[username].append(v)

    opp_name = opportunity_detail.get("name", f"Opportunity {opportunity_id}")

    # Per-opp deterministic jitter so a curated cohort varies opp-to-opp (one opp
    # runs hotter on flags / clinical prevalence than another) without hand-authoring.
    opp_rng = random.Random(opportunity_id)
    opp_jitter = round(opp_rng.uniform(0.7, 1.4), 3) if curate else 1.0

    personas = _profile_flw_personas(visits_by_flw, curate=curate, opp_jitter=opp_jitter)
    timeline = _profile_timeline(user_visits, visits_by_flw)
    field_dists = _profile_field_distributions(user_visits, form_json_paths)

    kinds: dict[str, str] = {}

    # If caller provided app_structure, derive field types and enrich distributions.
    if app_structure is not None:
        form_schema = parse_form_schema_from_app_json(app_structure, app_type="deliver")
        kinds = _classify_paths(form_schema)

        # Model every numeric schema field, even one too sparsely present to be
        # auto-discovered, so nothing real is left for the engine's randint(0,10)
        # stub to fill (issue #713 #4). Already-profiled paths are left untouched.
        for path, kind in kinds.items():
            if kind in {"int", "decimal"} and path not in field_dists:
                extra = _profile_field_distributions(user_visits, [path])
                if path in extra:
                    field_dists[path] = extra[path]

        # Attach null_rate to every numeric distribution we profiled.
        for path, dist in field_dists.items():
            dist["null_rate"] = _profile_null_rate(user_visits, path)

        # Add categorical distributions for select/multiselect paths not already covered.
        for path, kind in kinds.items():
            if kind in {"select", "multiselect"} and path not in field_dists:
                values = _profile_categorical(user_visits, path)
                if values:
                    if curate:
                        target = round(min(0.3, opp_rng.uniform(0.05, 0.18) * opp_jitter), 4)
                        values = _curate_categorical(values, target)
                    field_dists[path] = {
                        "distribution": "categorical",
                        "values": values,
                        "null_rate": _profile_null_rate(user_visits, path),
                    }

    entity_ids = {v.get("entity_id") for v in user_visits if v.get("entity_id")}
    cohort_size = max(len(entity_ids), 10)

    kpis = _profile_kpis(field_dists, user_visits)

    # Compute new profiling blocks.
    correlation = _profile_correlation(user_visits, list(field_dists.keys()), kinds)
    temporal = _profile_temporal(user_visits)
    flag_reasons = _profile_flag_reasons(user_visits)

    # Attach weekly volume multipliers to the (mutable) timeline dict.
    start_date_obj = dt.date.fromisoformat(timeline["start_date"])
    weekly = _profile_weekly_volume(user_visits, start_date_obj, timeline["weeks"])
    if weekly is not None:
        timeline["weekly_volume_multipliers"] = weekly

    cohort: dict = {
        "id": "primary",
        "size": cohort_size,
        "field_distributions": field_dists,
        "progression": "flat",
    }
    if correlation is not None:
        cohort["correlation"] = correlation

    # Reproduce repeat groups (JSON arrays of sub-records) found in the real data.
    repeat_groups = _profile_repeat_groups(user_visits)
    if repeat_groups:
        cohort["repeat_groups"] = repeat_groups

    # High-fidelity 'close mirror': carry a de-identified per-entity transplant pool
    # so the engine replays the source's exact visits/case, cases/FLW, timing and
    # value trajectories (issue #713 #2). Owners are remapped from source usernames
    # to persona ids (the same volume ranking _profile_flw_personas uses), so the
    # pool references the manifest's personas and never leaks a real username.
    if mirror:
        numeric_paths = {p for p, d in field_dists.items() if d.get("distribution") in ("normal", "uniform")}
        structure = profile_entity_structure(user_visits, numeric_paths=numeric_paths)
        ranked = sorted(visits_by_flw.items(), key=lambda kv: -len(kv[1]))
        username_to_persona = {username: f"flw_{i + 1:03d}" for i, (username, _) in enumerate(ranked)}
        pool = []
        for series in structure.transplant_pool:
            persona = username_to_persona.get(series["owner"])
            if persona is None:
                continue
            pool.append({**series, "owner": persona})
        if pool:
            cohort["longitudinal"] = {"mode": "mirror", "transplant_pool": pool}

    # Seed deliberate QA anomalies (only under curation) so dashboards/evals have
    # something to find. Faithful profiling leaves this empty.
    anomalies = (
        _seed_anomalies(personas, field_dists, weeks=timeline["weeks"], opp_id=opportunity_id, jitter=opp_jitter)
        if curate
        else []
    )

    manifest_dict = {
        "opportunity_id": opportunity_id,
        "opportunity_name": opp_name,
        "random_seed": 42,
        "timeline": timeline,
        "flw_personas": personas,
        "beneficiary_cohorts": [cohort],
        "anomalies": anomalies,
        "kpi_config": kpis,
        "coaching_arcs": [],
        "temporal": temporal,
        "flag_reason_distribution": flag_reasons,
    }

    manifest_yaml = yaml.dump(manifest_dict, default_flow_style=False, sort_keys=False)

    try:
        Manifest.from_yaml(manifest_yaml)
    except ManifestValidationError as exc:
        raise ManifestValidationError(f"Profiler produced an invalid manifest (this is a bug): {exc}") from exc

    return manifest_yaml
