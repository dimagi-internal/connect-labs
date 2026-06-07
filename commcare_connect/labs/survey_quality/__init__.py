"""Reusable, extensible survey data-quality algorithm library.

A registry of field-survey quality indicators drawn from the established
literature (DHS/MICS, World Bank LSMS/DIME, J-PAL & IPA back-checks), so any
labs app, workflow, or synthetic-data generator computes them the same way:

    from commcare_connect.labs.survey_quality import run_metrics
    results = run_metrics(round_records, layers=["survey_quality", "backcheck"])
    payload = {r.key: r.to_dict() for r in results}

Three layers ship today:
  - ``survey_quality`` — Layer 1: completeness, GPS, evidence, duration,
    consistency, duplicates.
  - ``backcheck``      — Layer 2: J-PAL Type-1/2/3 error rates, outcome
    proportion test, and the auditable side-by-side comparison rows.
  - ``outlier``        — Layer 3: per-enumerator fabrication screening + a
    composite scorecard (the seam an internal outlier tool plugs into).

Add a new algorithm anywhere with ``@register_metric(...)`` — see ``registry``.

Records are plain dicts. The canonical fields a record may carry:
"""

from __future__ import annotations

from . import metrics as _metrics  # noqa: F401  (registers Layer 1 + 2)
from . import outliers as _outliers  # noqa: F401  (registers Layer 3)
from .registry import REGISTRY, MetricResult, register_metric, results_to_map, run_metrics

# The canonical record schema the algorithms read (a record is a dict). Generators
# should emit these keys; metrics tolerate missing keys (treated as None).
CANONICAL_FIELDS = [
    "record_id",
    "round",
    "opp_id",
    "form_type",  # "primary" | "back_check"
    "household_id",
    "ward",
    "arm",  # "treatment" | "comparison"
    "enumerator_id",
    "lat",
    "lon",
    "assigned_lat",
    "assigned_lon",
    "gps_offset_m",
    "in_ward",
    "start_ts",
    "end_ts",
    "duration_min",
    "evidence_photo",
    "child_present",
    "child_sex",
    "child_age_months",
    "eligible",
    "vitamin_a_received",
    "dose_source",
    "original_record_id",  # back_check -> primary
    "original_enumerator_id",
]

__all__ = [
    "run_metrics",
    "register_metric",
    "results_to_map",
    "MetricResult",
    "REGISTRY",
    "CANONICAL_FIELDS",
]
