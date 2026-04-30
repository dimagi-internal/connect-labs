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

    Note `field_name` is currently only used for error messages; it's part
    of the signature so callers pattern-match the SQL builder's signature.
    """
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
        non_null = [v for v in values if v is not None]
        if aggregation == "count":
            out[group] = len(non_null)
        elif aggregation in ("count_unique", "count_distinct"):
            out[group] = len(set(non_null))
        elif aggregation == "sum":
            nums = [float(v) for v in non_null if _is_num(v)]
            out[group] = sum(nums)
        elif aggregation == "avg":
            nums = [float(v) for v in non_null if _is_num(v)]
            out[group] = (sum(nums) / len(nums)) if nums else None
        elif aggregation == "min":
            out[group] = min(non_null) if non_null else None
        elif aggregation == "max":
            out[group] = max(non_null) if non_null else None
        elif aggregation == "first":
            out[group] = non_null[0] if non_null else None
        elif aggregation == "last":
            out[group] = non_null[-1] if non_null else None
        elif aggregation == "list":
            out[group] = list(non_null)
        elif aggregation == "median":
            nums = [float(v) for v in non_null if _is_num(v)]
            out[group] = statistics.median(nums) if nums else None
        elif aggregation == "mode":
            out[group] = _mode(non_null) if non_null else None
        elif aggregation == "mode_share":
            if not non_null:
                out[group] = None
            else:
                m = _mode(non_null)
                out[group] = sum(1 for v in non_null if v == m) / len(non_null)
        else:
            raise ValueError(f"Unknown aggregation {aggregation!r} for field {field_name!r}")
    return out


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
