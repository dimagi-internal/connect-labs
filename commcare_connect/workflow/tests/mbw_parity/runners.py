"""Adapters that run "v1" and "v3" computation paths against a fixture.

`run_v1_path` calls the existing Python helpers (templates/mbw_monitoring/...)
to produce a dashboard payload — the ground truth that v3 must match.

`run_v3_pipeline` simulates the v3 pipeline path: it takes the same fixture
rows and runs an in-memory aggregation pass that mirrors the SQL backend's
behaviour for each aggregation type. As real pipeline-native MBW v3 logic
lands in subsequent PRs, this runner stays in sync with the SQL emitter.

Why an in-memory mirror, not just integration tests?
- Postgres-backed integration tests are slow (~seconds) and not easily
  parameterised across many fixtures. The mirror runs in microseconds and
  lets us keep the corner-case fixture set small but exhaustive.
- The mirror's correctness is bounded by a separate SQL-execution sanity
  test (test_aggregation_execution.py) that runs through real Postgres
  and asserts agreement with the mirror.
"""

import statistics
from collections import Counter
from collections.abc import Iterable
from typing import Any

# ---- in-memory aggregation runner ----


def aggregate(
    rows: list[dict],
    *,
    grouping_key: str,
    field_name: str,
    source_path: str,
    aggregation: str,
    filter_path: str | None = None,
    filter_value: Any = None,
    filter_op: str = "eq",
    pre_aggregate_by: str | None = None,
    pre_aggregation: str = "first",
) -> dict[Any, Any]:
    """Mirror `_aggregation_to_sql` semantics in pure Python.

    Returns a dict {group_value: aggregated_value}.

    Supported aggregations: count, count_unique, count_distinct, sum, avg,
    min, max, first, last, list, median, mode, mode_share.

    Filter semantics: rows where filter_path doesn't match filter_value are
    excluded from the aggregation entirely (mirrors the SQL `FILTER (WHERE ...)`
    clause). `filter_op` selects the comparison shape:
    - "eq" (default): exact equality of stringified values.
    - "contains_word": filter_path's value is whitespace-split into tokens;
      matches when filter_value is one of the tokens. Mirrors V1 logic like
      `"ebf" in bf_status.split()`.

    Two-pass: when `pre_aggregate_by` is set, rows are first grouped by
    (grouping_key, pre_aggregate_by) and collapsed using `pre_aggregation`
    to produce one value per pre-group; those per-pre-group values are
    then grouped by `grouping_key` and aggregated using `aggregation`.
    Filter is still applied at the row level (i.e., before pre-aggregation).

    Note `field_name` is currently only used for error messages; it's part
    of the signature so callers pattern-match the SQL builder's signature.
    """
    # Two-pass path: collapse rows into per-pre-group values, then recurse.
    if pre_aggregate_by:
        # Inner: per (grouping_key, pre_aggregate_by) collapse via pre_aggregation.
        # Apply filter at row level so it gates which rows enter the inner agg.
        inner_rows = []
        for row in rows:
            if filter_path is not None:
                field_val = row.get(filter_path)
                if filter_op == "eq":
                    if str(field_val) != str(filter_value):
                        continue
                elif filter_op == "contains_word":
                    tokens = (field_val or "").split() if isinstance(field_val, str) else []
                    if str(filter_value) not in tokens:
                        continue
                else:
                    raise ValueError(f"Unknown filter_op {filter_op!r} for field {field_name!r}")
            if row.get(pre_aggregate_by) is None:
                continue
            inner_rows.append(row)

        # Group by (outer_key, pre_group) and reduce.
        by_pre: dict[tuple, list[Any]] = {}
        for row in inner_rows:
            outer = row.get(grouping_key)
            pre = row.get(pre_aggregate_by)
            by_pre.setdefault((outer, pre), []).append(row.get(source_path))
        per_pre_records = []
        for (outer, _pre), values in by_pre.items():
            collapsed = _reduce(values, pre_aggregation, field_name)
            if collapsed is None:
                continue
            per_pre_records.append({"_outer": outer, "_v": collapsed})

        # Outer: aggregate the per-pre-group values by grouping_key.
        return aggregate(
            per_pre_records,
            grouping_key="_outer",
            field_name=field_name,
            source_path="_v",
            aggregation=aggregation,
        )

    by_group: dict[Any, list[Any]] = {}
    for row in rows:
        if filter_path is not None:
            field_val = row.get(filter_path)
            if filter_op == "eq":
                if str(field_val) != str(filter_value):
                    continue
            elif filter_op == "contains_word":
                tokens = (field_val or "").split() if isinstance(field_val, str) else []
                if str(filter_value) not in tokens:
                    continue
            else:
                raise ValueError(f"Unknown filter_op {filter_op!r} for field {field_name!r}")
        key = row.get(grouping_key)
        val = row.get(source_path)
        by_group.setdefault(key, []).append(val)

    out: dict[Any, Any] = {}
    for group, values in by_group.items():
        out[group] = _reduce(values, aggregation, field_name)
    return out


def _reduce(values: list[Any], aggregation: str, field_name: str) -> Any:
    """Apply an aggregation to a list of values. Returns the reduced value
    (or None when the list reduces to nothing, e.g. all-null with `min`).
    Shared between the single-pass and pre-aggregated paths.
    """
    non_null = [v for v in values if v is not None]
    if aggregation == "count":
        return len(non_null)
    if aggregation in ("count_unique", "count_distinct"):
        return len(set(non_null))
    if aggregation == "sum":
        nums = [float(v) for v in non_null if _is_num(v)]
        return sum(nums)
    if aggregation == "avg":
        nums = [float(v) for v in non_null if _is_num(v)]
        return (sum(nums) / len(nums)) if nums else None
    if aggregation == "min":
        return min(non_null) if non_null else None
    if aggregation == "max":
        return max(non_null) if non_null else None
    if aggregation == "first":
        return non_null[0] if non_null else None
    if aggregation == "last":
        return non_null[-1] if non_null else None
    if aggregation == "list":
        return list(non_null)
    if aggregation == "median":
        nums = [float(v) for v in non_null if _is_num(v)]
        return statistics.median(nums) if nums else None
    if aggregation == "mode":
        return _mode(non_null) if non_null else None
    if aggregation == "mode_share":
        if not non_null:
            return None
        m = _mode(non_null)
        return sum(1 for v in non_null if v == m) / len(non_null)
    raise ValueError(f"Unknown aggregation {aggregation!r} for field {field_name!r}")


def _is_num(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _mode(values: Iterable[Any]) -> Any:
    """Return the most frequent value. Postgres MODE() is unspecified on
    ties — for parity testing, mirror Counter's tie-break (insertion order).
    """
    return Counter(values).most_common(1)[0][0]


# ---- v3 overview-data computation ----
#
# Mirrors what the v3 mbw_monitoring_v3 template + JSX would produce for the
# `overview_data` block of the dashboard payload, given pipeline rows in the
# same shape the runtime would deliver. Used as the "v3 path" in parity tests
# until the actual template + render code lands. Once they do, the live
# pipeline must produce the same numbers; this stays as a fast in-memory
# mirror.


def compute_v3_overview(
    visits: list[dict],
    registrations: list[dict],
    gs_forms: list[dict],
) -> dict:
    """Compute the dashboard's overview_data block via pipeline-equivalent ops.

    Each field corresponds to a declarative pipeline aggregation in the
    forthcoming mbw_monitoring_v3 template:

    - mother_counts: count_unique(mother_case_id) per username on the visits
      pipeline.
    - ebf_pct_by_flw: round(100 * count(filter_op="contains_word", value="ebf")
      / count(non-empty bf_status)) per username on visits with bf_status.
    - form_name_distribution: global histogram of form_name across all visits;
      JSX computes this from the visit-level rows.
    - total_visit_rows / total_registration_forms / total_gs_forms: read
      directly from each pipeline's `metadata.row_count` (here just len()).
    """
    # mother_counts — count_unique(mother_case_id) per username
    mother_counts = aggregate(
        [r for r in visits if r.get("mother_case_id") and r.get("username")],
        grouping_key="username",
        field_name="mother_count",
        source_path="mother_case_id",
        aggregation="count_unique",
    )

    # ebf_pct_by_flw — V1: `if "ebf" in bf_status.split()` over rows where
    # bf_status is non-empty. We compute the numerator (ebf-token count) and
    # denominator (non-empty bf_status count) separately, then take the rounded
    # percentage. The denominator uses an "exists & non-empty" check; the
    # numerator uses contains_word.
    #
    # V1 quirk: _compute_ebf_by_flw lowercases the username before grouping;
    # mother_counts above does NOT. We match v1 exactly so parity holds on
    # mixed-case usernames. v3 SQL will eventually express this via a
    # `transform: "lower"` on the grouping_key.
    ebf_visits = [
        {**r, "username": (r.get("username") or "").strip().lower()}
        for r in visits
        if r.get("username") and isinstance(r.get("bf_status"), str) and r.get("bf_status").strip()
    ]
    ebf_numerator = aggregate(
        ebf_visits,
        grouping_key="username",
        field_name="ebf_count",
        source_path="bf_status",
        aggregation="count",
        filter_path="bf_status",
        filter_value="ebf",
        filter_op="contains_word",
    )
    ebf_denominator = aggregate(
        ebf_visits,
        grouping_key="username",
        field_name="bf_total",
        source_path="bf_status",
        aggregation="count",
    )
    ebf_pct_by_flw = {}
    for username, total in ebf_denominator.items():
        if total > 0:
            ebf_pct_by_flw[username] = round(ebf_numerator.get(username, 0) / total * 100)

    # form_name_distribution — global histogram from visit rows
    form_name_distribution = dict(Counter((r.get("form_name") or "").strip() for r in visits))

    return {
        "mother_counts": mother_counts,
        "ebf_pct_by_flw": ebf_pct_by_flw,
        "form_name_distribution": form_name_distribution,
        "total_visit_rows": len(visits),
        "total_registration_forms": len(registrations),
        "total_gs_forms": len(gs_forms),
    }


# ---- v3 quality-metrics computation (partial slice) ----


def compute_v3_quality(
    visits: list[dict],
    registrations: list[dict],  # noqa: ARG001 — needed for future JOIN-backed leaves
    gs_forms: list[dict],  # noqa: ARG001
) -> dict[str, dict]:
    """Compute the dashboard's quality_metrics block via pipeline-equivalent ops.

    PR #3 slice — parity_concentration only. Future PRs add the other
    quality leaves (phone_dup_pct, age_concentration, anc_pnc_same_date_count,
    age_equals_reg_pct) once cross-pipeline JOIN and cross-form-type
    extraction primitives land.
    """
    # parity_mode_share: per-FLW mode_share over per-mother parities (last seen).
    # Mirrors the v3 template's `parity_mode_share` field.
    parity_mode_share = aggregate(
        [r for r in visits if r.get("form_name") == "ANC Visit" and r.get("mother_case_id")],
        grouping_key="username",
        field_name="parity_mode_share",
        source_path="parity",
        aggregation="mode_share",
        pre_aggregate_by="mother_case_id",
        pre_aggregation="last",
    )
    parity_mode_value = aggregate(
        [r for r in visits if r.get("form_name") == "ANC Visit" and r.get("mother_case_id")],
        grouping_key="username",
        field_name="parity_mode_value",
        source_path="parity",
        aggregation="mode",
        pre_aggregate_by="mother_case_id",
        pre_aggregation="last",
    )

    quality: dict[str, dict] = {}
    all_flws = set(parity_mode_share) | set(parity_mode_value)
    for flw in all_flws:
        share = parity_mode_share.get(flw)
        mode_pct = round(share * 100) if share is not None else 0
        quality[flw] = {
            "parity_concentration": {
                "mode_pct": mode_pct,
                "mode_value": parity_mode_value.get(flw),
            }
        }
    return quality


# ---- v1 reference implementations ----
#
# Side-by-side ground-truth implementations of each overview_data leaf,
# mirroring what v1's Python helpers produce for the SAME inputs (pipeline-
# shaped rows). Kept here in the harness rather than calling v1 directly,
# because v1's helpers expect VisitRow dataclasses and module-level imports
# we want to avoid in fast unit tests. Both v1 and v3 paths must agree
# on the same fixture; if v1's real helper produces something different,
# we update this reference and v3 to match.


def compute_v1_overview_reference(
    visits: list[dict],
    registrations: list[dict],
    gs_forms: list[dict],
) -> dict:
    """Reference implementation of v1's overview_data computation."""
    # mother_counts: distinct mother_case_ids per username
    mother_counts: dict[str, int] = {}
    by_flw_mothers: dict[str, set[str]] = {}
    for row in visits:
        u = row.get("username")
        m = row.get("mother_case_id")
        if not u or not m:
            continue
        by_flw_mothers.setdefault(u, set()).add(m)
    mother_counts = {u: len(s) for u, s in by_flw_mothers.items()}

    # ebf_pct_by_flw: V1's _compute_ebf_by_flw logic verbatim
    ebf_counts: dict[str, dict] = {}
    for row in visits:
        bf = (row.get("bf_status") or "").strip()
        if not bf:
            continue
        u = (row.get("username") or "").strip().lower()
        if not u:
            continue
        ebf_counts.setdefault(u, {"ebf": 0, "total": 0})
        ebf_counts[u]["total"] += 1
        if "ebf" in bf.split():
            ebf_counts[u]["ebf"] += 1
    ebf_pct_by_flw = {u: round(c["ebf"] / c["total"] * 100) for u, c in ebf_counts.items() if c["total"] > 0}

    # form_name_distribution: global histogram
    form_name_distribution = dict(Counter((r.get("form_name") or "").strip() for r in visits))

    return {
        "mother_counts": mother_counts,
        "ebf_pct_by_flw": ebf_pct_by_flw,
        "form_name_distribution": form_name_distribution,
        "total_visit_rows": len(visits),
        "total_registration_forms": len(registrations),
        "total_gs_forms": len(gs_forms),
    }


def compute_v1_quality_reference(
    visits: list[dict],
    registrations: list[dict],  # noqa: ARG001
    gs_forms: list[dict],  # noqa: ARG001
) -> dict[str, dict]:
    """Reference implementation of v1's quality_metrics — parity slice only.

    Mirrors `_extract_per_mother_fields` (overwrite-in-loop = `last` semantics
    on iteration order) plus `_compute_value_concentration.mode_pct` /
    `mode_value`. The other quality leaves (phone_dup_pct, age_concentration,
    anc_pnc_same_date_count, age_equals_reg_pct) need data v3 doesn't yet
    pull, so they're not in this reference.
    """
    # Per-FLW per-mother last parity, ANC visits only.
    by_flw_mother: dict[tuple, str] = {}
    for row in visits:
        if row.get("form_name") != "ANC Visit":
            continue
        u = row.get("username")
        m = row.get("mother_case_id")
        p = row.get("parity")
        if not u or not m or not p:
            continue
        by_flw_mother[(u, m)] = p  # overwrite — `last` semantics

    # Per-FLW collect per-mother parities, then compute mode_pct + mode_value.
    parities_by_flw: dict[str, list[str]] = {}
    for (u, _m), parity in by_flw_mother.items():
        parities_by_flw.setdefault(u, []).append(parity)

    quality: dict[str, dict] = {}
    for u, parities in parities_by_flw.items():
        if not parities:
            continue
        counter = Counter(parities)
        mode_value, mode_count = counter.most_common(1)[0]
        mode_pct = round(mode_count / len(parities) * 100)
        quality[u] = {
            "parity_concentration": {
                "mode_pct": mode_pct,
                "mode_value": mode_value,
            }
        }
    return quality
