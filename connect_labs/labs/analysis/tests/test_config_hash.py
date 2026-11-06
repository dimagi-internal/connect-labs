"""Regression tests for get_config_hash cache-invalidation coverage.

Guards issue #957: the entity-result cache key must change when the entity
grouping key (`linking_field`) changes, and when a field/histogram defined via
the multi-path `paths` list changes. Previously the hash read only the singular
`field.path` and omitted `linking_field`, so those edits silently served a
stale cached result.
"""

import pytest

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


def _flw_config(**field_kwargs):
    field = FieldComputation(
        name="sachets", path="form.rutf_dispensing.rutf_sachets_dispensed", aggregation="sum", **field_kwargs
    )
    return AnalysisPipelineConfig(grouping_key="username", terminal_stage=CacheStage.AGGREGATED, fields=[field])


@pytest.mark.parametrize(
    "before, after",
    [
        ({}, {"filter_path": "status", "filter_value": "approved"}),
        ({"filter_path": "status", "filter_value": "approved"}, {"filter_path": "status", "filter_value": "rejected"}),
        (
            {"filter_path": "form.@name", "filter_value": "Visit"},
            {"filter_paths": ["form.@name"], "filter_value": "Visit"},
        ),
        (
            {"filter_path": "form.x", "filter_value": "a"},
            {"filter_path": "form.x", "filter_value": "a", "filter_op": "contains_word"},
        ),
        ({}, {"pre_aggregate_by": "form.meta.instanceID"}),
        (
            {"pre_aggregate_by": "form.meta.instanceID"},
            {"pre_aggregate_by": "form.meta.instanceID", "pre_aggregation": "max"},
        ),
        (
            {"pre_aggregate_by": "form.case.@case_id"},
            {"pre_aggregate_by": "form.case.@case_id", "pre_aggregate_attribute_to": "last_username"},
        ),
    ],
)
def test_field_filter_and_pre_aggregation_change_invalidates_hash(before, after):
    """A field's FILTER and two-pass collapse change its aggregate, so they must perturb the hash.

    Left out, adding pre_aggregate_by (to count each submission once) and a status filter
    to a saved FLW pipeline kept serving the old plain sums until the cache expired.
    """
    assert get_config_hash(_flw_config(**before)) != get_config_hash(_flw_config(**after))


def test_field_without_filter_or_pre_aggregation_keeps_its_hash():
    """A field that declares neither keeps the hash its caches were written under."""
    assert get_config_hash(_flw_config()) == PLAIN_FLW_HASH


# get_config_hash of `_flw_config()`, computed with origin/main before filters were hashed.
PLAIN_FLW_HASH = "707d77293717"
