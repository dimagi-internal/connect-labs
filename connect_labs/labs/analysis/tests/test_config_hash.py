"""Regression tests for get_config_hash cache-invalidation coverage.

Guards issue #957: the entity-result cache key must change when the entity
grouping key (`linking_field`) changes, and when a field/histogram defined via
the multi-path `paths` list changes. Previously the hash read only the singular
`field.path` and omitted `linking_field`, so those edits silently served a
stale cached result.
"""

from connect_labs.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    FieldComputation,
    HistogramComputation,
)
from connect_labs.labs.analysis.utils import get_config_hash


def _entity_config(**overrides):
    base = dict(
        grouping_key="username",
        terminal_stage=CacheStage.ENTITY,
        linking_field="child_case_id",
        fields=[
            FieldComputation(name="muac", path="form.case.update.soliciter_muac_cm", aggregation="max"),
        ],
    )
    base.update(overrides)
    return AnalysisPipelineConfig(**base)


def test_linking_field_change_invalidates_hash():
    """Changing the entity GROUP BY key must produce a different hash (#957)."""
    a = _entity_config(linking_field="child_case_id")
    b = _entity_config(linking_field="entity_name")
    assert get_config_hash(a) != get_config_hash(b)


def test_identical_configs_hash_equal():
    """Sanity: same config → same hash (hash is deterministic)."""
    assert get_config_hash(_entity_config()) == get_config_hash(_entity_config())


def test_field_paths_list_change_invalidates_hash():
    """A field defined only via `paths` must perturb the hash when `paths` change (#957)."""
    a = _entity_config(
        fields=[FieldComputation(name="muac", paths=["form.a", "form.b"], aggregation="max")],
    )
    b = _entity_config(
        fields=[FieldComputation(name="muac", paths=["form.a", "form.c"], aggregation="max")],
    )
    assert get_config_hash(a) != get_config_hash(b)


def test_histogram_paths_list_change_invalidates_hash():
    """A histogram's `paths` change must perturb the hash (#957)."""
    common = dict(name="dist", path="", lower_bound=9.5, upper_bound=21.5, num_bins=12)
    a = _entity_config(histograms=[HistogramComputation(paths=["form.a", "form.b"], **common)])
    b = _entity_config(histograms=[HistogramComputation(paths=["form.a", "form.c"], **common)])
    assert get_config_hash(a) != get_config_hash(b)
