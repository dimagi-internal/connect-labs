"""Kept results (``mcp/kept_results.py``): data_query keeps its rows as a
``labs-result://`` result and returns facts plus a link; the link is read back as the
reader, by the owner and by whoever could run the same query, a page at a time, for a day.

Over HTTP, through canopy, with real DPoP and execution statements:
``test_labsdata_end_to_end.py``.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from fastmcp.server.auth import AccessToken
from mcp.types import ResourceLink

from connect_labs.labs import canopy
from connect_labs.labsdata import engine, scope
from connect_labs.labsdata.scope import Opportunity, ScopeError
from connect_labs.mcp import kept_results
from connect_labs.mcp.kept_results import Refused
from connect_labs.mcp.models import KeptResult as Row
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool, read_resource

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
        lambda opps, sql, max_rows, **kw: engine.QueryResult(
            COLUMNS, [list(r) for r in ROWS], len(ROWS), False, sql, 3
        ),
    )
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: False)
    return held


def _pat(user) -> str:
    _, raw = MCPAccessToken.create_token(user, name="kept")
    return raw


def _query(user, keep_result=True):
    """A person's own client (a PAT) asking data_query to keep its result: the result a
    canopy call keeps, without canopy."""
    args = {"opportunity_ids": [OPP.id], "sql": "SELECT worker, visits FROM visits"}
    return call_tool(_pat(user), "data_query", {**args, "keep_result": True} if keep_result else args)


def test_a_client_that_will_not_read_it_back_gets_its_rows_and_nothing_is_kept(owner, connect):
    connect["users"].add(owner.pk)
    envelope = _query(owner, keep_result=False)["result"]
    out = envelope["structuredContent"]
    assert out["rows"] == ROWS and "result_uri" not in out
    assert [c for c in envelope["content"] if isinstance(c, ResourceLink)] == []
    assert not Row.objects.exists()


def test_labs_declares_its_scheme_and_keeps_the_results_itself():
    from canopy_sdk.django import conf

    settings = canopy.host_settings()
    # The storage is labs' own (dimagi-canopy >= 0.19 has no helper for it).
    assert "ARTIFACTS" not in settings
    assert settings["RESOURCE_SCHEMES"] == [kept_results.SCHEME] == ["labs-result"]
    # site.json declares it, so canopy keeps the links in a sensitive conversation.
    assert conf.resource_schemes() == ["labs-result"]
    # ...and how long its ids are, so canopy keeps nothing but `labs-result://<that id>`.
    assert conf.resource_id_lengths() == {"labs-result": 24} == settings["RESOURCE_ID_LENGTHS"]
    assert kept_results.TTL_SECONDS == 86400


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
    assert row.owner == owner and row.kind == "data_query" and out["result_uri"] == f"labs-result://{row.id}"
    assert row.payload["rows"] == ROWS and row.payload["sql_executed"] == "SELECT worker, visits FROM visits"
    # What the read rules ask is kept beside the payload, not in it.
    assert "access" not in row.payload
    assert row.access == {"opportunity_ids": [OPP.id], "sensitive": True, "through_canopy": False}
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
    assert len(out["sample_rows"]) == kept_results.INLINE_ROWS
    assert out["sample_rows"][0] == {"worker": "w000", "visits": 0}
    assert any("site_read" in n for n in out["notes"])
    # The kept result still holds every row.
    assert len(Row.objects.get().payload["rows"]) == 45


def test_canopys_agent_gets_column_types_and_is_told_the_card_shows_the_rows(owner, connect, monkeypatch):
    from connect_labs.labs import canopy_panel
    from connect_labs.mcp.tools import labsdata as tools

    asked = {}

    def run_query(opps, sql, max_rows=None, relations=(), **_):
        asked["max_rows"] = max_rows
        return engine.QueryResult(
            COLUMNS,
            [list(r) for r in ROWS],
            len(ROWS),
            False,
            sql,
            3,
            column_types=["text", "integer"],
            row_cap={"limit": 500, "source": "default"},
        )

    connect["users"].add(owner.pk)
    monkeypatch.setattr(engine, "run_query", run_query)
    monkeypatch.setattr(tools, "_from_canopy", lambda: True)
    monkeypatch.setattr(canopy_panel, "call_sensitive_data_clearance", lambda: canopy_panel.Clearance(True))
    out = _query(owner)["result"]["structuredContent"]
    # max_rows left out reaches the engine as "the default", not as a number the caller chose.
    assert asked["max_rows"] is None
    assert out["column_types"] == ["text", "integer"]
    assert out["row_cap"] == {"limit": 500, "source": "default"}
    assert any("not a markdown table" in n for n in out["notes"])
    # The card reads the types (and the cap) from the kept result.
    payload = Row.objects.get().payload
    assert payload["column_types"] == ["text", "integer"] and payload["row_cap"]["source"] == "default"


def test_a_query_error_reaches_the_client_with_its_category_and_recovery(owner, connect, monkeypatch):
    def run_query(opps, sql, max_rows=None, relations=(), **_):
        raise engine.QueryError("timeout", "The query ran past 20s. Aggregate or narrow it.")

    connect["users"].add(owner.pk)
    monkeypatch.setattr(engine, "run_query", run_query)
    result = _query(owner)["result"]
    assert result["isError"] is True
    # The error text a client receives: the message, then its details as one JSON line.
    message = result["structuredContent"]["error"]["message"]
    first, last = message.split("\n")[0], message.split("\n")[-1]
    assert first == "The query ran past 20s. Aggregate or narrow it."
    assert json.loads(last) == {
        "code": "INVALID_SCHEMA",
        "error": "timeout",
        "message": "The query ran past 20s. Aggregate or narrow it.",
        "category": "capacity",
        "retryable": False,
        "recovery_action": "narrow_or_aggregate",
    }


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
    monkeypatch.setattr(engine, "validate_query", lambda sql, **kw: {"valid": True})
    out = _query(owner)["result"]["structuredContent"]
    assert out["withheld"] is True and out["result_uri"].startswith("labs-result://")
    kept = kept_results.read(out["result_uri"], reader=owner.pk)
    assert kept.payload["withheld"] is True and "rows" not in kept.payload
    assert kept.sensitive is False


# --- reading ---------------------------------------------------------------------------


def _uri(owner, connect):
    connect["users"].add(owner.pk)
    return _query(owner)["result"]["structuredContent"]["result_uri"]


def test_the_owner_reads_it_a_page_at_a_time(owner, connect):
    uri = _uri(owner, connect)
    (content,) = kept_results.contents(f"{uri}?page=3&page_size=20", reader=owner.pk)
    body = json.loads(content["text"])
    assert body["rows"] == ROWS[40:45] and body["columns"] == COLUMNS
    meta = content["_meta"]["labs/result"]
    assert (meta["page"], meta["page_size"], meta["pages"], meta["row_count"]) == (3, 20, 3, 45)
    assert meta["snapshot"] is True and meta["kind"] == "data_query"
    # Default page size 100; at most 1000; a page past the end is the last.
    (content,) = kept_results.contents(uri, reader=owner.pk)
    assert content["_meta"]["labs/result"]["page_size"] == 100 and len(json.loads(content["text"])["rows"]) == 45
    (content,) = kept_results.contents(f"{uri}?page=9&page_size=5000", reader=owner.pk)
    assert (content["_meta"]["labs/result"]["page_size"], content["_meta"]["labs/result"]["page"]) == (1000, 1)


def test_a_colleague_who_holds_the_opportunities_may_read_it(owner, colleague, connect):
    uri = _uri(owner, connect)
    connect["users"].add(colleague.pk)
    assert kept_results.read(uri, reader=colleague.pk).owner == owner


def test_one_who_could_not_run_the_query_may_not(owner, colleague, connect):
    uri = _uri(owner, connect)
    with pytest.raises(Refused) as refused:
        kept_results.read(uri, reader=colleague.pk)
    assert refused.value.code == "forbidden"


def test_an_inactive_or_unknown_reader_may_not(owner, colleague, connect):
    uri = _uri(owner, connect)
    connect["users"].add(colleague.pk)
    colleague.is_active = False
    colleague.save()
    for reader in (colleague.pk, 999999, None):
        with pytest.raises(Refused) as refused:
            kept_results.read(uri, reader=reader)
        assert refused.value.code == "forbidden"


def test_an_expired_result_reads_as_expired_and_is_swept(owner, connect):
    from connect_labs.mcp.tasks import sweep_kept_results

    uri = _uri(owner, connect)
    Row.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(Refused) as refused:
        kept_results.read(uri, reader=owner.pk)
    assert refused.value.code == "expired"
    assert sweep_kept_results() == 1 and not Row.objects.exists()
    with pytest.raises(Refused) as gone:
        kept_results.read(uri, reader=owner.pk)
    assert gone.value.code == "not_found"
    with pytest.raises(Refused) as bad:
        kept_results.read("https://example.org/x", reader=owner.pk)
    assert bad.value.code == "bad_uri"


def test_the_sweep_is_scheduled():
    from django.conf import settings

    assert settings.CELERY_BEAT_SCHEDULE["sweep-kept-results"]["task"] == "connect_labs.mcp.tasks.sweep_kept_results"


# --- labs' own gate, for every reader (kept_results.read_as) ---------------------------


def _token(user, **claims) -> AccessToken:
    return AccessToken(
        token="t",
        client_id="c",
        scopes=[],
        claims={"sub": str(user.pk), "user_id": user.pk, "auth_method": "pat", **claims},
    )


def test_the_reader_is_the_caller_of_the_request_in_flight(owner):
    with mock.patch("connect_labs.mcp.server.get_access_token", return_value=_token(owner)):
        assert kept_results.reader_id() == owner.pk
    with mock.patch("connect_labs.mcp.server.get_access_token", return_value=None):
        assert kept_results.reader_id() is None


def test_a_no_user_visit_data_caller_never_reads_sensitive_rows_even_its_own(owner, connect):
    _uri(owner, connect)
    kept = Row.objects.get()
    assert kept_results.read_denial(kept, _token(owner)) is None
    assert "user visit data" in kept_results.read_denial(kept, _token(owner, labs_restricted_user=True))


def test_rows_that_are_not_sensitive_are_not_judged(owner, connect, monkeypatch):
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: True)
    _uri(owner, connect)
    kept = Row.objects.get()
    assert kept.sensitive is False
    assert kept_results.read_denial(kept, _token(owner, labs_restricted_user=True)) is None


@pytest.mark.django_db(transaction=True)
def test_every_read_is_audit_logged_through_the_real_resources_read(owner, colleague, connect):
    from connect_labs.audit_trail.models import AuditEvent

    uri = _uri(owner, connect)
    contents = read_resource(_pat(owner), f"{uri}?page=2&page_size=20")
    assert len(json.loads(contents[0].text)["rows"]) == 20
    event = AuditEvent.objects.filter(resource_type="labs_result", outcome="success").latest("id")
    assert (event.action, event.opportunity_id, event.record_count) == ("read", OPP.id, 20)
    assert event.source == "mcp" and event.path == "mcp:resources/read" and event.request_id.startswith("mcp:")
    assert event.user_id == owner.pk
    assert event.metadata["kind"] == "data_query" and event.metadata["page"] == 2
    assert event.metadata["auth_method"] == "pat" and event.metadata["client_id"] == str(owner.pk)

    # A refused read is recorded too: who asked, for what, and why.
    refused = read_resource(_pat(colleague), uri)
    assert isinstance(refused, Exception) and "forbidden" in str(refused)
    event = AuditEvent.objects.filter(resource_type="labs_result", outcome="failure").latest("id")
    assert event.user_id == colleague.pk and event.source == "mcp" and event.path == "mcp:resources/read"
    assert event.metadata["refused"] == "forbidden" and event.resource_id == uri.split("://")[1]
    # ...for which opportunity: refused before the result was judged, its access still named.
    assert event.opportunity_id == OPP.id
    # The refusal is structured: a View decides on `data.code`, never on the message's words.
    assert refused.error.data == {"code": "forbidden"}
    missing = read_resource(_pat(owner), "labs-result://nope")
    assert "not_found" in str(missing) and missing.error.data == {"code": "not_found"}
    assert AuditEvent.objects.filter(resource_type="labs_result", outcome="failure", resource_id="nope").exists()


def test_someone_who_may_not_read_it_never_learns_it_expired(owner, colleague, connect):
    uri = _uri(owner, connect)
    Row.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(Refused) as refused:
        kept_results.read(uri, reader=colleague.pk)
    assert refused.value.code == "forbidden"
    with pytest.raises(Refused) as own:
        kept_results.read(uri, reader=owner.pk)
    assert own.value.code == "expired"


def test_a_result_too_large_to_keep_is_refused(owner, connect, settings):
    connect["users"].add(owner.pk)
    settings.KEPT_RESULT_MAX_BYTES = 500
    out = _query(owner)["result"]
    assert out["isError"] is True and "ask for fewer rows" in out["structuredContent"]["error"]["message"]
    assert not Row.objects.exists()


def test_a_page_is_read_without_loading_the_whole_payload(owner, connect):
    uri = _uri(owner, connect)
    kept = kept_results.read(uri, reader=owner.pk)
    assert "payload" not in kept.__dict__  # deferred for the ACL check
    body, paging = kept_results.page_of(kept, {"page": "3", "page_size": "20"})
    assert body["rows"] == ROWS[40:45] and body["columns"] == COLUMNS
    assert paging == {"page": 3, "page_size": 20, "pages": 3, "row_count": 45}
    assert "payload" not in kept.__dict__


def test_an_agents_page_is_cut_as_its_query_was(owner, connect):
    from connect_labs.labsdata import service

    uri = _uri(owner, connect)
    kept = kept_results.read(uri, reader=owner.pk)
    big = "x" * (service.AGENT_CELL_CHARS + 50)
    body = {"columns": COLUMNS, "rows": [[big, 1]], "truncated": True}
    shaped = kept_results._shaped(body, {"row_count": 1})
    assert "[cut:" in shaped["rows"][0][0] and shaped["truncated"] is True
    assert any("cut at" in n for n in shaped["notes"])
    (content,) = kept_results.contents_of(kept, uri, for_agent=True, paged=(body, {"row_count": 1, "page": 1}))
    assert "[cut:" in json.loads(content["text"])["rows"][0][0]


def test_the_public_pmc_ranking_lasts_a_month(owner):
    link = kept_results.save(
        owner, kept_results.KIND_PMC, {"recommendation": {}}, access={"sensitive": False}, name="PMC"
    )
    row = Row.objects.get(pk=link["uri"].split("://")[1])
    assert row.expires_at - row.created_at == timedelta(seconds=kept_results.PUBLIC_TTL_SECONDS)


def test_the_mcp_server_lists_the_kept_result_template():
    import asyncio

    from fastmcp import Client

    from connect_labs.mcp.server import mcp

    async def templates():
        async with Client(mcp) as client:
            return await client.list_resource_templates()

    listed = {t.name: t for t in asyncio.run(templates())}
    template = listed["labs_results"]
    assert template.uri_template == "labs-result://{id}{?page,page_size}"
    assert template.mime_type == "application/json"


# --- the owner too, now; the token's scope; a deactivated person --------------------------


def test_an_owner_who_lost_the_opportunities_no_longer_reads_the_rows(owner, connect):
    uri = _uri(owner, connect)
    connect["users"].discard(owner.pk)
    with pytest.raises(Refused) as refused:
        kept_results.read(uri, reader=owner.pk)
    assert refused.value.code == "forbidden"


def test_a_yes_is_kept_a_minute_so_paging_does_not_ask_connect_each_time(owner, connect, monkeypatch):
    uri = _uri(owner, connect)
    asked = []
    real = kept_results._could_run
    monkeypatch.setattr(kept_results, "_could_run", lambda u, r: asked.append(1) or real(u, r))
    for page in (1, 2, 3):
        kept_results.read_as(f"{uri}?page={page}&page_size=20", owner.pk, _token(owner))
    assert len(asked) == 1


def test_read_as_judges_once(owner, connect, monkeypatch):
    uri = _uri(owner, connect)
    calls = []
    real = kept_results.read_verdict
    monkeypatch.setattr(kept_results, "read_verdict", lambda *a: calls.append(1) or real(*a))
    kept_results.read_as(uri, owner.pk, _token(owner))
    assert len(calls) == 1


def test_a_token_reads_back_only_what_a_tool_in_its_scope_keeps(owner, connect, monkeypatch):
    from connect_labs.mcp import server

    uri = _uri(owner, connect)
    kept = Row.objects.get()
    monkeypatch.setattr(server, "allowed_tools", lambda token: frozenset({"marketplace_orgs_get"}))
    assert "data_query" in kept_results.scope_denial(kept, _token(owner))
    with pytest.raises(Refused):
        kept_results.read_as(uri, owner.pk, _token(owner))
    monkeypatch.setattr(server, "allowed_tools", lambda token: frozenset({"data_query"}))
    assert kept_results.scope_denial(kept, _token(owner)) is None
    monkeypatch.setattr(server, "allowed_tools", lambda token: None)
    assert kept_results.scope_denial(kept, _token(owner)) is None


def test_a_deactivated_persons_pat_opens_nothing(owner):
    from connect_labs.mcp.server import _verify_pat_sync

    raw = _pat(owner)
    assert _verify_pat_sync(raw) is not None
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    assert MCPAccessToken.verify(raw) is None and _verify_pat_sync(raw) is None


@pytest.mark.django_db(transaction=True)
def test_an_expired_result_says_so_in_its_errors_data(owner, connect):
    uri = _uri(owner, connect)
    Row.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    refused = read_resource(_pat(owner), uri)
    assert refused.error.data == {"code": "expired"} and str(refused).startswith("expired: ")


def test_when_connect_cannot_answer_the_owner_is_told_so_not_that_it_is_not_theirs(owner, connect, monkeypatch):
    from django.core.cache import cache

    from connect_labs.audit_trail.models import AuditEvent

    uri = _uri(owner, connect)
    cache.clear()

    def down(caller, ids):
        raise ScopeError("scopes_unavailable", "Connect did not answer")

    monkeypatch.setattr(scope, "resolve", down)
    with pytest.raises(Refused) as refused:
        kept_results.read_as(uri, owner.pk, _token(owner))
    assert refused.value.code == "unavailable" and refused.value.message == kept_results.UNAVAILABLE
    assert "not yours" not in str(refused.value)
    event = AuditEvent.objects.filter(resource_type="labs_result", outcome="failure").latest("id")
    assert event.metadata["refused"] == "unavailable" and event.opportunity_id == OPP.id
    # Still refused, and not remembered: the next read asks again.
    assert kept_results.read_verdict(owner.pk, Row.objects.get()) == "unavailable"
    # Any other failure to ask counts the same; a "not held" is a no.
    monkeypatch.setattr(scope, "resolve", lambda caller, ids: (_ for _ in ()).throw(ConnectionError("reset")))
    assert kept_results.read_verdict(owner.pk, Row.objects.get()) == "unavailable"
    monkeypatch.setattr(scope, "resolve", lambda caller, ids: (_ for _ in ()).throw(ScopeError("not_held", "no")))
    assert kept_results.read_verdict(owner.pk, Row.objects.get()) == "forbidden"


def test_a_delegated_read_that_is_not_a_verified_view_counts_as_an_agent(owner, connect, monkeypatch):
    """Not only a read carrying `Canopy-Execution`: a delegated read with no statement, or
    a View statement that does not verify, is an agent's read -- for its page and its audit."""
    from connect_labs.mcp import execution

    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: True)
    uri = _uri(owner, connect)
    kept = Row.objects.get()
    assert kept.sensitive is False
    delegated = _token(owner, auth_method="delegated")
    for view in (execution.CallView(refused="no_statement"), execution.CallView(refused="bad_signature")):
        monkeypatch.setattr(execution, "view_for_this_request", lambda *a, _v=view, **k: _v)
        denied, stated = kept_results.read_judgement(kept, delegated, uri)
        assert denied is None and stated["agent"] is True and stated["view"] == view.audit()
    # A person's own client (not delegated) is nobody's agent.
    assert kept_results.read_judgement(kept, _token(owner), uri) == (None, {})


def test_every_link_labs_returns_is_one_canopy_keeps_in_a_sensitive_conversation(owner):
    """The id is exactly the declared length, the mimeType one canopy allows, so the
    links survive canopy's check (`canopy_sdk.resources.validate_link`) -- and nothing longer would."""
    from canopy_sdk import resources
    from canopy_sdk.django import conf

    for _ in range(50):
        link = kept_results.save(owner, kept_results.KIND_PMC, {"x": 1}, access={"sensitive": False}, name="r")
        assert resources.validate_link(link, conf.resource_id_lengths(), keep_name=False) is not None
    assert resources.validate_link({**link, "uri": link["uri"] + "x"}, conf.resource_id_lengths()) is None
