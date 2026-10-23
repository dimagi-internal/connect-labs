"""Host-held results (``mcp/artifacts.py``): data_query keeps its rows as a
``labs-result://`` artifact and returns facts plus a link; the link is read back as the
reader, by the owner and by whoever could run the same query, a page at a time, for a day.

canopy-web spec ``2026-10-23-host-held-results-design``. Over HTTP, through canopy, with
real DPoP and execution statements: ``test_labsdata_end_to_end.py``.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

import pytest
from canopy_sdk.django import artifacts as sdk
from canopy_sdk.django.models import Artifact as Row
from canopy_sdk.host.artifacts import ArtifactRefused
from django.contrib.auth import get_user_model
from django.utils import timezone
from fastmcp.server.auth import AccessToken
from mcp.types import ResourceLink

from connect_labs.labs import canopy
from connect_labs.labsdata import engine, scope
from connect_labs.labsdata.scope import Opportunity, ScopeError
from connect_labs.mcp import artifacts
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool

pytestmark = pytest.mark.django_db

OPP = Opportunity(id=973, name="Opp Kept", llo="LLO Kept")
COLUMNS = ["worker", "visits"]
ROWS = [[f"w{i:03d}", i % 9] for i in range(45)]


@pytest.fixture
def owner():
    return get_user_model().objects.create_user(username="kept-owner", password="x")


@pytest.fixture
def colleague():
    return get_user_model().objects.create_user(username="kept-colleague", password="x")


@pytest.fixture
def connect(monkeypatch):
    """Connect as labs sees it: who holds OPP (``held``), and the query's rows."""
    held = {"users": set()}

    def resolve(caller, ids):
        if caller.user is not None and caller.user.pk not in held["users"]:
            raise ScopeError("not_held", f"you do not have access to opportunity {OPP.id}")
        return [OPP]

    monkeypatch.setattr(scope, "resolve", resolve)
    monkeypatch.setattr("connect_labs.mcp.tools.labsdata.require_connect_token", lambda user: "tok")
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda user: "tok")
    monkeypatch.setattr(
        engine,
        "run_query",
        lambda opps, sql, max_rows: engine.QueryResult(COLUMNS, [list(r) for r in ROWS], len(ROWS), False, sql, 3),
    )
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: False)
    return held


def _pat(user) -> str:
    _, raw = MCPAccessToken.create_token(user, name="kept")
    return raw


def _query(user):
    return call_tool(
        _pat(user), "data_query", {"opportunity_ids": [OPP.id], "sql": "SELECT worker, visits FROM visits"}
    )


def test_labs_declares_its_scheme_and_its_read_rules():
    from canopy_sdk.django import conf

    assert canopy.host_settings()["ARTIFACTS"] == {
        "SCHEME": "labs-result",
        "TTL": 86400,
        "MAY_READ": "connect_labs.mcp.artifacts.may_read",
        "ON_READ": None,
        "READER": "connect_labs.mcp.artifacts.reader_subject",
        "NOTIFY": True,
    }
    # site.json declares it, so canopy keeps the links in a sensitive conversation.
    assert conf.resource_schemes() == ["labs-result"]


def test_data_query_keeps_the_rows_and_returns_a_link_owned_by_the_caller(owner, connect):
    connect["users"].add(owner.pk)
    envelope = _query(owner)["result"]
    out = envelope["structuredContent"]
    # A person's own client (no site_read) keeps every row inline, as before.
    assert out["rows"] == ROWS and out["row_count"] == 45
    assert "_links" not in out
    text, link = envelope["content"]
    assert json.loads(text.text)["result_uri"] == out["result_uri"]
    assert isinstance(link, ResourceLink) and str(link.uri) == out["result_uri"]
    assert link.mimeType == "application/json" and link.name == f"Query over {OPP.name}"

    row = Row.objects.get()
    assert row.owner == str(owner.pk) and row.kind == "data_query" and row.scheme == "labs-result"
    assert row.payload["rows"] == ROWS and row.payload["sql_executed"] == "SELECT worker, visits FROM visits"
    assert row.payload["access"] == {"opportunity_ids": [OPP.id], "sensitive": True, "through_canopy": False}
    assert row.tool_call_id.startswith("mcp:")
    assert timedelta(hours=23) < row.expires_at - timezone.now() <= timedelta(hours=24)


def test_canopys_agent_gets_facts_a_sample_and_the_link(owner, connect, monkeypatch):
    from connect_labs.labs import canopy_panel
    from connect_labs.mcp.tools import labsdata as tools

    connect["users"].add(owner.pk)
    monkeypatch.setattr(tools, "_from_canopy", lambda: True)
    monkeypatch.setattr(canopy_panel, "call_sensitive_data_clearance", lambda: canopy_panel.Clearance(True))
    out = _query(owner)["result"]["structuredContent"]
    assert "rows" not in out
    assert out["row_count"] == 45 and out["sample_is_partial"] is True
    assert len(out["sample_rows"]) == artifacts.INLINE_ROWS
    assert out["sample_rows"][0] == {"worker": "w000", "visits": 0}
    assert any("site_read" in n for n in out["notes"])
    # The artifact still holds every row.
    assert len(Row.objects.get().payload["rows"]) == 45


def test_explorer_query_stays_inline_and_keeps_nothing(owner, connect):
    connect["users"].add(owner.pk)
    out = call_tool(
        _pat(owner), "explorer_query", {"opportunity_ids": [OPP.id], "sql": "SELECT worker, visits FROM visits"}
    )["result"]["structuredContent"]
    assert out["rows"] == ROWS and "result_uri" not in out
    assert not Row.objects.exists()


def test_a_withheld_query_keeps_the_hand_off_for_the_card(owner, connect, monkeypatch):
    from connect_labs.labs import canopy_panel
    from connect_labs.mcp.tools import labsdata as tools

    connect["users"].add(owner.pk)
    monkeypatch.setattr(tools, "_from_canopy", lambda: True)
    monkeypatch.setattr(canopy_panel, "call_sensitive_data_clearance", lambda: canopy_panel.Clearance(False, "no"))
    monkeypatch.setattr(engine, "validate_query", lambda sql: {"valid": True})
    out = _query(owner)["result"]["structuredContent"]
    assert out["withheld"] is True and out["result_uri"].startswith("labs-result://")
    kept = sdk.read(out["result_uri"], reader=str(owner.pk))
    assert kept.payload["withheld"] is True and "rows" not in kept.payload
    assert kept.payload["access"]["sensitive"] is False


# --- reading ---------------------------------------------------------------------------


def _uri(owner, connect):
    connect["users"].add(owner.pk)
    return _query(owner)["result"]["structuredContent"]["result_uri"]


def test_the_owner_reads_it_a_page_at_a_time(owner, connect):
    uri = _uri(owner, connect)
    (content,) = sdk.resource_handler(f"{uri}?page=3&page_size=20", reader=str(owner.pk))
    body = json.loads(content["text"])
    assert body["rows"] == ROWS[40:45] and body["columns"] == COLUMNS
    meta = content["_meta"]["canopy/artifact"]
    assert (meta["page"], meta["page_size"], meta["pages"], meta["row_count"]) == (3, 20, 3, 45)
    assert meta["snapshot"] is True and meta["kind"] == "data_query"


def test_a_colleague_who_holds_the_opportunities_may_read_it(owner, colleague, connect):
    uri = _uri(owner, connect)
    connect["users"].add(colleague.pk)
    assert sdk.read(uri, reader=str(colleague.pk)).owner == str(owner.pk)


def test_one_who_could_not_run_the_query_may_not(owner, colleague, connect):
    uri = _uri(owner, connect)
    with pytest.raises(ArtifactRefused) as refused:
        sdk.read(uri, reader=str(colleague.pk))
    assert refused.value.code == "forbidden"


def test_an_inactive_or_unknown_reader_may_not(owner, colleague, connect):
    uri = _uri(owner, connect)
    connect["users"].add(colleague.pk)
    colleague.is_active = False
    colleague.save()
    for reader in (str(colleague.pk), "999999", None):
        with pytest.raises(ArtifactRefused) as refused:
            sdk.read(uri, reader=reader)
        assert refused.value.code == "forbidden"


def test_an_expired_result_reads_as_expired_and_is_swept(owner, connect):
    from connect_labs.mcp.tasks import sweep_artifacts

    uri = _uri(owner, connect)
    Row.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ArtifactRefused) as refused:
        sdk.read(uri, reader=str(owner.pk))
    assert refused.value.code == "expired"
    assert sweep_artifacts() == 1 and not Row.objects.exists()
    with pytest.raises(ArtifactRefused) as gone:
        sdk.read(uri, reader=str(owner.pk))
    assert gone.value.code == "not_found"


def test_the_sweep_is_scheduled():
    from django.conf import settings

    assert (
        settings.CELERY_BEAT_SCHEDULE["sweep-labs-result-artifacts"]["task"]
        == "connect_labs.mcp.tasks.sweep_artifacts"
    )


# --- labs' own gate, for every reader (server.ArtifactReadGate) -------------------------


def _token(user, **claims) -> AccessToken:
    return AccessToken(
        token="t",
        client_id="c",
        scopes=[],
        claims={"sub": str(user.pk), "user_id": user.pk, "auth_method": "pat", **claims},
    )


def test_the_reader_is_the_caller_of_the_request_in_flight(owner):
    with mock.patch("connect_labs.mcp.server.get_access_token", return_value=_token(owner)):
        assert artifacts.reader_subject() == str(owner.pk)
    with mock.patch("connect_labs.mcp.server.get_access_token", return_value=None):
        assert artifacts.reader_subject() is None


def test_a_no_user_visit_data_caller_never_reads_sensitive_rows_even_its_own(owner, connect):
    uri = _uri(owner, connect)
    kept = artifacts.stored(uri)
    assert artifacts.read_denial(kept, _token(owner)) is None
    assert "user visit data" in artifacts.read_denial(kept, _token(owner, labs_restricted_user=True))


def test_rows_that_are_not_sensitive_are_not_judged(owner, connect, monkeypatch):
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: True)
    uri = _uri(owner, connect)
    kept = artifacts.stored(uri)
    assert kept.payload["access"]["sensitive"] is False
    assert artifacts.read_denial(kept, _token(owner, labs_restricted_user=True)) is None


def test_stored_names_only_a_live_labs_result(owner, connect):
    uri = _uri(owner, connect)
    assert artifacts.stored(f"{uri}?page=2").id == artifacts.stored(uri).id
    assert artifacts.stored("labs-result://nope") is None
    assert artifacts.stored("https://example.org/x") is None
    Row.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert artifacts.stored(uri) is None


def test_every_read_is_audit_logged(owner, connect):
    from connect_labs.audit_trail.models import AuditEvent

    uri = _uri(owner, connect)
    artifacts.record_read(artifacts.stored(uri), owner, f"{uri}?page=2&page_size=20")
    event = AuditEvent.objects.filter(resource_type="labs_result").latest("id")
    assert (event.action, event.opportunity_id, event.record_count) == ("read", OPP.id, 20)
    assert event.metadata["kind"] == "data_query" and event.metadata["page"] == 2
