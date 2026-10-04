"""Entity stage: composite `group_by` keys and several named `groupings` in one pass.

The motivating case is a program dashboard over one Drive folder (workflow 23891)
that needed totals by questionnaire, by question, by state, by FLW type, and answer
types by question x state -- nine pipelines, each a separate pass over the same
173,488 rows. These tests drive the real pipeline path (stream_analysis -> SQL) with
rows shaped the way the Drive fetcher shapes them, and pin that:

* a composite key's rows equal a hand count;
* every named grouping equals the same grouping run as its own single-key pipeline
  (per-grouping filters, filtered count / first fields included);
* the JSON extraction runs ONCE however many groupings there are;
* a pipeline declaring neither is untouched.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql.query_builder import (
    build_entity_aggregation_query,
    build_grouped_aggregation_plan,
    execute_grouped_aggregation,
    generate_sql_preview,
)
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
    GroupingSpec,
    HistogramComputation,
    schema_grouping_problems,
)
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.analysis.utils import get_config_hash

ANSWERS = (
    b"qid,topic,state,flw_type,classification_basis,typology_id,typology_name,session_id,score\n"
    b"4.12,payment,Kebbi,chw,answered_clean,T1,Workload,s1,1\n"
    b"4.12,payment,Kebbi,chw,answered_clean,T2,System & Resource Gaps,s2,2\n"
    b"4.12,payment,Kebbi,vol,answered_clean,T1,Workload,s3,5\n"
    b"4.12,payment,Kebbi,vol,unclear,T3,Training,s3,3\n"
    b"4.12,payment,Borno,chw,answered_clean,T2,System Gaps,s4,4\n"
    b"4.12,payment,Borno,chw,answered_clean,T4,Payment,s4,2\n"
    b"5.01,training,Kebbi,chw,answered_clean,T3,Training,s1,5\n"
    b"5.01,training,Borno,vol,answered_low_quality,T3,Training,s5,1\n"
    b"5.02,training,,chw,answered_clean,T1,Workload,s6,3\n"
)

FIELDS = [
    FieldComputation(name="qid", path="row.qid", aggregation="first"),
    FieldComputation(name="topic", path="row.topic", aggregation="first"),
    FieldComputation(name="state", path="row.state", aggregation="first"),
    FieldComputation(name="flw_type", path="row.flw_type", aggregation="first"),
    FieldComputation(name="basis", path="row.classification_basis", aggregation="first"),
    FieldComputation(name="typology_id", path="row.typology_id", aggregation="first"),
    FieldComputation(name="typology_name", path="row.typology_name", aggregation="first"),
    FieldComputation(name="n", path="row.qid", aggregation="count"),
    FieldComputation(
        name="clean",
        path="row.qid",
        aggregation="count",
        filter_path="row.classification_basis",
        filter_value="answered_clean",
    ),
    FieldComputation(
        name="t3_name", path="row.typology_name", aggregation="first", filter_path="row.typology_id", filter_value="T3"
    ),
    FieldComputation(name="interviews", path="row.session_id", aggregation="count_distinct"),
    FieldComputation(name="sessions", path="row.session_id", aggregation="list"),
]

GROUPINGS = [
    GroupingSpec(name="by_topic", group_by=["topic"]),
    GroupingSpec(name="by_question", group_by=["qid"]),
    GroupingSpec(name="by_state", group_by=["state"]),
    GroupingSpec(name="by_flw_type", group_by=["flw_type"], fields=["n", "clean", "interviews"]),
    GroupingSpec(
        name="types",
        group_by=["qid", "typology_id", "state"],
        filters={"basis": ["answered_clean"]},
        fields=["n", "typology_name"],
    ),
]


@pytest.fixture
def answers(drive):  # noqa: F811
    drive.files["answers"] = {
        "name": "answers_scored_x.csv",
        "mimeType": "text/csv",
        "parent": "folderA",
        "content": ANSWERS,
    }
    return drive


def _config(
    pipeline_id, *, fields=FIELDS, filters=None, linking_field="qid", group_by=None, groupings=None, histograms=None
):
    stamped = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "answers"}, OPP, STAFF, pipeline_id=pipeline_id)
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=list(fields),
        terminal_stage=CacheStage.ENTITY,
        linking_field=linking_field,
        filters=filters or {},
        data_source=DataSourceConfig(**stamped),
        pipeline_id=pipeline_id,
        group_by=group_by or [],
        groupings=groupings or [],
        histograms=histograms or [],
    )


def _result_rows(config):
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP))
    assert events[-1][0] == "result", events[-1]
    return events[-1][1].rows


def _rows(config):
    return {r.entity_id: r for r in _result_rows(config)}


@pytest.mark.django_db
def test_a_composite_key_groups_by_every_key_and_carries_each_as_a_column(answers):
    config = _config(
        401,
        group_by=["qid", "typology_id", "state"],
        filters={"basis": ["answered_clean"]},
    )
    rows = _rows(config)
    # Hand count over the clean rows:
    #   4.12 T1 Kebbi x2, 4.12 T2 Kebbi, 4.12 T2 Borno, 4.12 T4 Borno, 5.01 T3 Kebbi, 5.02 T1 <no state>
    assert {k: r.total_visits for k, r in rows.items()} == {
        "4.12|T1|Kebbi": 2,
        "4.12|T2|Kebbi": 1,
        "4.12|T2|Borno": 1,
        "4.12|T4|Borno": 1,
        "5.01|T3|Kebbi": 1,
        "5.02|T1|": 1,
    }
    row = rows["4.12|T1|Kebbi"].custom_fields
    assert (row["qid"], row["typology_id"], row["state"]) == ("4.12", "T1", "Kebbi")
    assert row["n"] == 2
    assert row["interviews"] == 2  # s1, s3
    assert row["typology_name"] == "Workload"
    assert "grouping" not in row  # an unnamed group_by carries no tag
    assert rows["5.02|T1|"].custom_fields["state"] is None


@pytest.mark.django_db
def test_one_unnamed_key_is_exactly_the_linking_field_aggregation(answers):
    linked = _rows(_config(402, linking_field="state"))
    grouped = _rows(_config(403, group_by=["state"]))
    assert linked.keys() == grouped.keys()
    for key, row in linked.items():
        assert grouped[key].to_dict() == row.to_dict()


def _single_pipeline_equivalent(spec: GroupingSpec, pipeline_id: int):
    """The same grouping run as its own pipeline, the way it had to be done before."""
    # (a filter only applies to a field the pipeline declares, so keep those too)
    keep = set(spec.fields or []) | set(spec.group_by) | set(spec.filters)
    fields = [f for f in FIELDS if spec.fields is None or f.name in keep]
    if len(spec.group_by) == 1:
        return _config(pipeline_id, fields=fields, filters=spec.filters, linking_field=spec.group_by[0])
    return _config(pipeline_id, fields=fields, filters=spec.filters, group_by=spec.group_by)


@pytest.mark.django_db
def test_every_named_grouping_equals_the_same_grouping_run_as_its_own_pipeline(answers):
    combined = _result_rows(_config(410, groupings=GROUPINGS))
    by_grouping: dict[str, list] = {}
    for row in combined:
        by_grouping.setdefault(row.custom_fields["grouping"], []).append(row)
    assert list(by_grouping) == [g.name for g in GROUPINGS]  # declaration order

    for i, spec in enumerate(GROUPINGS):
        alone = _rows(_single_pipeline_equivalent(spec, 420 + i))
        mine = {r.entity_id.split(":", 1)[1]: r for r in by_grouping[spec.name]}
        assert mine.keys() == alone.keys(), spec.name
        for key, expected in alone.items():
            got = mine[key].to_dict()
            assert got.pop("grouping") == spec.name
            assert got.pop("entity_id") == f"{spec.name}:{key}"
            want = expected.to_dict()
            want.pop("entity_id")
            if spec.fields is not None:
                # A restricted grouping carries its keys and its own fields, nothing else.
                assert set(got) - set(want) == set(), spec.name
            assert got == {k: v for k, v in want.items() if k in got}, (spec.name, key)
            assert set(want) - set(got) <= set(spec.filters)


@pytest.mark.django_db
def test_the_types_grouping_counts_clean_answers_per_question_type_and_state(answers):
    rows = _rows(_config(411, groupings=GROUPINGS))
    t = rows["types:4.12|T1|Kebbi"].custom_fields
    assert (t["qid"], t["typology_id"], t["state"], t["n"], t["typology_name"]) == (
        "4.12",
        "T1",
        "Kebbi",
        2,
        "Workload",
    )
    assert set(t) == {"grouping", "qid", "typology_id", "state", "n", "typology_name"}
    # the `unclear` T3 row in Kebbi is outside the grouping's filter
    assert "types:4.12|T3|Kebbi" not in rows
    # ...but every other grouping still sees it: per-grouping filters are per grouping
    assert rows["by_question:4.12"].total_visits == 6
    assert rows["by_question:4.12"].custom_fields["clean"] == 5
    assert rows["by_question:4.12"].custom_fields["t3_name"] == "Training"
    assert rows["by_state:Kebbi"].custom_fields["interviews"] == 3
    assert sorted(rows["by_topic:training"].custom_fields["sessions"]) == ["s1", "s5", "s6"]
    assert set(rows["by_flw_type:chw"].custom_fields) == {"grouping", "flw_type", "n", "clean", "interviews"}


SCORES = HistogramComputation(
    name="score",
    path="row.score",
    lower_bound=1,
    upper_bound=5,
    num_bins=2,
    bin_name_prefix="score",
    transform=lambda x: float(x) if x else None,
)


@pytest.mark.django_db
def test_a_histogram_in_a_grouping_equals_the_same_histogram_run_alone(answers):
    alone = _rows(_config(430, linking_field="state", histograms=[SCORES]))
    grouped = _rows(
        _config(
            431, histograms=[SCORES], groupings=[GroupingSpec(name="s", group_by=["state"], fields=["score", "n"])]
        )
    )
    for key, row in alone.items():
        mine = grouped[f"s:{key}"].custom_fields
        for name in ("score_1_0_3_0_visits", "score_3_0_5_0_visits", "score_mean", "score_count", "n"):
            assert mine[name] == row.custom_fields[name], (key, name)
    assert grouped["s:Kebbi"].custom_fields["score_count"] == 5


@pytest.mark.django_db
def test_the_pipeline_filters_apply_to_every_grouping(answers):
    rows = _rows(_config(412, groupings=GROUPINGS, filters={"state": ["Borno"]}))
    assert rows["by_question:4.12"].total_visits == 2
    assert {k for k in rows if k.startswith("by_state:")} == {"by_state:Borno"}


@pytest.mark.django_db
def test_the_extraction_runs_once_for_every_grouping(answers):
    config = _config(413, groupings=GROUPINGS)
    _result_rows(config)  # fills the raw cache
    with CaptureQueriesContext(connection) as ctx:
        results = execute_grouped_aggregation(config, OPP)
    assert len(results) == len(GROUPINGS)
    reads = [q["sql"] for q in ctx.captured_queries if "labs_raw_visit_cache" in q["sql"]]
    assert len(reads) == 1, reads
    assert reads[0].lstrip().startswith("CREATE TEMP TABLE")
    extracting = [q["sql"] for q in ctx.captured_queries if "form_json" in q["sql"]]
    assert extracting == reads  # no grouping re-walks the JSON


@pytest.mark.django_db
def test_grouped_rows_are_cached_and_read_back_like_entity_rows(answers):
    config = _config(414, groupings=GROUPINGS)
    first = {r.entity_id: r.to_dict() for r in _result_rows(config)}
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP))
    again = events[-1][1]
    assert {r.entity_id: r.to_dict() for r in again.rows} == first


@pytest.mark.django_db
def test_query_pipeline_rows_filters_by_grouping(answers):
    from connect_labs.workflow.pipeline_query import cached_queryset, parse_query, run_query

    config = _config(415, groupings=GROUPINGS)
    _result_rows(config)
    qs = cached_queryset(config, OPP)
    assert qs is not None
    result = run_query(config, OPP, parse_query({"filters": {"grouping": "by_state"}, "order_by": "state"}), qs)
    assert result["total"] == 3  # Borno, Kebbi, <none>
    assert sorted(r["state"] or "" for r in result["rows"]) == ["", "Borno", "Kebbi"]
    assert {r["grouping"] for r in result["rows"]} == {"by_state"}
    types = run_query(
        config, OPP, parse_query({"filters": {"grouping": "types", "state": "Borno"}}), cached_queryset(config, OPP)
    )
    assert types["total"] == 2


def test_the_sql_preview_shows_one_extraction_and_each_grouping():
    config = AnalysisPipelineConfig(
        grouping_key="username", fields=list(FIELDS), terminal_stage=CacheStage.ENTITY, groupings=list(GROUPINGS)
    )
    preview = generate_sql_preview(config, 7)
    script = preview["grouped_aggregation_sql"]
    assert script.count("CREATE TEMP TABLE") == 1
    assert script.count("form_json") > 0
    plan = build_grouped_aggregation_plan(config, 7)
    assert all("form_json" not in q.sql for q in plan.queries)
    assert [g["name"] for g in preview["groupings"]] == [g.name for g in GROUPINGS]


def test_a_pipeline_without_groupings_is_untouched():
    plain = AnalysisPipelineConfig(
        grouping_key="username", fields=list(FIELDS), terminal_stage=CacheStage.ENTITY, linking_field="qid"
    )
    assert plain.effective_groupings() == []
    assert "grouped_aggregation_sql" not in generate_sql_preview(plain, 7)
    # The cache key of every existing pipeline is unchanged (pinned on the pre-groupings code).
    assert get_config_hash(plain) == PLAIN_HASH
    # ...and the entity SQL is the linking_field GROUP BY it always was.
    assert (
        "GROUP BY (COALESCE(NULLIF(form_json->'row'->>'qid', ''))), opportunity_id"
        in build_entity_aggregation_query(plain, 7)
    )
    grouped = AnalysisPipelineConfig(
        grouping_key="username", fields=list(FIELDS), terminal_stage=CacheStage.ENTITY, group_by=["qid"]
    )
    assert get_config_hash(grouped) != get_config_hash(plain)


# get_config_hash of `plain` above, computed with origin/main before groupings existed.
PLAIN_HASH = "ab04b7ee9f3b"


@pytest.mark.parametrize(
    "schema, needle",
    [
        ({"group_by": ["nope"]}, "unknown group_by field 'nope'"),
        ({"groupings": {"ByState": {"group_by": ["state"]}}}, "must be lower-case"),
        ({"groupings": {"s": {"group_by": ["state"], "fields": ["ghost"]}}}, "unknown field 'ghost'"),
        ({"groupings": {"s": {"group_by": ["state"], "filters": {"ghost": "x"}}}}, "unknown key 'ghost'"),
        ({"groupings": {"s": {"group_by": []}}}, "at least one field"),
        ({"groupings": {"s": {"group_by": ["state"], "having": 1}}}, "unknown keys"),
        ({"group_by": ["state"], "groupings": {"s": {"group_by": ["state"]}}}, "not both"),
        ({"group_by": ["state"], "terminal_stage": "aggregated"}, "terminal_stage 'entity'"),
        ({"groupings": {"s": {"group_by": ["state"]}}, "extra_field": "mode_share"}, "only works per FLW"),
        ({"groupings": {"s": {"group_by": ["qid", "entity_id"]}}}, "sole key"),
        ({"groupings": {"s": {"group_by": ["form_json"]}}}, "JSON column"),
    ],
)
def test_schema_validation_refuses_what_cannot_run(schema, needle):
    fields = [{"name": "state", "path": "row.state"}, {"name": "qid", "path": "row.qid"}]
    if schema.pop("extra_field", None):
        fields.append({"name": "conc", "path": "row.x", "aggregation": "mode_share"})
    problems = schema_grouping_problems({"terminal_stage": "entity", "fields": fields, **schema})
    assert any(needle in p for p in problems), problems


def test_schema_validation_accepts_the_documented_example():
    schema = {
        "fields": [
            {"name": n, "path": f"row.{n}"}
            for n in ("topic", "qid", "state", "flw_type", "typology_id", "typology_name")
        ]
        + [
            {"name": "basis", "path": "row.classification_basis"},
            {"name": "n", "path": "row.qid", "aggregation": "count"},
        ],
        "groupings": {
            "by_topic": {"group_by": ["topic"]},
            "by_question": {"group_by": ["qid"]},
            "by_state": {"group_by": ["state"]},
            "by_flw_type": {"group_by": ["flw_type"]},
            "types": {
                "group_by": ["qid", "typology_id", "state"],
                "filters": {"basis": ["answered_clean"]},
                "fields": ["n", "typology_name"],
            },
        },
    }
    assert schema_grouping_problems(schema) == []


def test_mcp_validation_refuses_a_bad_grouping():
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools.pipelines import _validate_pipeline_schema

    with pytest.raises(MCPToolError) as e:
        _validate_pipeline_schema({"fields": [{"name": "qid", "path": "row.qid"}], "group_by": ["state"]})
    assert "unknown group_by field 'state'" in str(e.value)


def test_a_schema_with_groupings_and_no_terminal_stage_is_entity_stage():
    from connect_labs.workflow.data_access import PipelineDataAccess

    access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
    config = access._schema_to_config(
        {
            "fields": [{"name": "qid", "path": "row.qid"}, {"name": "state", "path": "row.state"}],
            "groupings": {"by_state": {"group_by": ["state"]}, "pairs": {"group_by": ["qid", "state"]}},
        },
        definition_id=99101,
    )
    assert config.terminal_stage == CacheStage.ENTITY
    assert [g.name for g in config.groupings] == ["by_state", "pairs"]
    assert config.groupings[1].group_by == ["qid", "state"]
