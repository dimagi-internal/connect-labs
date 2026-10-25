"""Artifacts in a data space (``models.SpaceArtifact``): render code and the named queries it
reads, kept in the space, drawn only in the workflow sandbox.

An artifact inherits its space's rules: listed, opened, saved and deleted only through
``spaces.open`` (someone missing a source gets ``not_found``); any member adds a version, a
member deletes their own, a manager any. Each named query is checked against the space as
a run would be when it is saved AND every time it is opened -- a query naming another
opportunity is refused both times -- and opening runs them over the space as the person,
for the page to hand the frame. No rows are kept; the frame is the workflow sandbox's own.

Holdings are faked at ``scopes.holdings`` (``test_spaces``), so the real gate runs on top.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import pytest

from connect_labs.labsdata import engine, service
from connect_labs.labsdata.models import SpaceArtifact
from connect_labs.labsdata.tests import test_spaces
from connect_labs.labsdata.tests.test_spaces import O1, O2, O3, _client, _post, _program_space
from connect_labs.mcp.tool_registry import MCPToolError

# The spaces tests' invented people and faked Connect (holdings, the engine), reused.
people = test_spaces.people
connect = test_spaces.connect

pytestmark = pytest.mark.django_db

CODE = "function Artifact({ pipelines }) { return <div>{pipelines.by_month.rows.length}</div>; }"
QUERIES = [{"name": "by_month", "sql": "SELECT 1 AS n FROM visits", "max_rows": 100}]


def _artifacts(sid) -> str:
    return f"/labs/data/api/spaces/{sid}/artifacts/"


def _save(client, sid, name="Visits per month", code=CODE, queries=QUERIES):
    return _post(client, _artifacts(sid), {"name": name, "code": code, "queries": queries})


def test_an_artifact_is_kept_in_its_space_versioned_and_audited(people, monkeypatch):
    from connect_labs.audit_trail import service as audit

    recorded = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: recorded.append(k))
    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    first = _save(alice, sid)
    assert first.status_code == 200, first.content
    assert first.json()["saved"]["version"] == 1
    # A teammate who holds the sources sees it, and may add a version.
    again = _save(bob, sid, code=CODE.replace("div", "section"))
    assert again.json()["saved"]["version"] == 2
    listed = bob.get(_artifacts(sid)).json()["artifacts"]
    assert [(a["name"], a["version"], a["saved_by"]) for a in listed] == [("Visits per month", 2, "space-bob")]
    assert [v["version"] for v in listed[0]["versions"]] == [2, 1]
    # The rail is never handed the code; opening fetches it, through the gate.
    assert "code" not in listed[0]
    stored = SpaceArtifact.objects.get(space_id=sid, version=2)
    assert stored.code == CODE.replace("div", "section")
    assert stored.queries == [{"name": "by_month", "sql": "SELECT 1 AS n FROM visits", "max_rows": 100}]
    # One audit event per opportunity the space reads, as a save is recorded.
    events = [e for e in recorded if e.get("resource_type") == "labsdata_space_artifact"]
    assert sorted(e["opportunity_id"] for e in events) == sorted([O1.id, O2.id] * 2)


def test_opening_runs_its_queries_over_the_space_as_the_person_and_keeps_nothing(people, connect):
    sid = _program_space(people)
    pk = _save(_client(people["alice"]), sid).json()["saved"]["id"]
    res = _post(_client(people["bob"]), f"/labs/data/api/spaces/{sid}/artifacts/{pk}/run/", {})
    assert res.status_code == 200, res.content
    out = res.json()
    assert out["artifact"]["code"] == CODE
    assert out["results"]["by_month"]["columns"] == ["n"] and out["results"]["by_month"]["rows"] == [[3]]
    # Over the space's frozen opportunities, and nothing else.
    assert connect == [[O1.id, O2.id]]
    assert SpaceArtifact.objects.get(pk=pk).queries == QUERIES  # no rows kept anywhere on it


def test_a_query_naming_an_opportunity_outside_the_space_is_refused_at_save(people):
    sid = _program_space(people)
    outside = [{**QUERIES[0], "opportunity_ids": [O3.id]}]
    res = _save(_client(people["alice"]), sid, queries=outside)
    assert res.status_code == 400 and res.json()["error"] == "outside_space"
    # Nor another space, program or workflow.
    for extra in ({"space_id": sid + 1}, {"program_ids": [123]}, {"workflow": {"id": 5, "opportunity_id": O1.id}}):
        res = _save(_client(people["alice"]), sid, queries=[{**QUERIES[0], **extra}])
        assert res.status_code == 400 and res.json()["error"] == "outside_space", extra
    assert not SpaceArtifact.objects.exists()
    # The space's own sources, said again, are no widening.
    own = [{**QUERIES[0], "opportunity_ids": [O1.id, O2.id]}]
    assert _save(_client(people["alice"]), sid, queries=own).status_code == 200


def test_a_query_naming_an_opportunity_outside_the_space_is_refused_at_run(people, connect):
    """Checked again every time it is opened: a stored artifact that somehow names another
    opportunity (written behind the save's back) runs nothing."""
    sid = _program_space(people)
    pk = _save(_client(people["alice"]), sid).json()["saved"]["id"]
    SpaceArtifact.objects.filter(pk=pk).update(queries=[{**QUERIES[0], "opportunity_ids": [O3.id]}])
    res = _post(_client(people["alice"]), f"/labs/data/api/spaces/{sid}/artifacts/{pk}/run/", {})
    assert res.status_code == 400 and res.json()["error"] == "outside_space"
    assert connect == []  # nothing ran
    # A draft takes the same path.
    draft = _post(
        _client(people["alice"]),
        "/labs/data/api/artifact-draft/run/",
        {"space_id": sid, "queries": [{**QUERIES[0], "opportunity_ids": [O3.id]}]},
    )
    assert draft.status_code == 400 and draft.json()["error"] == "outside_space"
    assert connect == []


def test_each_query_is_validated_over_the_space_at_save(people, monkeypatch):
    sid = _program_space(people)
    checked = []

    def validate(sql, space):
        checked.append((sql, sorted(space.selection.ids)))
        if "nope" in sql:
            raise engine.QueryError("invalid_sql", 'relation "nope" does not exist')
        return {"valid": True}

    monkeypatch.setattr(service, "validate_selected", validate)
    queries = [QUERIES[0], {"name": "bad", "sql": "SELECT * FROM nope"}]
    res = _save(_client(people["alice"]), sid, queries=queries)
    assert res.status_code == 400 and res.json()["error"] == "invalid_sql"
    assert res.json()["message"].startswith("Query bad:")
    assert checked == [(QUERIES[0]["sql"], [O1.id, O2.id]), ("SELECT * FROM nope", [O1.id, O2.id])]
    assert not SpaceArtifact.objects.exists()


@pytest.mark.parametrize(
    "body, code",
    [
        ({"code": "  "}, "bad_code"),
        ({"code": "x" * (512 * 1024 + 1)}, "bad_code"),
        ({"queries": []}, "bad_queries"),
        ({"queries": [{"name": "1bad", "sql": "SELECT 1"}]}, "bad_queries"),
        ({"queries": [QUERIES[0], QUERIES[0]]}, "bad_queries"),
        ({"queries": [{**QUERIES[0], "rows": [[1]]}]}, "bad_queries"),
        ({"queries": [{"name": f"q{i}", "sql": "SELECT 1"} for i in range(9)]}, "bad_queries"),
        ({"name": ""}, "name_required"),
    ],
)
def test_an_artifact_that_cannot_be_kept_is_refused_as_itself(people, body, code):
    sid = _program_space(people)
    res = _post(_client(people["alice"]), _artifacts(sid), {"name": "A", "code": CODE, "queries": QUERIES, **body})
    assert res.status_code == 400 and res.json()["error"] == code, res.content


@pytest.mark.parametrize("who", ["carol", "dave"])
def test_someone_who_cannot_open_the_space_cannot_read_or_render_its_artifacts(people, connect, who):
    sid = _program_space(people)
    pk = _save(_client(people["alice"]), sid).json()["saved"]["id"]
    c = _client(people[who])
    for res in (
        c.get(_artifacts(sid)),
        _post(c, f"/labs/data/api/spaces/{sid}/artifacts/{pk}/run/", {}),
        _post(c, f"/labs/data/api/spaces/{sid}/artifacts/{pk}/delete/", {}),
        _save(c, sid),
        _post(c, "/labs/data/api/artifact-draft/run/", {"space_id": sid, "queries": QUERIES}),
    ):
        assert res.status_code == 404 and res.json()["error"] == "not_found", res.content
        assert "Visits per month" not in res.content.decode()
    assert connect == []
    assert SpaceArtifact.objects.count() == 1


def test_a_member_deletes_their_own_artifact_and_a_manager_any(people):
    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    alices = _save(alice, sid, name="Alice's").json()["saved"]["id"]
    bobs = _save(bob, sid, name="Bob's").json()["saved"]["id"]
    listed = {a["name"]: a["can_delete"] for a in bob.get(_artifacts(sid)).json()["artifacts"]}
    assert listed == {"Alice's": False, "Bob's": True}
    refused = _post(bob, f"/labs/data/api/spaces/{sid}/artifacts/{alices}/delete/", {})
    assert refused.status_code == 403 and refused.json()["error"] == "not_manager"
    assert _post(bob, f"/labs/data/api/spaces/{sid}/artifacts/{bobs}/delete/", {}).json()["deleted"] == 1
    # Alice manages the space (she made it): she deletes any.
    _save(bob, sid, name="Alice's")  # Bob adds a version of hers
    assert _post(alice, f"/labs/data/api/spaces/{sid}/artifacts/{alices}/delete/", {}).json()["deleted"] == 2
    assert not SpaceArtifact.objects.exists()


def test_a_draft_runs_as_the_person_and_saves_nothing(people, connect):
    sid = _program_space(people)
    res = _post(_client(people["bob"]), "/labs/data/api/artifact-draft/run/", {"space_id": sid, "queries": QUERIES})
    assert res.status_code == 200, res.content
    assert res.json()["results"]["by_month"]["rows"] == [[3]]
    # Invented opportunities are not generated data: the page asks before drawing the draft.
    assert res.json()["all_generated"] is False
    assert connect == [[O1.id, O2.id]] and not SpaceArtifact.objects.exists()
    # On the plain page it reads the selection on screen, generated as the person.
    plain = _post(
        _client(people["bob"]),
        "/labs/data/api/artifact-draft/run/",
        {"opportunity_ids": [O1.id], "queries": QUERIES},
    )
    assert plain.status_code == 200 and connect[-1] == [O1.id]


def test_a_query_that_fails_to_run_says_so_and_the_others_still_run(people, monkeypatch):
    sid = _program_space(people)

    def run_query(opps, sql, max_rows=None, relations=None):
        if "bad" in sql:
            raise engine.QueryError("invalid_sql", "column bad does not exist")
        return engine.QueryResult(["n"], [[3]], 1, False, sql, 1)

    monkeypatch.setattr(engine, "run_query", run_query)
    queries = [QUERIES[0], {"name": "other", "sql": "SELECT bad FROM visits"}]
    out = _post(_client(people["alice"]), "/labs/data/api/artifact-draft/run/", {"space_id": sid, "queries": queries})
    results = out.json()["results"]
    assert results["by_month"]["rows"] == [[3]]
    assert results["other"]["rows"] == [] and results["other"]["error"]["code"] == "invalid_sql"


def test_the_data_page_draws_artifacts_only_in_the_workflow_sandbox(people, settings):
    """The page loads the sandbox host and carries the run page's enforced frame-src: the
    frame may be only the sandbox's shell (or canopy's panel). It loads no Babel and runs
    no code of its own -- the frame (workflow/sandbox.py) compiles it, opaque-origin."""
    from connect_labs.workflow.sandbox import SHELL_PATH, frame_policy, host_frame_src

    sid = _program_space(people)
    res = _client(people["bob"]).get(f"/labs/data/space/{sid}/")
    assert res.status_code == 200
    assert res["Content-Security-Policy"] == host_frame_src("http://testserver")
    assert res["Content-Security-Policy"].startswith(f"frame-src http://testserver{SHELL_PATH}")
    html = res.content.decode()
    assert "bundles/js/space-artifact-bundle.js" in html
    assert 'id="ex-artifacts"' in html
    assert "babel" not in html.lower()
    # The shell it draws is the one workflows draw in: no labs origin to reach, no network.
    policy = frame_policy("http://testserver")
    connect_src = next(p for p in policy.split("; ") if p.startswith("connect-src"))
    assert "testserver" not in connect_src and "*" not in connect_src.replace("*.tiles", "")
    assert "sandbox allow-scripts" in policy and "allow-same-origin" not in policy
    # Switched off, artifacts are not drawn at all -- never unsandboxed.
    settings.LABS_PAGE_SANDBOX = False
    off = _client(people["bob"]).get(f"/labs/data/space/{sid}/")
    assert "space-artifact-bundle.js" not in off.content.decode()
    assert "Content-Security-Policy" not in off or SHELL_PATH not in off["Content-Security-Policy"]


def test_the_data_page_script_never_runs_an_artifacts_code_itself():
    """data.js hands code only to the sandbox host (window.LabsArtifact.mount); it has no
    eval, no Function, no Babel and no same-origin frame of its own."""
    import pathlib

    js = (pathlib.Path(__file__).resolve().parents[2] / "static" / "labsdata" / "data.js").read_text()
    for forbidden in ("eval(", "new Function", "Babel", "srcdoc", "allow-same-origin", "createElement('iframe'"):
        assert forbidden not in js, forbidden
    assert "LabsArtifact" in js and "host.mount(" in js


# --- the agent's side: data_describe lists them; data_artifact reads code, never rows ------


def test_the_agent_reads_an_artifacts_code_and_queries_never_its_rows(people, connect):
    from connect_labs.mcp.tools import labsdata as tools

    sid = _program_space(people)
    pk = _save(_client(people["alice"]), sid).json()["saved"]["id"]
    _post(_client(people["alice"]), f"/labs/data/api/spaces/{sid}/items/", {"name": "Weekly", "sql": "SELECT 1"})
    described = tools.describe(people["bob"], space_id=sid)
    items = described["space"]["items"]
    assert items["artifacts"] == [{"id": pk, "name": "Visits per month", "version": 1, "queries": ["by_month"]}]
    assert [q["name"] for q in items["queries"]] == ["Weekly"]
    out = tools.data_artifact(people["bob"], space_id=sid, artifact_id=pk)
    assert out["code"] == CODE and out["queries"] == QUERIES
    assert "results" not in out and "rows" not in str(out).replace("max_rows", "").replace(".rows", "")
    ran = list(connect)
    tools.data_artifact(people["bob"], space_id=sid, artifact_id=pk)
    assert connect == ran  # reading an artifact runs nothing
    for who in ("carol", "dave"):
        with pytest.raises(MCPToolError) as e:
            tools.data_artifact(people[who], space_id=sid, artifact_id=pk)
        assert e.value.code == "NOT_FOUND"
    with pytest.raises(MCPToolError) as e:
        tools.data_artifact(people["bob"], space_id=sid, artifact_id=pk + 999)
    assert e.value.code == "NOT_FOUND"
