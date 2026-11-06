"""A visit extraction reads each row's form_json once, however many fields it has.

form_json is stored out of line (TOAST), and Postgres re-fetches an out-of-line
value for every reference in a SELECT list. Measured on prod, an 8-field pipeline
did ~9x the buffer reads and ~2x the time of the same read over a once-fetched
copy. These tests pin the cost (buffer hits), not just the rows: the rows are
identical before and after by design.
"""

import json
import secrets

import pytest
from django.db import connection
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql import query_builder
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.backends.sql.query_builder import (
    build_multi_opportunity_visit_extraction,
    build_visit_extraction_query,
)
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation

FIELDS = [f"f{i}" for i in range(8)]


def _config(opp_id, fields=FIELDS):
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=f, path=f"form.{f}", aggregation="first") for f in fields],
        histograms=[],
        filters={},
        experiment="test",
    )
    config.pipeline_id = opp_id
    return config


def _seed(opp_id, n=20):
    slot = _config(opp_id).raw_slot_id
    for i in range(n):
        form = {f: f"{f}-{i}" for f in FIELDS}
        # Incompressible padding, so the value is stored out of line like a real visit.
        form["padding"] = secrets.token_hex(20_000)
        RawVisitCache.objects.create(
            opportunity_id=opp_id,
            pipeline_id=slot,
            visit_count=n,
            expires_at=timezone.now() + timezone.timedelta(days=1),
            visit_id=f"v{i}",
            username="flw_a",
            visit_date="2026-08-01",
            status="approved",
            form_json={"form": form},
        )


def _rows(sql):
    with connection.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _shared_hits(sql):
    with connection.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
        plan = cur.fetchone()[0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    return plan[0]["Plan"]["Shared Hit Blocks"] + plan[0]["Plan"].get("Shared Read Blocks", 0)


def _plain(monkeypatch):
    """Build the pre-change query shape: form_json referenced straight off the table."""
    monkeypatch.setattr(query_builder, "_raw_visit_source", lambda select, where: ("labs_raw_visit_cache", where))


class TestSqlShape:
    def test_two_or_more_references_read_through_the_fence(self):
        sql, _ = build_visit_extraction_query(_config(40001), opportunity_id=40001)
        assert "form_json || '{}'::jsonb AS form_json" in sql
        assert "OFFSET 0" in sql

    def test_a_single_reference_keeps_the_plain_table(self):
        """One reference gains nothing from the fence, and a read that never
        names form_json must not start fetching it."""
        sql, _ = build_visit_extraction_query(_config(40002, fields=["f0"]), opportunity_id=40002)
        assert "OFFSET 0" not in sql


@pytest.mark.django_db
class TestAgainstPostgres:
    def test_rows_are_identical_to_the_plain_read(self, monkeypatch):
        _seed(40010)
        sql, _ = build_visit_extraction_query(_config(40010), opportunity_id=40010)
        fenced = _rows(sql)
        _plain(monkeypatch)
        plain_sql, _ = build_visit_extraction_query(_config(40010), opportunity_id=40010)
        assert "OFFSET 0" not in plain_sql
        assert fenced == _rows(plain_sql)
        assert len(fenced) == 20
        assert {r["visit_id"]: r["f5"] for r in fenced}["v3"] == "f5-3"

    def test_the_value_is_fetched_once_not_once_per_field(self, monkeypatch):
        _seed(40020)
        sql, _ = build_visit_extraction_query(_config(40020), opportunity_id=40020)
        fenced = _shared_hits(sql)
        _plain(monkeypatch)
        plain_sql, _ = build_visit_extraction_query(_config(40020), opportunity_id=40020)
        plain = _shared_hits(plain_sql)
        # Eight references re-fetch the TOAST chunks eight times; once-read fetches them once.
        assert fenced * 3 < plain, (fenced, plain)

    def test_multi_opportunity_extraction_matches_the_plain_read(self, monkeypatch):
        _seed(40030)
        _seed(40031)
        cfg = _config(40030)
        sql = build_multi_opportunity_visit_extraction(cfg, [40030, 40031])
        assert "OFFSET 0" in sql
        fenced = _rows(sql)
        _plain(monkeypatch)
        plain = _rows(build_multi_opportunity_visit_extraction(cfg, [40030, 40031]))
        assert fenced == plain
        assert len(fenced) == 40
