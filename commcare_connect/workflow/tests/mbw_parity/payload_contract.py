"""Dashboard payload contract — the keys and tolerances that define v1↔v3 parity.

Every leaf the React render code reads is enumerated here. A parity test
walks this contract and compares v1 against v3 leaf-by-leaf with the
declared tolerance.

Why this file is the source of truth, not the React code:
- React reads from a JSON blob; the schema is implicit in property accesses.
- Encoding the schema here makes "did we break something downstream?"
  a precise question rather than a manual review.

When a new field is added to the dashboard, add it here first, then write
the parity test for it, then implement.
"""

from dataclasses import dataclass, field
from typing import Literal

# Tolerance kinds — picked deliberately:
# - exact: integer counts, dates, strings, dict keys (any inequality fails)
# - epsilon: float values; absolute tolerance |a - b| < epsilon
# - relative: float values where scale matters; |a - b| / max(|a|, |b|, 1) < epsilon
ToleranceKind = Literal["exact", "epsilon", "relative"]


@dataclass(frozen=True)
class Tolerance:
    kind: ToleranceKind
    epsilon: float = 0.0

    @classmethod
    def exact(cls) -> "Tolerance":
        return cls(kind="exact")

    @classmethod
    def of_epsilon(cls, eps: float) -> "Tolerance":
        return cls(kind="epsilon", epsilon=eps)

    @classmethod
    def of_relative(cls, eps: float) -> "Tolerance":
        return cls(kind="relative", epsilon=eps)


@dataclass(frozen=True)
class Leaf:
    """One leaf of the dashboard payload contract.

    `path` is dotted with `[]` for "every element of this list" and `{}`
    for "every value of this dict keyed by FLW username". Wildcards are
    walked uniformly during diffing so that a contract entry like
    `gps_data.flw_summaries[].avg_case_distance_km` defines the
    tolerance for that field across all FLWs.
    """

    path: str
    type: type | str  # type or "list[Leaf]" / "dict[str, Leaf]" sentinel string
    tolerance: Tolerance
    notes: str = ""


# Declarative tolerances:
EXACT = Tolerance.exact()
PCT_EPS = Tolerance.of_epsilon(0.01)  # percentage-style 0..100 values
DIST_EPS_M = Tolerance.of_epsilon(2.0)  # ±2m for SQL PERCENTILE_CONT vs Python median rounding
DIST_EPS_KM = Tolerance.of_epsilon(0.01)  # ±10m as km
TIME_EPS_MIN = Tolerance.of_epsilon(0.5)  # ±30s as minutes


# Top-level dashboard payload contract.
# Every leaf the React render code consumes (mbw_monitoring_v2_render.js)
# appears here. Adding to the dashboard? Add here first.
DASHBOARD_CONTRACT: list[Leaf] = [
    # ---------- gps_data ----------
    Leaf("gps_data.total_visits", int, EXACT),
    Leaf("gps_data.total_flagged", int, EXACT),
    Leaf("gps_data.date_range_start", "str|null", EXACT, "ISO date string or null when no GPS visits"),
    Leaf("gps_data.date_range_end", "str|null", EXACT),
    Leaf("gps_data.flw_summaries[].username", str, EXACT),
    Leaf("gps_data.flw_summaries[].name", str, EXACT),
    Leaf("gps_data.flw_summaries[].total_visits", int, EXACT),
    Leaf("gps_data.flw_summaries[].flagged_visits", int, EXACT),
    Leaf("gps_data.flw_summaries[].avg_case_distance_km", "float|null", DIST_EPS_KM),
    Leaf("gps_data.flw_summaries[].max_case_distance_km", "float|null", DIST_EPS_KM),
    Leaf("gps_data.flw_summaries[].cases_with_revisits", int, EXACT),
    Leaf("gps_data.flw_summaries[].avg_daily_travel_km", "float|null", DIST_EPS_KM),
    Leaf(
        "gps_data.median_meters_by_flw{}",
        int,
        DIST_EPS_M,
        "Median meters between consecutive same-day visits, per FLW",
    ),
    Leaf("gps_data.median_minutes_by_flw{}", int, TIME_EPS_MIN),
    # ---------- followup_data ----------
    Leaf("followup_data.total_cases", int, EXACT),
    Leaf("followup_data.flw_summaries[].username", str, EXACT),
    Leaf("followup_data.flw_summaries[].on_track_pct", float, PCT_EPS),
    Leaf("followup_data.flw_summaries[].late_pct", float, PCT_EPS),
    Leaf("followup_data.flw_summaries[].missed_pct", float, PCT_EPS),
    Leaf("followup_data.visit_status_distribution.approved", int, EXACT),
    Leaf("followup_data.visit_status_distribution.pending", int, EXACT),
    Leaf("followup_data.visit_status_distribution.rejected", int, EXACT),
    Leaf("followup_data.visit_status_distribution.over_limit", int, EXACT),
    # ---------- quality_metrics ----------
    Leaf(
        "quality_metrics{}.parity_concentration_pct",
        float,
        PCT_EPS,
        "Mode-share % of parity per FLW; high = same parity reported repeatedly",
    ),
    Leaf("quality_metrics{}.anc_anomaly_count", int, EXACT),
    Leaf("quality_metrics{}.pnc_anomaly_count", int, EXACT),
    # ---------- performance_data ----------
    Leaf("performance_data[].status", str, EXACT),
    Leaf("performance_data[].flw_count", int, EXACT),
    Leaf("performance_data[].avg_followup_pct", float, PCT_EPS),
    # ---------- overview_data ----------
    Leaf("overview_data.mother_counts{}", int, EXACT),
    Leaf("overview_data.ebf_pct_by_flw{}", int, EXACT),
    Leaf("overview_data.form_name_distribution{}", int, EXACT),
    Leaf("overview_data.total_visit_rows", int, EXACT),
    Leaf("overview_data.total_registration_forms", int, EXACT),
    Leaf("overview_data.total_gs_forms", int, EXACT),
]


@dataclass(frozen=True)
class ContractCoverage:
    """Result of a coverage check — which contract leaves are covered by tests."""

    covered: tuple[str, ...] = field(default_factory=tuple)
    uncovered: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        return len(self.uncovered) == 0
