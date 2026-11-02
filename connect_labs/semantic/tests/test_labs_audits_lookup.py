"""A registry grades a per-worker photo approval rate from the `labs_audits` source.

The source yields one row per audited visit (see connect_labs/audit/audited_visit_rows.py),
stored through the real raw-cache write path into its own pipeline slot. A registry
LEFT-JOINs it onto its visit rows with a lookup `{on: visit_id, key: visit_id}`.

The per-worker figure must credit the worker who MADE the visit (took the photos).
Invented fixture, one opportunity:

    visit  worker  mother  photos (human)          entity worker (last_visit)
    v1     ada     m1      pass, pass               m1 -> bola (bola visited it last)
    v2     ada     m2      pass, fail               m2 -> ada
    v3     bola    m1      fail, fail
    v4     bola    m3      (not audited)            m3 -> bola
    v5     ada     m2      pending

    by visit maker:  ada 3/4 = 75%   bola 0/2 = 0%   programme 3/6 = 50%
    by mother's worker (an entity-grain measure in a mother registry):
                     ada 1/2 = 50%   bola 2/4 = 50%  -- v1's passes land on bola

`VISIT_PROPS` -- a registry whose entity IS the visit -- gives the first; the second
is what a ratio over mother rows gives, and is here to pin down why that is wrong.
Everything executes against Postgres.
"""

from __future__ import annotations

import datetime as dt

import pytest

from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig, DataSourceConfig
from connect_labs.labs.analysis.config import FieldComputation as F
from connect_labs.semantic.layer1 import LOOKUPS_KEY

OPP = 977301
AUDIT_PIPELINE = 5601
AS_OF = "DATE '2026-09-30'"
FUTURE = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)


def _int(x):
    return int(float(x)) if x else None


def _visits_config():
    config = AnalysisPipelineConfig(grouping_key="username", fields=[F(name="mother_case_id", path="form.mother")])
    config.pipeline_id = 22143
    return config


def _audits_config():
    """The pipeline a `{"data_source": {"type": "labs_audits"}, "fields": [...]}` schema
    resolves to (WORKFLOW_REFERENCE.md, "Labs audit results")."""
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[
            F(name="n_pass", path="audit.n_pass", transform=_int, aggregation="sum"),
            F(name="n_decided", path="audit.n_decided", transform=_int, aggregation="sum"),
            F(name="n_pending", path="audit.n_pending", transform=_int, aggregation="sum"),
        ],
        data_source=DataSourceConfig(type="labs_audits"),
    )
    config.pipeline_id = AUDIT_PIPELINE
    return config


LOOKUP = {
    "pipeline": "photo_audits",
    "on": "visit_id",
    "key": "visit_id",
    "pick": "latest",
    "fields": {"photos_pass": "n_pass", "photos_decided": "n_decided"},
}

MEASURES = [
    {
        "name": "photo_approval_rate",
        "title": "Photo approval rate",
        "type": "number",
        "sql": "100.0 * {photos_passed} / NULLIF({photos_decided}, 0)",
        "meta": {"indicator": "P01", "unit": "%", "direction": "higher"},
    },
    {"name": "photos_passed", "type": "sum", "sql": "{CUBE}.photos_pass_n"},
    {"name": "photos_decided", "type": "sum", "sql": "{CUBE}.photos_decided_n"},
]

AGGREGATES = [
    {"name": "first_visit", "sql": "MIN(visit_date)"},
    # A visit no session audited has no lookup row: NULL, so count it as nothing.
    {"name": "photos_pass_n", "sql": "COALESCE(SUM(photos_pass), 0)"},
    {"name": "photos_decided_n", "sql": "COALESCE(SUM(photos_decided), 0)"},
]

# The entity is the VISIT: one visitor per row, so the flw scope is the visit maker.
VISIT_PROPS = {
    "entity": {"name": "visit", "plural": "visits", "key": "visit_id", "cohort_date": "first_visit"},
    "pipelines": {"entity": "visits", "lookups": {"photo_audit": LOOKUP}},
    "aggregates": AGGREGATES,
    "properties": [{"name": "photo_audited", "type": "bool", "sql": "photos_decided_n > 0"}],
}

# The same lookup in a MOTHER registry, crediting each mother to her latest visitor.
MOTHER_PROPS = {
    "entity": {
        "name": "mother",
        "plural": "mothers",
        "key": "mother_case_id",
        "cohort_date": "first_visit",
        "worker": "last_visit",
    },
    "pipelines": {"entity": "visits", "lookups": {"photo_audit": LOOKUP}},
    "aggregates": AGGREGATES,
    "properties": [{"name": "photo_audited", "type": "bool", "sql": "photos_decided_n > 0"}],
}

INDICATORS = {
    "version": 1,
    "cube": "photo_review",
    "defaults": {"min_denominator": 1},
    "series": ["P"],
    "measures": MEASURES,
}


def _visit(visit_id, username, date, mother):
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    RawVisitCache.objects.create(
        opportunity_id=OPP,
        pipeline_id=USER_VISITS_RAW_SLOT,
        visit_count=1,
        expires_at=FUTURE,
        visit_id=visit_id,
        username=username,
        status="approved",
        visit_date=dt.date.fromisoformat(date),
        form_json={"form": {"mother": mother}},
    )


def _session(id, visit_results, visit_images, completed_at="2026-09-20T10:00:00Z"):
    from connect_labs.audit.models import AuditSessionRecord

    data = {"status": "completed", "completed_at": completed_at, "visit_results": visit_results}
    data["visit_images"] = visit_images
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": OPP, "data": data}
    )


def _img(blob, username):
    return {"blob_id": blob, "username": username, "visit_date": "2026-09-01T09:00:00"}


@pytest.fixture
def cache(db):
    from connect_labs.audit.audited_visit_rows import build_audited_visit_rows
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
    from connect_labs.labs.analysis.backends.sql.labs_audits_fetcher import normalize_audited_visit_row

    _visit("1", "ada", "2026-09-01", "m1")
    _visit("2", "ada", "2026-09-02", "m2")
    _visit("3", "bola", "2026-09-03", "m1")
    _visit("4", "bola", "2026-09-04", "m3")
    _visit("5", "ada", "2026-09-05", "m2")

    def a(result):
        return {"question_id": "q/photo", "result": result}

    session = _session(
        1,
        {
            "1": {"assessments": {"b1": a("pass"), "b2": a("pass")}},
            "2": {"assessments": {"b3": a("pass"), "b4": a("fail")}},
            "3": {"assessments": {"b5": a("fail"), "b6": a("duplicate_fake")}},
            "5": {"assessments": {"b7": a(None)}},
        },
        {
            "1": [_img("b1", "ada"), _img("b2", "ada")],
            "2": [_img("b3", "ada"), _img("b4", "ada")],
            "3": [_img("b5", "bola"), _img("b6", "bola")],
            "5": [_img("b7", "ada")],
        },
    )
    rows = [normalize_audited_visit_row(r, OPP) for r in build_audited_visit_rows([session])]
    # The real write path, into the labs_audits pipeline's own slot.
    SQLCacheManager(OPP, _audits_config()).store_raw_visits(rows, len(rows))
    return True


def _evaluate(props, scopes):
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate

    return evaluate(
        _visits_config(),
        [OPP],
        extra_fields={LOOKUPS_KEY: {"photo_audit": _audits_config()}},
        registry_documents=(props, INDICATORS),
        scopes=scopes,
        as_of=AS_OF,
        connection=connection,
    )


def _by_scope(rows):
    return {(r["scope"], r.get("username") if r["scope"] == "flw" else None): r for r in rows}


def test_both_registries_validate():
    from connect_labs.semantic.validation import validate_registry

    assert validate_registry(VISIT_PROPS, INDICATORS, {}) == []
    assert validate_registry(MOTHER_PROPS, INDICATORS, {}) == []


def test_a_visit_entity_registry_credits_the_visit_maker(cache):
    rows = _by_scope(_evaluate(VISIT_PROPS, ["programme", "flw"]))
    assert rows[("flw", "ada")]["photo_approval_rate"] == pytest.approx(75.0)
    assert rows[("flw", "ada")]["photos_decided"] == 4
    assert rows[("flw", "bola")]["photo_approval_rate"] == pytest.approx(0.0)
    assert rows[("flw", "bola")]["photos_decided"] == 2
    assert rows[("programme", None)]["photo_approval_rate"] == pytest.approx(50.0)


def test_unaudited_and_pending_only_visits_join_with_nothing_decided(cache):
    """v4 (never audited) and v5 (pending only) keep their rows and add nothing to the
    denominator; a lookup never multiplies a visit."""
    rows = _by_scope(_evaluate(VISIT_PROPS, ["programme"]))
    assert rows[("programme", None)]["photos_decided"] == 6
    assert rows[("programme", None)]["photos_passed"] == 3


def test_a_mother_registry_credits_the_mothers_worker_instead(cache):
    """Why the per-FLW figure cannot be an entity-grain ratio in a mother registry:
    v1 is ada's visit, but m1's latest visitor is bola, so ada's two passes count for bola."""
    rows = _by_scope(_evaluate(MOTHER_PROPS, ["programme", "flw"]))
    assert rows[("flw", "ada")]["photo_approval_rate"] == pytest.approx(50.0)
    assert rows[("flw", "bola")]["photo_approval_rate"] == pytest.approx(50.0)
    # The programme figure is the same either way: only the attribution moves.
    assert rows[("programme", None)]["photo_approval_rate"] == pytest.approx(50.0)


def test_an_opportunity_with_no_audits_yet_still_grades_with_a_blank_photo_rate(db):
    """No audits reviewed is the normal initial state: the lookup finds nothing, every
    visit keeps its row, and the photo approval rate is blank (NULL), not an error or 0."""
    _visit("1", "ada", "2026-09-01", "m1")
    _visit("2", "bola", "2026-09-02", "m2")
    rows = _by_scope(_evaluate(VISIT_PROPS, ["programme", "flw"]))
    assert rows[("programme", None)]["photo_approval_rate"] is None
    assert rows[("programme", None)]["photos_decided"] == 0
    assert rows[("flw", "ada")]["photo_approval_rate"] is None
    assert rows[("flw", "bola")]["photo_approval_rate"] is None
