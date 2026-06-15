"""Plan-grounded synthetic survey generation (labs).

The generation counterpart to ``...generator.core.survey_quality``: turn a
plan's sampled work areas (real primary/alternate footprint centroids) into a
representative run of household survey records, grounded on those locations.

    from commcare_connect.labs.synthetic.generator.core.survey_sim import simulate_plan, SimParams
    recs = simulate_plan(work_areas, SimParams(...), rng, ward_name="Tse")

Pure (no Django/DB/network) so it imports anywhere and is unit-testable. The
records carry every field the survey_quality metrics + the Verified Monitoring
back-check/scorecard assembly consume, plus ``sample_type`` / ``cluster`` /
``work_area_id`` for the primary-vs-alternate mix.
"""

from .backcheck import simulate_backchecks
from .params import PrimaryRate, SimParams
from .plan import cluster_surveyors, simulate_plan
from .scatter import scatter_primaries

__all__ = [
    "simulate_plan",
    "cluster_surveyors",
    "scatter_primaries",
    "simulate_backchecks",
    "SimParams",
    "PrimaryRate",
]
