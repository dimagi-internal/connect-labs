"""A small, extensible registry of survey data-quality algorithms.

Any labs code or workflow can compute the same indicators the same way:

    from commcare_connect.labs.survey_quality import run_metrics
    results = run_metrics(round_records, layers=["survey_quality", "backcheck"])

Adding a new algorithm is a one-liner — decorate a function that takes the
round's records (a list of canonical record dicts) plus a config dict and
returns a partial result. The registry attaches metadata and the pass/fail
decision:

    @register_metric("my_metric", "My metric", "survey_quality", threshold=95.0)
    def my_metric(records, cfg):
        return {"value": 97.3, "n": len(records), "detail": {...}}

Layers in use: ``survey_quality`` (Layer 1), ``backcheck`` (Layer 2),
``outlier`` (Layer 3 — per-enumerator fabrication screening). The layer string
is free-form, so new layers can be introduced without touching the runner.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field


@dataclass
class MetricResult:
    """One computed indicator. JSON-serializable for workflow ``instance.state``."""

    key: str
    label: str
    layer: str
    value: float | None
    unit: str  # pct | count | ratio | minutes | pvalue
    threshold: float | None
    direction: str  # higher_better | lower_better | none
    passed: bool | None
    n: int | None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Metric:
    key: str
    label: str
    layer: str
    unit: str
    threshold: float | None
    direction: str
    fn: Callable
    params: dict


# Insertion-ordered: results come back in registration order.
REGISTRY: dict[str, _Metric] = {}


def register_metric(
    key: str,
    label: str,
    layer: str,
    *,
    unit: str = "pct",
    threshold: float | None = None,
    direction: str = "higher_better",
    params: dict | None = None,
):
    """Decorator registering an algorithm under ``key``. Idempotent on re-import
    (replaces, so editing a metric and re-importing during dev is safe)."""

    def deco(fn: Callable) -> Callable:
        REGISTRY[key] = _Metric(key, label, layer, unit, threshold, direction, fn, params or {})
        return fn

    return deco


def _decide(value, threshold, direction) -> bool | None:
    if value is None or threshold is None or direction == "none":
        return None
    if direction == "higher_better":
        return value >= threshold
    if direction == "lower_better":
        return value <= threshold
    return None


def run_metrics(
    records: list,
    *,
    layers: list | None = None,
    keys: list | None = None,
    config: dict | None = None,
) -> list:
    """Run registered metrics over one round's ``records`` (primary + back_check).

    Filter by ``layers`` and/or explicit ``keys``. Returns a list of
    ``MetricResult`` in registration order. A metric may override ``threshold``
    or ``passed`` in its returned dict (e.g. the proportion test sets ``passed``
    from a p-value directly).
    """
    cfg = config or {}
    out: list[MetricResult] = []
    for key, m in REGISTRY.items():
        if layers and m.layer not in layers:
            continue
        if keys and key not in keys:
            continue
        res = m.fn(records, cfg) or {}
        value = res.get("value")
        n = res.get("n")
        threshold = res.get("threshold", m.threshold)
        passed = res.get("passed", _decide(value, threshold, m.direction))
        out.append(
            MetricResult(
                key=m.key,
                label=m.label,
                layer=m.layer,
                value=value,
                unit=m.unit,
                threshold=threshold,
                direction=m.direction,
                passed=passed,
                n=n,
                detail=res.get("detail", {}),
            )
        )
    return out


def results_to_map(results: list) -> dict:
    """Index a list of MetricResult by key -> dict (handy for render payloads)."""
    return {r.key: r.to_dict() for r in results}
