"""Case fields in a de-identified export: a registry's aggregates and properties, one row
per case, each at least as strict as the form fields it is computed from.

The case statement here is the REAL one: the on-disk KMC registry compiled over the
semantic suite's own pipeline schemas and cached visits (``semantic/tests/test_end_to_end``),
through ``cases.compile_sql`` with only the Connect reads (workflow, registry record,
pipeline records) stood in for. THIS REPOSITORY IS PUBLIC: every value is invented.
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from connect_labs.labs import canopy_panel
from connect_labs.labs.access import scopes
from connect_labs.labsdata import cases
from connect_labs.labsdata import dictionary as dictionary_module
from connect_labs.labsdata import exports, lineage, scope, tasks
from connect_labs.labsdata.models import SpaceExport
from connect_labs.labsdata.scope import Opportunity
from connect_labs.semantic.tests.test_end_to_end import (  # noqa: F401 -- visit_cache is a fixture
    ENTITY_PIPELINE,
    ENTITY_SCHEMA,
    OPP,
    VISIT_PIPELINE,
    VISIT_SCHEMA,
    _config,
    visit_cache,
)

pytestmark = pytest.mark.django_db

OPP_ROW = Opportunity(id=OPP, name="Opp Cases", llo="LLO C")


@pytest.fixture
def people():
    User = get_user_model()
    return {n: User.objects.create_user(username=f"case-{n}", password="x") for n in ("ann", "ben")}


@pytest.fixture(autouse=True)
def connect(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)

    def holdings(caller):
        return scopes.Holdings(org_slugs=frozenset(), opportunity_ids=frozenset({OPP}), program_ids=frozenset())

    monkeypatch.setattr(scopes, "holdings", holdings)
    monkeypatch.setattr(scope, "directory", lambda caller: {OPP: OPP_ROW})
    monkeypatch.setattr(canopy_panel, "panel", lambda *a, **k: None)
    monkeypatch.setattr(tasks.build_space_export, "delay", lambda pk: exports.build(pk))
    # Scans run on the worker; here, at once.
    monkeypatch.setattr(dictionary_module, "_enqueue_scan", lambda pk: dictionary_module.run_scan(pk))


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _post(client, url, body=None):
    return client.post(url, json.dumps(body or {}), content_type="application/json")


def _api(sid, tail=""):
    return f"/labs/data/api/spaces/{sid}/{tail}"


#: What lineage says the KMC registry's columns inherit, in this test: the visit-count
#: aggregate reads nothing, birth weight reads a field set to round, and one property
#: reads a field nobody reviewed.
LINEAGE = {
    "available": True,
    "columns": [
        {"name": "num_visits", "layer": "aggregate", "inherits": "keep", "label": "Visits"},
        {"name": "birth_weight_g", "layer": "aggregate", "inherits": "round", "label": "Birthweight (g)"},
    ],
    "properties": [{"name": "registered", "layer": "property", "inherits": "unset", "label": "Registered"}],
}


@pytest.fixture
def space(people, monkeypatch, visit_cache):  # noqa: F811 -- the imported fixture
    monkeypatch.setattr(lineage, "for_space", lambda caller, opened: json.loads(json.dumps(LINEAGE)))
    ann = _client(people["ann"])
    sid = _post(ann, "/labs/data/api/spaces/", {"name": "Cases", "opportunity_ids": [OPP]}).json()["space"]["id"]
    assert _post(ann, _api(sid, "dictionary/scan/")).status_code == 200
    return sid


def _entry(client, sid, key):
    return next(e for e in client.get(_api(sid, "dictionary/")).json()["entries"] if e["key"] == key)


def test_case_fields_start_at_what_they_inherit_and_never_go_looser(people, space, monkeypatch):
    ann = _client(people["ann"])
    added = _post(ann, _api(space, "dictionary/cases/")).json()
    assert added["case_fields"] == {"added": 3, "updated": 0}
    weight = _entry(ann, space, "@case:birth_weight_g")
    assert (weight["level"], weight["inherits"], weight["suggested_setting"], weight["reviewed"]) == (
        "case",
        "round",
        "round",
        False,
    )
    looser = _post(ann, _api(space, "dictionary/entry/"), {"key": "@case:birth_weight_g", "setting": "keep"})
    assert looser.status_code == 400 and looser.json()["error"] == "looser_than_sources"
    assert (
        _post(ann, _api(space, "dictionary/entry/"), {"key": "@case:registered", "setting": "keep"}).status_code == 400
    )
    ok = _post(ann, _api(space, "dictionary/entry/"), {"key": "@case:num_visits", "setting": "keep"})
    assert ok.status_code == 200
    # Its sources tighten (a reviewer drops a field it reads): it goes back to unreviewed.
    LINEAGE_NOW = json.loads(json.dumps(LINEAGE))
    LINEAGE_NOW["columns"][0]["inherits"] = "drop"
    monkeypatch.setattr(lineage, "for_space", lambda caller, opened: LINEAGE_NOW)
    assert _post(ann, _api(space, "dictionary/cases/")).json()["case_fields"]["updated"] == 1
    assert _entry(ann, space, "@case:num_visits")["effective"] == "drop"


def _real_case_query(monkeypatch):
    """``cases.compile_sql`` itself, with Connect's three reads stood in for by the
    semantic suite's own schemas and the on-disk KMC registry."""
    from connect_labs.labsdata import workflow_context
    from connect_labs.semantic import runtime, workflow_binding

    props_doc, registry = runtime.load_registry("kmc")
    monkeypatch.setattr(
        workflow_context, "read_workflow", lambda caller, wid, **kw: (SimpleNamespace(), "tok", {"program_id": 1}, [])
    )
    monkeypatch.setattr(
        workflow_binding,
        "resolve_registry_for",
        lambda *a, **k: (props_doc, registry, None, {}, {}, {"registry_id": 7}),
    )
    monkeypatch.setattr(
        workflow_binding,
        "build_evaluate_inputs",
        lambda *a, **k: (_config(ENTITY_SCHEMA, ENTITY_PIPELINE), {"weight_g": _config(VISIT_SCHEMA, VISIT_PIPELINE)}),
    )
    real = cases.compile_sql

    def compile_sql(caller, opened, *, as_of):
        # The space here is opportunities, not a workflow; the statement is the same.
        shaped = SimpleNamespace(
            space=SimpleNamespace(workflow={"id": 1, "program_id": 1}), selection=opened.selection
        )
        monkeypatch.setattr(cases.dictionary_mod, "for_space", lambda o: opened.space.dictionary)
        return real(caller, shaped, as_of=as_of)

    monkeypatch.setattr(cases, "compile_sql", compile_sql)


def test_an_export_adds_one_coded_row_per_case(people, space, monkeypatch, django_capture_on_commit_callbacks):
    _real_case_query(monkeypatch)
    ann, ben = _client(people["ann"]), _client(people["ben"])
    _post(ann, _api(space, "dictionary/cases/"))
    _post(
        ann,
        _api(space, "dictionary/accept/"),
        {"keys": ["@case:num_visits", "@case:birth_weight_g", "@entity_id", "@visit_date"]},
    )
    with django_capture_on_commit_callbacks(execute=True):
        res = _post(ben, _api(space, "exports/"))
    assert res.status_code == 200, res.content
    export = SpaceExport.objects.get(pk=res.json()["export"]["id"])
    assert export.status == SpaceExport.READY, export.checks
    assert export.case_rows == 2  # two babies in the fixture
    assert export.case_query["registry"] == {"registry_id": 7}
    space_row = export.space
    space_row.managers.add(people["ben"])
    _post(ann, _api(space, f"exports/{export.pk}/sign-off/"), {"note": "n", "acknowledge_warnings": True})
    body = b"".join(ben.get(_api(space, f"exports/{export.pk}/download/")).streaming_content)
    z = zipfile.ZipFile(io.BytesIO(body))
    case_rows = z.read("cases.csv").decode().splitlines()
    assert case_rows[0] == "case,case.birth_weight_g,case.num_visits"
    visit_codes = {line.split(",")[0] for line in z.read("data.csv").decode().splitlines()[1:]}
    case_codes = {line.split(",")[0] for line in case_rows[1:]}
    assert case_codes == visit_codes  # the same codes in both files: cases join to visits
    for raw in ("b1", "b2", "asha", "ravi"):
        assert raw not in z.read("cases.csv").decode()
    assert "registry property birth_weight_g" in z.read("dictionary.csv").decode()
    assert "cases.csv: 2 cases" in z.read("README.md").decode()


def test_a_case_field_that_carries_a_name_fails_the_leak_scan(
    people, space, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(
        cases,
        "compile_sql",
        lambda caller, opened, as_of: {
            "sql": "SELECT 'x|b1' AS baby_id, 'asha' AS worker_label UNION ALL SELECT 'x|b2', 'ravi'",
            "row_id": "baby_id",
            "registry": {},
        },
    )
    LINEAGE_NAMES = {"available": True, "columns": [], "properties": [{"name": "worker_label", "inherits": "keep"}]}
    monkeypatch.setattr(lineage, "for_space", lambda caller, opened: LINEAGE_NAMES)
    ann = _client(people["ann"])
    _post(ann, _api(space, "dictionary/cases/"))
    _post(ann, _api(space, "dictionary/accept/"), {"keys": ["@case:worker_label", "@entity_id"]})
    with django_capture_on_commit_callbacks(execute=True):
        pk = _post(ann, _api(space, "exports/")).json()["export"]["id"]
    export = SpaceExport.objects.get(pk=pk)
    scan = next(c for c in export.checks["checks"] if c["name"] == "pii_scan")
    assert export.status == SpaceExport.BLOCKED and "case.worker_label" in scan["detail"]["fail"]
