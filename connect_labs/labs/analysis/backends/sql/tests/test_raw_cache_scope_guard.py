"""Every generated read of labs_raw_visit_cache is scoped to a raw-cache slot.

The raw cache holds, per opportunity, the slot every visits pipeline shares (#1921)
and one slot per pipeline on any other source -- CommCare HQ forms, OCS sessions,
work areas (#116). A read without a `pipeline_id` condition sees them all, so an
HQ registration form arrives as a visit. Nothing about the table stops that: the
slot is a number, and remembering to filter on it is every reader's job.

It was forgotten three times in one reader -- the semantic layer, which rewrote
the engine's SQL as text and lost the scope along with a status filter and the
in-progress-download guard. That reader now asks the engine instead. This test
is the backstop for the next one: it parses what each builder EMITS, over the
pipeline shapes that exist (plain, pre-aggregated, joined, windowed, HQ-sourced),
and fails if any raw-cache read sits in a SELECT with no slot condition.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from connect_labs.labs.analysis.backends.sql import query_builder as qb
from connect_labs.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
    JoinConfig,
    WindowFieldComputation,
)

RAW = "labs_raw_visit_cache"


def _unscoped_reads(sql: str) -> list[str]:
    """The raw-cache reads whose enclosing SELECT has no `pipeline_id` condition."""
    problems = []
    for statement in sqlglot.parse(sql, read="postgres"):
        if statement is None:
            continue
        for table in statement.find_all(exp.Table):
            if table.name != RAW:
                continue
            select = table.find_ancestor(exp.Select)
            where = select.args.get("where") if select else None
            scoped = where is not None and any(c.name == "pipeline_id" for c in where.find_all(exp.Column))
            if not scoped:
                problems.append((select or table).sql(dialect="postgres")[:300])
    return problems


def _config(pipeline_id=5108, source="connect_csv", **kw):
    config = AnalysisPipelineConfig(
        grouping_key="username",
        data_source=DataSourceConfig(type=source, form_name="Register Mother" if source == "cchq_forms" else ""),
        **kw,
    )
    config.pipeline_id = pipeline_id
    return config


def _shapes():
    plain = [
        FieldComputation(name="mother_case_id", path="form.case.@case_id"),
        FieldComputation(name="n", path="form.@name", aggregation="count"),
        FieldComputation(name="mothers", path="form.case.@case_id", aggregation="count_distinct"),
    ]
    yield "plain", _config(fields=list(plain))
    yield "hq forms", _config(pipeline_id=5501, source="cchq_forms", fields=list(plain))
    yield "status filter", _config(fields=list(plain), filters={"status": ["approved"]})
    yield "pre-aggregated", _config(
        fields=[
            FieldComputation(
                name="per_mother_visits",
                path="form.@name",
                aggregation="avg",
                pre_aggregate_by="form.case.@case_id",
            ),
            *plain,
        ]
    )
    joined = _config(fields=list(plain))
    joined.joins = [
        JoinConfig(
            from_alias="registrations",
            local_key="form.case.@case_id",
            remote_key_field="mother_case_id",
            fields=[{"name": "eligible", "from": "eligible"}],
            resolved_config_hash="abc123",
        )
    ]
    yield "joined", joined
    windowed = _config(
        fields=[
            FieldComputation(name="mother_case_id", path="form.case.@case_id"),
            FieldComputation(name="lat", path="form.lat"),
            FieldComputation(name="lon", path="form.lon"),
        ],
        window_fields=[
            WindowFieldComputation(
                name="metres",
                operation="lag_haversine",
                partition_by="mother_case_id",
                order_by="visit_date",
                lat_field="lat",
                lon_field="lon",
            )
        ],
        extracted_filters=[{"field": "lat", "op": "is_not_null"}],
    )
    yield "windowed", windowed
    entity = _config(fields=list(plain), terminal_stage=CacheStage.ENTITY, linking_field="mother_case_id")
    yield "entity", entity


SHAPES = list(_shapes())


@pytest.mark.parametrize("name,config", SHAPES, ids=[n for n, _ in SHAPES])
def test_every_engine_builder_scopes_its_raw_reads(name, config):
    built = {"visit extraction": qb.build_visit_extraction_query(config, 10042)[0]}
    built["flw aggregation"] = qb.build_flw_aggregation_query(config, 10042)
    if config.terminal_stage == CacheStage.ENTITY:
        built["entity aggregation"] = qb.build_entity_aggregation_query(config, 10042)
    if not config.joins:
        built["multi-opportunity"] = qb.build_multi_opportunity_visit_extraction(config, [10042, 10016])
    for builder, sql in built.items():
        assert not _unscoped_reads(sql), f"{name} / {builder}: a raw-cache read with no slot condition"


def test_layer1_scopes_every_raw_read_including_its_lookups():
    from connect_labs.semantic.layer1 import LOOKUPS_KEY, build_visit_sql
    from connect_labs.semantic.tests.test_layer1_lookups_windows import (
        PROPS,
        _gs_config,
        _registrations_config,
        _visits_config,
    )

    sql = build_visit_sql(
        _visits_config(),
        [10042, 10016],
        extra_fields={LOOKUPS_KEY: {"registration": _registrations_config(), "gs": _gs_config()}},
        props_doc=PROPS,
    )
    assert sql.count(RAW) == 3, "the visits and both lookups"
    assert not _unscoped_reads(sql)


def test_the_guard_catches_an_unscoped_read():
    """So a green run means scoped, not that the parser saw nothing."""
    assert _unscoped_reads("SELECT visit_id FROM labs_raw_visit_cache WHERE opportunity_id IN (1)")
    assert not _unscoped_reads(
        "SELECT visit_id FROM labs_raw_visit_cache WHERE opportunity_id IN (1) AND pipeline_id = -1"
    )


# Files allowed to write raw-cache SQL by hand. Readers of visit DATA go through the
# engine (query_builder); the admin views read every slot on purpose -- they report
# on the cache itself. A new file here should be a decision, not an accident.
RAW_SQL_FILES = {
    "connect_labs/labs/analysis/backends/sql/query_builder.py",
    "connect_labs/labs/admin/views.py",
    "connect_labs/labs/admin/sql_validator.py",
}


def test_only_the_engine_and_the_cache_admin_write_raw_cache_sql():
    root = Path(__file__).resolve().parents[6]
    out = subprocess.run(
        ["git", "grep", "-l", "-E", rf"(FROM|JOIN)\s+{RAW}", "--", "connect_labs/*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    offenders = sorted(f for f in out if "/tests/" not in f and "/migrations/" not in f and f not in RAW_SQL_FILES)
    assert not offenders, f"raw-cache SQL outside the engine: {offenders} -- build it with query_builder instead"
