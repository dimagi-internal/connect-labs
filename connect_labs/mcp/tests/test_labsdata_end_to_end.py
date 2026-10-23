"""labsdata end to end: the Data page's grant, and production rows only on a call canopy vouched for.

Through labs' real code and the real canopy SDK (>= 0.13): the Data page mints a grant
for ``labsdata`` on sensitive-data runners, canopy redeems it at ``/o/token/`` and calls labs' MCP
as the visitor over DPoP -- and with a ``Canopy-Execution`` statement signed by canopy's
client key (``consumer.execution_statement``) the call gets production rows; with none, or with
one that does not clear it, the rows are withheld. The person's own View reads them back only
with canopy's ``Canopy-View`` (``consumer.view_statement``) for that result and that person.
canopy is stubbed only where it is canopy (the contact-token mint, its client documents).

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest import mock

import pytest
from canopy_sdk import consumer, contract
from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.labsdata import scope
from connect_labs.labsdata.scope import Opportunity
from connect_labs.mcp.tests import test_canopy_panel_end_to_end as _panel_e2e
from connect_labs.mcp.tests.test_canopy_panel_end_to_end import _mint, _redeem, _request, _scopes
from connect_labs.mcp.tests.test_delegation import RESOURCE, _run_mcp

# The panel end-to-end test's fixtures: labs with the grant on, and a signed-in visitor.
enabled = _panel_e2e.enabled
visitor = _panel_e2e.visitor

REAL = Opportunity(id=961, name="Opp Real", llo="LLO Real")
SENSITIVE_DECLARED = {
    "agent": "labsdata",
    "capability": "data",
    "declaration": {"data": "sensitive", "capability": "data", "declared_by": "an author", "declared_at": "t"},
    "runner": {
        "id": 1,
        "name": "sensitive-data-box",
        "flags": [{"flag": "sensitive_data", "declared_by": "a person", "declared_at": "t"}],
    },
    "runner_posture": {"data_mode": "sensitive_data", "config_hash": "h"},
    "requirements": ["sensitive_data"],
    "turn": "turn-1",
    "session": "session-1",
}


@pytest.fixture
def labsdata(enabled, canopy_agents):
    canopy_agents("ace", sensitive_data=["labsdata"])
    return enabled


@pytest.fixture
def real_visits(db):
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=REAL.id,
                pipeline_id=USER_VISITS_RAW_SLOT,
                visit_count=3,
                expires_at=timezone.now() + timedelta(hours=1),
                visit_id=f"rv{n}",
                username="flw",
                entity_id=f"c{n}",
                visit_date=date(2026, 9, 1),
                status="approved",
                form_json={"form": {"q": "invented"}},
            )
            for n in range(3)
        ]
    )


def _data_page_grant(canopy_redeem, visitor):
    """Render the Data page, mint its panel token as the browser does, redeem as canopy."""
    from connect_labs.labsdata.views import DataView

    request = _request(f"/labs/data/?opps={REAL.id}", visitor, {})
    with mock.patch.object(scope, "directory", return_value={REAL.id: REAL}):
        response = DataView.as_view()(request)
        response.render()
    panel = response.context_data["canopy_panel"]
    assert panel and panel["ready"]
    arrival = _mint(panel, visitor)
    id_jag = arrival[contract.ARRIVAL_ID_JAG_FIELD]
    assert _scopes(id_jag) == {"data:read"}
    # The page chose its own agent and required sensitive-data runners (signed into the assertion).
    assert "labsdata" in str(arrival)
    return _redeem(canopy_redeem, id_jag)


def _view_statement(client_creds, token, *, subject, uri, now=None, key=None, rpc=None):
    """canopy's ``Canopy-View``: the person's own View reading ``uri`` (consumer.view_statement),
    bound to the request ``rpc`` names (by default, the read of ``uri``)."""
    return consumer.view_statement(
        key or client_creds.client_key,
        client_creds.client_id,
        RESOURCE,
        subject=str(subject),
        uri=uri,
        session="session-1",
        jkt=consumer.dpop_jkt(client_creds.dpop_key),
        access_token=token,
        viewer="user:7",
        agent="labsdata",
        rpc=rpc or contract.rpc_digest("resources/read", {"uri": uri}),
        now=now,
    )


def _headers(client_creds, token, facts, view=None, rpc=None):
    """canopy's gateway, per request: DPoP, and the statement for THIS JSON-RPC request
    (its ``rpc`` digest, from the body -- or ``rpc``, to lift one onto another call)."""

    async def add_headers(request):
        request.headers["DPoP"] = client_creds.dpop_proof(request.method, RESOURCE, access_token=token)
        bound = rpc or contract.rpc_digest_of_body(request.content)
        if view is not None:
            # A fresh statement per request, as canopy's gateway signs one per request --
            # unless the test hands one in to spend twice.
            request.headers[contract.VIEW_HEADER] = view.get("raw") or _view_statement(
                client_creds, token, **{"rpc": bound, **view}
            )
        if facts is not None:
            request.headers[contract.EXECUTION_HEADER] = consumer.execution_statement(
                client_creds.client_key,
                client_creds.client_id,
                RESOURCE,
                facts,
                jkt=consumer.dpop_jkt(client_creds.dpop_key),
                access_token=token,
                rpc=bound,
            )

    return add_headers


def _query(client_creds, token, facts, rpc=None):
    """Call data_query through labs' real MCP as canopy does: DPoP on every request,
    and -- when ``facts`` -- a fresh Canopy-Execution statement on each."""
    from config.asgi import build_application

    add_headers = _headers(client_creds, token, facts, rpc=rpc)

    async def work(mcp_client):
        return await mcp_client.call_tool(
            "data_query", {"opportunity_ids": [REAL.id], "sql": "SELECT visit_id FROM visits ORDER BY visit_id"}
        )

    with (
        mock.patch("connect_labs.mcp.tools.labsdata._caller", return_value=scopes.Caller(access_token="t")),
        mock.patch.object(scope, "resolve", return_value=[REAL]),
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=False),
    ):
        return _run_mcp(build_application(), {"Authorization": f"DPoP {token}"}, add_headers, work)


def _read(client_creds, token, facts, uri, view=None):
    """``resources/read`` of a kept result through labs' real MCP, as canopy's gateway
    does: ``facts`` for an agent's ``site_read``; ``view`` (``Canopy-View``'s subject, uri,
    ...) for the person's own View; neither for a call canopy vouches nothing for."""
    from fastmcp.exceptions import ClientError

    from config.asgi import build_application

    async def work(mcp_client):
        try:
            return await mcp_client.read_resource(uri)
        except (ClientError, Exception) as e:  # noqa: BLE001 -- the refusal is the result
            return e

    # The reader still holds the opportunity (kept_results.may_read asks again, owner too).
    with (
        mock.patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="t"),
        mock.patch.object(scope, "resolve", return_value=[REAL]),
    ):
        return _run_mcp(
            build_application(), {"Authorization": f"DPoP {token}"}, _headers(client_creds, token, facts, view), work
        )


@pytest.mark.django_db(transaction=True)
def test_a_vouched_sensitive_data_call_reads_real_rows_and_is_audited(labsdata, canopy_redeem, visitor, real_visits):
    from connect_labs.mcp.models import MCPAuditLog

    token = _data_page_grant(canopy_redeem, visitor)
    called = _query(labsdata, token, SENSITIVE_DECLARED)
    result = called.structured_content
    assert not result.get("withheld"), result
    # A canopy agent gets the facts and a sample -- here the whole three rows -- and the link.
    assert [r["visit_id"] for r in result["sample_rows"]] == ["rv0", "rv1", "rv2"]
    assert result["row_count"] == 3 and result["sample_is_partial"] is False and "rows" not in result
    links = [c for c in called.content if getattr(c, "type", "") == "resource_link"]
    assert [str(link.uri) for link in links] == [result["result_uri"]]
    assert result["result_uri"].startswith("labs-result://")
    row = MCPAuditLog.objects.filter(tool_name="data_query").latest("id")
    assert row.execution["agent"] == "labsdata"
    assert row.execution["runner"]["flags"] == ["sensitive_data"]
    assert row.execution["declaration"]["declared_by"] == "an author"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "facts, refused",
    [
        (None, "no_statement"),
        ({**SENSITIVE_DECLARED, "declaration": None}, None),  # not declared to handle sensitive data
        (
            {**SENSITIVE_DECLARED, "runner": {"id": 2, "name": "plain", "flags": []}},
            None,
        ),  # not a sensitive-data runner
        ({**SENSITIVE_DECLARED, "agent": "ace"}, None),  # an agent labs has not allowed
    ],
    ids=["no-statement", "undeclared", "not-sensitive-data", "other-agent"],
)
def test_anything_short_of_that_withholds_the_rows(labsdata, canopy_redeem, visitor, real_visits, facts, refused):
    from connect_labs.mcp.models import MCPAuditLog

    token = _data_page_grant(canopy_redeem, visitor)
    result = _query(labsdata, token, facts).structured_content
    assert result["withheld"] is True and "rows" not in result
    if refused:
        assert MCPAuditLog.objects.filter(tool_name="data_query").latest("id").execution == {"refused": refused}


def _kept(labsdata, canopy_redeem, visitor):
    token = _data_page_grant(canopy_redeem, visitor)
    return token, _query(labsdata, token, SENSITIVE_DECLARED).structured_content["result_uri"]


def _rows(contents) -> list[str]:
    import json

    (content,) = contents
    return [r[0] for r in json.loads(content.text)["rows"]]


@pytest.mark.django_db(transaction=True)
def test_the_cleared_agent_reads_the_kept_rows_back_a_page_at_a_time(labsdata, canopy_redeem, visitor, real_visits):
    """canopy's ``site_read``: the same visitor, the same cleared agent, a fresh statement."""
    from connect_labs.audit_trail.models import AuditEvent

    token, uri = _kept(labsdata, canopy_redeem, visitor)
    contents = _read(labsdata, token, SENSITIVE_DECLARED, f"{uri}?page=2&page_size=2")
    assert _rows(contents) == ["rv2"]
    meta = contents[0].meta["labs/result"]
    assert (meta["page"], meta["pages"], meta["row_count"], meta["snapshot"]) == (2, 2, 3, True)
    read = AuditEvent.objects.filter(resource_type="labs_result").latest("id")
    assert (read.action, read.opportunity_id, read.record_count) == ("read", REAL.id, 1)


@pytest.mark.django_db(transaction=True)
def test_the_persons_own_view_reads_what_the_cleared_agent_was_given(labsdata, canopy_redeem, visitor, real_visits):
    """A View's read carries canopy's signed ``Canopy-View`` for this result and this person:
    no agent is acting, the person is looking at a result that already reached a cleared
    agent through canopy. Every page of it, each request with its own statement."""
    from connect_labs.audit_trail.models import AuditEvent

    token, uri = _kept(labsdata, canopy_redeem, visitor)
    assert _rows(_read(labsdata, token, None, uri, view={"subject": visitor.pk, "uri": uri})) == ["rv0", "rv1", "rv2"]
    paged = f"{uri}?page=2&page_size=2"
    assert _rows(_read(labsdata, token, None, paged, view={"subject": visitor.pk, "uri": uri})) == ["rv2"]
    read = AuditEvent.objects.filter(resource_type="labs_result").latest("id")
    assert (read.action, read.opportunity_id, read.record_count) == ("read", REAL.id, 1)


@pytest.mark.django_db(transaction=True)
def test_a_delegated_read_with_no_statement_is_refused(labsdata, canopy_redeem, visitor, real_visits):
    """The regression: a delegated read carrying no statement at all -- an agent that
    omits its ``Canopy-Execution``, or anything else that makes canopy call without one --
    is NOT the person's View. It used to be read as one."""
    token, uri = _kept(labsdata, canopy_redeem, visitor)
    refused = _read(labsdata, token, None, uri)
    assert isinstance(refused, Exception) and "forbidden" in str(refused) and "sensitive" in str(refused)
    assert "no_statement" in str(refused)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "view, code",
    [
        ({"uri": "labs-result://another-result"}, "wrong_resource"),
        ({"subject": "999999"}, "wrong_person"),
        ({"now": time.time() - 3600}, "expired"),
        ({"key": "stranger"}, "bad_signature"),
        ({"rpc": contract.rpc_digest("resources/read", {"uri": "labs-result://x?page=9"})}, "wrong_request"),
    ],
    ids=["another-uri", "another-person", "expired", "not-canopys-key", "another-request"],
)
def test_a_view_statement_that_does_not_fit_is_refused(labsdata, canopy_redeem, visitor, real_visits, view, code):
    from canopy_sdk.keys import generate_private_key

    token, uri = _kept(labsdata, canopy_redeem, visitor)
    stated = {"subject": visitor.pk, "uri": uri, **view}
    if stated.get("key") == "stranger":
        stated["key"] = generate_private_key("EdDSA")
    refused = _read(labsdata, token, None, uri, view=stated)
    assert isinstance(refused, Exception) and "forbidden" in str(refused)
    assert code in str(refused) or (code == "bad_signature" and "unknown_key" in str(refused))


@pytest.mark.django_db(transaction=True)
def test_a_statement_lifted_onto_another_call_withholds_the_rows(labsdata, canopy_redeem, visitor, real_visits):
    """A cleared agent's statement, made for one call, does not clear another on its token."""
    token = _data_page_grant(canopy_redeem, visitor)
    other = contract.rpc_digest("tools/call", {"name": "data_query", "arguments": {"sql": "SELECT 1"}})
    result = _query(labsdata, token, SENSITIVE_DECLARED, rpc=other).structured_content
    assert result["withheld"] is True and "rows" not in result


@pytest.mark.django_db(transaction=True)
def test_a_view_statement_is_spent_once(labsdata, canopy_redeem, visitor, real_visits):
    token, uri = _kept(labsdata, canopy_redeem, visitor)
    raw = _view_statement(labsdata, token, subject=visitor.pk, uri=uri)
    assert _rows(_read(labsdata, token, None, uri, view={"raw": raw})) == ["rv0", "rv1", "rv2"]
    refused = _read(labsdata, token, None, uri, view={"raw": raw})
    assert isinstance(refused, Exception) and "replayed" in str(refused)


@pytest.mark.django_db(transaction=True)
def test_an_uncleared_agent_cannot_pass_itself_off_as_the_view(labsdata, canopy_redeem, visitor, real_visits):
    """An agent's call is judged as the agent: a view statement beside its execution
    statement does not make it the person."""
    token, uri = _kept(labsdata, canopy_redeem, visitor)
    refused = _read(
        labsdata, token, {**SENSITIVE_DECLARED, "agent": "ace"}, uri, view={"subject": visitor.pk, "uri": uri}
    )
    assert isinstance(refused, Exception) and "forbidden" in str(refused)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "facts",
    [
        {**SENSITIVE_DECLARED, "agent": "ace"},  # an agent labs has not allowed
        {**SENSITIVE_DECLARED, "declaration": None},  # not declared to handle sensitive data
    ],
    ids=["other-agent", "undeclared"],
)
def test_an_uncleared_agent_cannot_read_sensitive_rows_back(labsdata, canopy_redeem, visitor, real_visits, facts):
    token, uri = _kept(labsdata, canopy_redeem, visitor)
    refused = _read(labsdata, token, facts, uri)
    assert isinstance(refused, Exception) and "forbidden" in str(refused) and "sensitive" in str(refused)
