"""Pure-Python reference implementations for SQL-backend aggregation tests.

These mirror the Postgres aggregation/haversine semantics so the SQL backend
can be exercised and bounded against an independent in-memory implementation.

Originally lived in the MBW v1↔v3 parity harness (`workflow/tests/mbw_parity/
runners.py`); rehomed here when that harness was retired, since the SQL backend
tests (`test_aggregation_execution`, `test_haversine`) are the only remaining
consumers.
"""

import math
import statistics
from collections import Counter
from collections.abc import Iterable
from typing import Any

# ---- haversine distance ----

_EARTH_RADIUS_M = 6371000.0  # same constant as the SQL function and v1


def haversine_meters(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float | None:
    """Great-circle distance in meters between two lat/lon points.

    Returns None when any coordinate is None — sparse-GPS-friendly, mirrors
    the SQL function. Float-precision-equivalent to v1's gps_utils.haversine_distance.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_M * c


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
    if aggregation == "dup_share":
        # Share (0..1) of values that appear in duplicate groups (count > 1).
        # Mirrors v1's _compute_value_concentration.pct_duplicate (without the
        # *100 + round — caller does that if it wants a percentage).
        if not non_null:
            return None
        counts = Counter(non_null)
        dup_count = sum(c for c in counts.values() if c > 1)
        return dup_count / len(non_null)
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
    ties — mirror Counter's tie-break (insertion order).
    """
    return Counter(values).most_common(1)[0][0]
