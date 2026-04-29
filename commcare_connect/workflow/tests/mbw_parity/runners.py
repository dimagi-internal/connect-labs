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
) -> dict[Any, Any]:
    """Mirror `_aggregation_to_sql` semantics in pure Python.

    Returns a dict {group_value: aggregated_value}.

    Supported aggregations: count, count_unique, count_distinct, sum, avg,
    min, max, first, last, list, median, mode, mode_share.

    Filter semantics: rows where filter_path != filter_value are excluded
    from the aggregation entirely (matches the SQL `FILTER (WHERE ...)` clause).

    Note `field_name` is currently only used for error messages; it's part
    of the signature so callers pattern-match the SQL builder's signature.
    """
    by_group: dict[Any, list[Any]] = {}
    for row in rows:
        if filter_path is not None and str(row.get(filter_path)) != str(filter_value):
            continue
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
