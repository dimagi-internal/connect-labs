"""Entity-stage `filters` restrict the rows aggregated; per-field filter_path/filter_value
restricts `first` / `last` / `list`; upper-case field names are refused at save.

All three surfaced building the interview summary pipelines over a Drive folder
(workflow 23765): an entity pipeline filtered to one state and one answer basis
returned every state's totals, and `first(typology_name)` filtered on each typology
returned the same name for all four. Driven through the real pipeline path
(stream_analysis -> SQL), with rows shaped the way the Drive fetcher shapes them.
"""

import pytest

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_fetcher import (  # noqa: F401 - fixtures
    OPP,
    STAFF,
    allowed_root,
    drive,
)
from connect_labs.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
    field_name_problem,
)
from connect_labs.labs.analysis.pipeline import AnalysisPipeline

ANSWERS = (
    b"qid,state,classification_basis,typology_id,typology_name\n"
    b"4.12,Kebbi,answered_clean,T1,Workload\n"
    b"4.12,Kebbi,answered_clean,T2,System & Resource Gaps\n"
    b"4.12,Kebbi,answered_clean,T1,Workload\n"
    b"4.12,Kebbi,unclear,T3,Training\n"
    b"4.12,Borno,answered_clean,T2,System & Resource Gaps\n"
    b"4.12,Borno,answered_clean,T4,Payment\n"
    b"5.01,Kebbi,answered_clean,T3,Training\n"
)


@pytest.fixture
def answers(drive):  # noqa: F811
    drive.files["answers"] = {
        "name": "answers_scored_x.csv",
        "mimeType": "text/csv",
        "parent": "folderA",
        "content": ANSWERS,
    }
    return drive


def _config(pipeline_id, fields, *, filters=None, stage=CacheStage.ENTITY, linking_field="qid"):
    stamped = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "answers"}, OPP, STAFF, pipeline_id=pipeline_id)
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=fields,
        terminal_stage=stage,
        linking_field=linking_field,
        filters=filters or {},
        data_source=DataSourceConfig(**stamped),
        pipeline_id=pipeline_id,
    )


def _rows(config):
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP))
    assert events[-1][0] == "result", events[-1]
    return {r.entity_id: r for r in events[-1][1].rows}


BASE_FIELDS = [
    FieldComputation(name="qid", path="row.qid", aggregation="first"),
    FieldComputation(name="state", path="row.state", aggregation="first"),
    FieldComputation(name="basis", path="row.classification_basis", aggregation="first"),
]


@pytest.mark.django_db
def test_entity_filters_on_declared_fields_restrict_the_aggregated_rows(answers):
    filtered = _config(301, BASE_FIELDS, filters={"basis": ["answered_clean"], "state": ["Kebbi"]})
    rows = _rows(filtered)
    assert rows["4.12"].total_visits == 3  # not 6: Borno and `unclear` are out
    assert rows["4.12"].custom_fields["state"] == "Kebbi"
    assert rows["5.01"].total_visits == 1


@pytest.mark.django_db
def test_a_warm_unfiltered_entity_cache_is_not_served_to_a_filtered_config(answers):
    unfiltered = _config(302, BASE_FIELDS)
    assert _rows(unfiltered)["4.12"].total_visits == 6
    # Same pipeline, now filtered: must not read the unfiltered aggregates back.
    filtered = _config(302, BASE_FIELDS, filters={"state": "Borno"})
    rows = _rows(filtered)
    assert rows["4.12"].total_visits == 2
    assert "5.01" not in rows


def _typology_fields(agg):
    return BASE_FIELDS[:1] + [
        FieldComputation(
            name=f"t{n}_name",
            path="row.typology_name",
            aggregation=agg,
            filter_path="row.typology_id",
            filter_value=f"T{n}",
        )
        for n in (1, 2, 3, 4)
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("agg", ["first", "last"])
def test_first_and_last_honour_the_per_field_filter(answers, agg):
    row = _rows(_config(303, _typology_fields(agg)))["4.12"].custom_fields
    assert row["t1_name"] == "Workload"
    assert row["t2_name"] == "System & Resource Gaps"
    assert row["t3_name"] == "Training"
    assert row["t4_name"] == "Payment"


@pytest.mark.django_db
def test_list_honours_the_per_field_filter(answers):
    fields = BASE_FIELDS[:1] + [
        FieldComputation(
            name="t1_states", path="row.state", aggregation="list", filter_path="row.typology_id", filter_value="T1"
        )
    ]
    row = _rows(_config(304, fields))["4.12"].custom_fields
    assert sorted(row["t1_states"]) == ["Kebbi", "Kebbi"]


@pytest.mark.django_db
def test_filtered_count_still_counts_only_matching_rows(answers):
    fields = BASE_FIELDS[:1] + [
        FieldComputation(
            name="t2", path="row.typology_id", aggregation="count", filter_path="row.typology_id", filter_value="T2"
        )
    ]
    assert _rows(_config(305, fields))["4.12"].custom_fields["t2"] == 2


@pytest.mark.parametrize("name", ["T0", "typologyName", "1st", "has space", "", None])
def test_field_names_that_would_read_back_null_are_refused(name):
    assert field_name_problem(name)


@pytest.mark.parametrize("name", ["t0", "typology_name", "_x", "q4_12"])
def test_lower_case_field_names_are_fine(name):
    assert field_name_problem(name) is None


def test_mcp_schema_validation_refuses_upper_case_field_names():
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools.pipelines import _validate_pipeline_schema

    with pytest.raises(MCPToolError, match="'T0'.*use 't0'"):
        _validate_pipeline_schema({"fields": [{"name": "T0", "path": "row.x", "aggregation": "first"}]})
