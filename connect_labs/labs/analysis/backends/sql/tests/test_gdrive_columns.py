"""`data_source.columns` on a Google Drive source keeps only the listed cells per row.

A wide export (dozens of columns, 173k rows) read by pipelines that use a handful of
columns stored every cell of every row in the raw cache. These pin:

- the rows: listed columns only, in the listed order, `file` metadata kept; a listed
  column a file lacks reads null; omitted `columns` changes nothing;
- validation: a schema reading a `row.<col>` (or naming a username/date column) that
  `columns` does not list is refused at save and at read, naming what is missing;
- the cache: `columns` is part of the shared raw-slot key and of the config hash
  when set (order-insensitive), and absent from both when not -- so existing
  sources keep their slots and hashes;
- authorization: `columns` is not signed, so narrowing an authorized source needs no
  re-stamp, and it cannot reach anything outside the stamped target.
"""

import hashlib
import json
import logging

import pytest

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_fetcher import (  # noqa: F401 - fixtures
    OPP,
    PARTNER,
    STAFF,
    allowed_root,
    drive,
)
from connect_labs.labs.analysis.config import (
    GDRIVE_RAW_SLOT_FIELDS,
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
    gdrive_raw_slot,
    schema_gdrive_column_problems,
)
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.analysis.utils import get_config_hash

TARGET = {"type": "gdrive", "folder_id": "folderA", "file_pattern": "answers_scored_*.csv"}


def _source(**kw):
    return DataSourceConfig(**gf.authorize_gdrive_source({"type": "gdrive", **kw}, OPP, STAFF))


def _schema(columns, fields=None, **source):
    data_source = {**TARGET, **source}
    if columns is not None:
        data_source["columns"] = columns
    return {
        "data_source": data_source,
        "grouping_key": "username",
        "terminal_stage": "visit_level",
        "fields": fields if fields is not None else [{"name": "state", "path": "row.state"}],
    }


# --------------------------------------------------------------------------- the rows


def test_rows_keep_only_the_listed_columns_in_order(drive):  # noqa: F811
    rows = gf.fetch_gdrive_rows_as_visit_dicts(
        _source(file_id="fileCSV", columns=["state", "session_id"], username_column="session_id"), OPP
    )
    assert rows[0]["form_json"]["row"] == {"state": "Kebbi", "session_id": "s1"}
    assert list(rows[0]["form_json"]["row"]) == ["state", "session_id"]
    assert rows[0]["form_json"]["file"]["name"] == "answers_scored_bednets.csv"  # metadata stays
    assert [r["username"] for r in rows] == ["s1", "s2"]


def test_null_values_still_apply_to_kept_columns(drive):  # noqa: F811
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV", columns=["quality"]), OPP)
    assert rows[1]["form_json"]["row"] == {"quality": None}


def test_omitted_columns_keep_every_cell_exactly_as_before(drive):  # noqa: F811
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV"), OPP)
    assert rows[0]["form_json"]["row"] == {"session_id": "s1", "state": "Kebbi", "quality": "4", "day": "2026-08-03"}


def test_a_listed_column_a_file_lacks_reads_null_and_is_logged(drive, caplog):  # noqa: F811
    with caplog.at_level(logging.WARNING, logger=gf.logger.name):
        rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV", columns=["state", "qid"]), OPP)
    assert rows[0]["form_json"]["row"] == {"state": "Kebbi", "qid": None}
    assert any("answers_scored_bednets.csv" in m and "qid" in m for m in caplog.messages)


def test_json_rows_are_narrowed_too(drive):  # noqa: F811
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="json", columns=["tags"]), OPP)
    assert [r["form_json"]["row"] for r in rows] == [{"tags": ["x"]}, {"tags": {"k": 1}}]


def test_duplicate_columns_are_kept_once(drive):  # noqa: F811
    rows = gf.fetch_gdrive_rows_as_visit_dicts(_source(file_id="fileCSV", columns=["state", "state"]), OPP)
    assert rows[0]["form_json"]["row"] == {"state": "Kebbi"}


# --------------------------------------------------------------------------- validation


def test_a_schema_reading_an_unlisted_column_is_refused_naming_it():
    schema = _schema(
        ["state"],
        fields=[
            {"name": "state", "path": "row.state"},
            {"name": "qid", "paths": ["row.qid", "row.question_id"]},
            {"name": "ok", "path": "row.state", "filter_path": "row.status", "filter_value": "done"},
        ],
    )
    (problem,) = schema_gdrive_column_problems(schema)
    assert problem.startswith("data_source.columns does not list 3 column(s) the schema reads:")
    assert "'qid' (read by fields[1].paths 'row.qid')" in problem
    assert "'question_id'" in problem and "'status' (read by fields[2].filter_path 'row.status')" in problem
    assert "omit columns to keep every column" in problem


@pytest.mark.parametrize(
    "extra, missing",
    [
        ({"fields": [{"name": "x", "path": "row.state", "filter_paths": ["row.a"]}]}, "a"),
        (
            {
                "fields": [
                    {
                        "name": "x",
                        "path": "row.state",
                        "conditional_paths": [{"when_path": "row.form", "when_value": "v", "paths": ["row.b"]}],
                    }
                ]
            },
            "form",
        ),
        ({"fields": [{"name": "x", "path": "row.state", "pre_aggregate_by": "row.case"}]}, "case"),
        ({"linking_field": "row.session"}, "session"),
        (
            {"histograms": [{"name": "h", "path": "row.score", "lower_bound": 0, "upper_bound": 1, "num_bins": 2}]},
            "score",
        ),
        ({"data_source": {**TARGET, "columns": ["state"], "username_column": "participant"}}, "participant"),
        ({"data_source": {**TARGET, "columns": ["state"], "date_column": "day"}}, "day"),
        ({"filters": {"row.region": "x"}}, "region"),
    ],
)
def test_every_kind_of_reference_is_checked(extra, missing):
    schema = {**_schema(["state"]), **extra}
    problems = schema_gdrive_column_problems(schema)
    assert problems and f"{missing!r}" in problems[0]


def test_nested_paths_need_only_their_top_column():
    schema = _schema(["meta"], fields=[{"name": "x", "path": "row.meta.inner.leaf"}])
    assert schema_gdrive_column_problems(schema) == []


def test_non_row_paths_and_omitted_columns_are_not_checked():
    assert schema_gdrive_column_problems(_schema(None, fields=[{"name": "x", "path": "row.anything"}])) == []
    assert schema_gdrive_column_problems(_schema(["state"], fields=[{"name": "f", "path": "file.name"}])) == []


@pytest.mark.parametrize("columns", [[], ["ok", ""], "state", [1]])
def test_malformed_columns_are_refused(columns):
    (problem,) = schema_gdrive_column_problems(_schema(columns))
    assert "non-empty list of column names" in problem
    with pytest.raises(ValueError, match="non-empty list of column names"):
        DataSourceConfig(**{**TARGET, "columns": columns})


def test_columns_only_apply_to_drive():
    with pytest.raises(ValueError, match="only valid for type='gdrive'"):
        DataSourceConfig(type="connect_csv", columns=["a"])
    assert schema_gdrive_column_problems({"data_source": {"type": "connect_csv", "columns": ["a"]}}) == [
        "data_source.columns is only valid for type='gdrive'"
    ]


def test_a_read_of_a_schema_with_an_unlisted_column_is_refused():
    """Every read builds a config: a schema stored some other way cannot serve nulls."""
    with pytest.raises(ValueError, match="does not list 1 column"):
        AnalysisPipelineConfig(
            grouping_key="username",
            fields=[FieldComputation(name="qid", path="row.qid")],
            data_source=DataSourceConfig(**TARGET, columns=["state"]),
        )


def test_the_save_paths_refuse_it():
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools.pipelines import _validate_pipeline_schema

    bad = _schema(["state"], fields=[{"name": "qid", "path": "row.qid"}])
    with pytest.raises(MCPToolError) as e:
        _validate_pipeline_schema(bad)
    assert e.value.code == "INVALID_SCHEMA" and "'qid'" in e.value.message
    # The authorization step (every gdrive save path) refuses it before stamping.
    with pytest.raises(ValueError, match="does not list 1 column"):
        gf.authorize_schema_drive_source(bad, OPP, STAFF)
    _validate_pipeline_schema(_schema(["state", "qid"], fields=[{"name": "qid", "path": "row.qid"}]))


# --------------------------------------------------------------------------- the cache


def _old_slot(data_source):
    """gdrive_raw_slot as it was before `columns` existed."""
    key = {}
    for name in GDRIVE_RAW_SLOT_FIELDS:
        value = getattr(data_source, name, None)
        if name == "null_values":
            value = sorted({str(v) for v in (value if value is not None else [""])})
        else:
            value = value or ""
        key[name] = value
    digest = hashlib.blake2b(json.dumps(key, sort_keys=True).encode(), digest_size=8).digest()
    return -2 - (int.from_bytes(digest, "big") % (2**31 - 3))


@pytest.mark.parametrize(
    "source, slot",
    [
        (TARGET, -855336250),
        ({**TARGET, "null_values": ["", "NA"], "username_column": "u", "date_column": "d"}, -396424769),
    ],
)
def test_sources_without_columns_keep_their_slot(source, slot):
    # Literals computed with the pre-change gdrive_raw_slot: existing raw copies stay put.
    assert gdrive_raw_slot(DataSourceConfig(**source)) == _old_slot(DataSourceConfig(**source)) == slot


def test_columns_change_the_slot_order_insensitively():
    plain = gdrive_raw_slot(DataSourceConfig(**TARGET))
    ab = gdrive_raw_slot(DataSourceConfig(**TARGET, columns=["a", "b"]))
    assert ab != plain
    assert ab == gdrive_raw_slot(DataSourceConfig(**TARGET, columns=["b", "a", "a"]))
    assert ab != gdrive_raw_slot(DataSourceConfig(**TARGET, columns=["a"]))


def _config(pipeline_id=1, columns=None, field_name="state"):
    data_source = (
        DataSourceConfig(**gf.authorize_gdrive_source(TARGET, OPP, STAFF, pipeline_id=pipeline_id))
        if pipeline_id
        else DataSourceConfig(**TARGET)
    )
    data_source.columns = columns
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=field_name, path="row.state", aggregation="first")],
        terminal_stage=CacheStage.VISIT_LEVEL,
        data_source=data_source,
        pipeline_id=pipeline_id,
    )


def test_columns_join_the_config_hash_only_when_set():
    plain = get_config_hash(_config(None))
    assert get_config_hash(_config(None, columns=["state"])) != plain
    assert get_config_hash(_config(None, columns=["state", "day"])) == get_config_hash(
        _config(None, columns=["day", "state"])
    )
    # Pinned: the hash this column-less Drive config had before `columns` existed
    # (computed with the pre-change get_config_hash), so no existing cache is orphaned.
    assert plain == "41ec042bbfba"


def _run(config):
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP))
    assert events[-1][0] == "result", events[-1]
    return events


def _downloads(drive):  # noqa: F811
    return [c for c in drive.calls if c[0] == "download"]


@pytest.mark.django_db
def test_pipelines_share_a_raw_copy_only_when_their_columns_match(drive):  # noqa: F811
    _run(_config(101, columns=["state", "session_id"]))
    assert len(_downloads(drive)) == 2
    stored = RawVisitCache.objects.filter(opportunity_id=OPP)
    assert stored.count() == 3
    assert all(set(r.form_json["row"]) == {"state", "session_id"} for r in stored)

    # Same columns, other order: shares the copy, reads no Drive.
    events = _run(_config(102, columns=["session_id", "state"], field_name="s2"))
    assert len(_downloads(drive)) == 2
    assert any("Reusing 3 rows" in (e[1].get("message") or "") for e in events if e[0] == "status")

    # No columns (every cell): its own copy -- the narrowed one cannot serve it.
    _run(_config(103, field_name="s3"))
    assert len(_downloads(drive)) == 4
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 6
    assert len(set(RawVisitCache.objects.values_list("pipeline_id", flat=True))) == 2


# --------------------------------------------------------------------------- authorization


def test_narrowing_an_authorized_source_needs_no_restamp(drive):  # noqa: F811
    stored = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)
    narrowed = {"data_source": {**stored, "columns": ["state"]}, "fields": [{"name": "s", "path": "row.state"}]}
    # A non-staff editor may narrow: the stamp still verifies, nothing is re-stamped.
    assert (
        gf.authorize_schema_drive_source(narrowed, OPP, PARTNER, previous_schema={"data_source": stored}) is narrowed
    )
    rows = gf.fetch_gdrive_rows_as_visit_dicts(DataSourceConfig(**narrowed["data_source"]), OPP)
    assert rows[0]["form_json"]["row"] == {"state": "Kebbi"}


def test_columns_cannot_reach_outside_the_stamped_target(drive):  # noqa: F811
    """Columns select cells of the stamped file's own rows; retargeting is still refused."""
    stored = gf.authorize_gdrive_source({"type": "gdrive", "file_id": "fileCSV"}, OPP, STAFF)
    with pytest.raises(gf.GDriveSourceError, match="changed after it was authorized"):
        gf.fetch_gdrive_rows_as_visit_dicts(
            DataSourceConfig(**{**stored, "file_id": "fileOther", "columns": ["quote"]}), OPP
        )
    rows = gf.fetch_gdrive_rows_as_visit_dicts(DataSourceConfig(**{**stored, "columns": ["quote", "file"]}), OPP)
    assert rows[0]["form_json"]["row"] == {"quote": None, "file": None}  # not the other file, not metadata
    assert {c[1] for c in drive.calls if c[0] == "download"} == {"fileCSV"}
