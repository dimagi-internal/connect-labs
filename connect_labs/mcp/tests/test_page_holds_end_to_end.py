"""The agent on a page reads only what the page shows (``labsdata/page_holds.py``).

Through labs' real page, mint, ``/o/token/`` and MCP, as canopy calls them: the Data page
signs its selection onto the panel's token URL, labs' mint records it against the ID-JAG,
and every ``data_*`` call made with the token redeemed from that ID-JAG is held to it --
whatever the agent passes, and whatever else the visitor holds.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from canopy_sdk import contract
from django.test import RequestFactory

from connect_labs.labs.access import scopes
from connect_labs.labsdata import scope
from connect_labs.labsdata.scope import Opportunity
from connect_labs.mcp.tests import test_canopy_panel_end_to_end as _panel_e2e
from connect_labs.mcp.tests import test_labsdata_end_to_end as _data_e2e
from connect_labs.mcp.tests.test_canopy_panel_end_to_end import _mint, _redeem, _request
from connect_labs.mcp.tests.test_delegation import _run_mcp

enabled = _panel_e2e.enabled
visitor = _panel_e2e.visitor
labsdata = _data_e2e.labsdata
real_visits = _data_e2e.real_visits

REAL = _data_e2e.REAL
#: Another opportunity the visitor holds, which the page does not show.
OTHER = Opportunity(id=962, name="Opp Other", llo="LLO Other")
HELD = {REAL.id: REAL, OTHER.id: OTHER}


def _render_data_page(visitor, path=f"/labs/data/?opps={REAL.id}"):
    from connect_labs.labsdata.views import DataView

    request = _request(path, visitor, {})
    with mock.patch.object(scope, "directory", return_value=HELD):
        response = DataView.as_view()(request)
        response.render()
    panel = response.context_data["canopy_panel"]
    assert panel and panel["ready"]
    return panel


def _grant(canopy_redeem, visitor, panel=None) -> str:
    arrival = _mint(panel or _render_data_page(visitor), visitor)
    return _redeem(canopy_redeem, arrival[contract.ARRIVAL_ID_JAG_FIELD])


def _resolve(caller, ids):
    return [HELD[int(i)] for i in ids]


def _call(client_creds, token, tool, arguments):
    """One tool call through labs' real MCP as canopy makes it (DPoP, a Canopy-Execution
    statement); returns ``(ok, text)``."""
    from config.asgi import build_application

    add_headers = _data_e2e._headers(client_creds, token, _data_e2e.SENSITIVE_DECLARED)

    async def work(mcp_client):
        result = await mcp_client.call_tool(tool, arguments, raise_on_error=False)
        text = " ".join(getattr(c, "text", "") or "" for c in result.content)
        return (not result.is_error), text

    with (
        mock.patch("connect_labs.mcp.tools.labsdata._caller", return_value=scopes.Caller(access_token="t")),
        mock.patch.object(scope, "resolve", side_effect=_resolve),
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=False),
    ):
        return _run_mcp(build_application(), {"Authorization": f"DPoP {token}"}, add_headers, work)


def _query(client_creds, token, opportunity_ids):
    return _call(
        client_creds, token, "data_query", {"opportunity_ids": opportunity_ids, "sql": "SELECT COUNT(*) FROM visits"}
    )


@pytest.mark.django_db(transaction=True)
def test_the_pages_selection_reads_and_anything_else_the_visitor_holds_is_refused(
    labsdata, canopy_redeem, visitor, real_visits
):
    token = _grant(canopy_redeem, visitor)
    ok, text = _query(labsdata, token, [REAL.id])
    assert ok, text
    ok, text = _query(labsdata, token, [REAL.id, OTHER.id])
    assert not ok
    assert "outside_page" in text or "not part of this page" in text
    assert str(OTHER.id) in text and "Data page" in text


@pytest.mark.django_db(transaction=True)
def test_a_grant_whose_page_was_not_recorded_reads_nothing(labsdata, canopy_redeem, visitor, real_visits):
    """The SDK's own mint (no record of the page): fail closed, even on the page's selection."""
    from canopy_sdk.django import views as sdk_views

    panel = _render_data_page(visitor)
    sent = {}

    def mint(config, payload, **kw):
        sent.update(payload)
        return {"token": "canopy-contact-token", "expires_at": "", "kind": "contact"}

    request = RequestFactory().post(panel["options"]["tokenUrl"])
    request.user = visitor
    with mock.patch.object(sdk_views, "mint_contact_token", mint):
        assert sdk_views.panel_token(request).status_code == 200
    token = _redeem(canopy_redeem, sent[contract.ARRIVAL_ID_JAG_FIELD])
    ok, text = _query(labsdata, token, [REAL.id])
    assert not ok and "did not record what it shows" in text


@pytest.mark.django_db(transaction=True)
def test_a_selection_signed_for_another_page_records_nothing(labsdata, canopy_redeem, visitor, real_visits):
    """Lifting one page's signed selection onto another page's token URL gets no hold."""
    import time

    first = _render_data_page(visitor)
    # A later drawing (a page token names its second): another page token.
    with mock.patch("canopy_sdk.host.pages.time.time", return_value=time.time() + 5):
        second = _render_data_page(visitor, f"/labs/data/?opps={OTHER.id}")
    assert first["page_token"] != second["page_token"]
    lifted = first["options"]["tokenUrl"].split("&holds=")[1]
    url = second["options"]["tokenUrl"].split("&holds=")[0] + "&holds=" + lifted
    token = _grant(canopy_redeem, visitor, {**second, "options": {**second["options"], "tokenUrl": url}})
    ok, text = _query(labsdata, token, [REAL.id])
    assert not ok and "did not record what it shows" in text


@pytest.mark.django_db(transaction=True)
def test_the_person_widens_the_data_page_and_its_agent_follows(labsdata, canopy_redeem, visitor, real_visits):
    """The plain Data page: the person adds an opportunity in their own session; the agent
    may then read it -- and the agent has no way to do that itself."""
    from connect_labs.labsdata.views import PageHoldsView

    panel = _render_data_page(visitor)
    token = _grant(canopy_redeem, visitor, panel)
    assert not _query(labsdata, token, [OTHER.id])[0]

    request = RequestFactory().post(
        "/labs/data/api/page-holds/",
        data=json.dumps(
            {
                "page_token": panel["page_token"],
                "page_holds": panel["page_holds"],
                "opportunity_ids": [REAL.id, OTHER.id],
            }
        ),
        content_type="application/json",
    )
    request.user = visitor
    request.session = {}
    with (
        mock.patch.object(scope, "resolve", side_effect=_resolve),
        mock.patch.object(scope, "directory", return_value=HELD),
    ):
        response = PageHoldsView.as_view()(request)
    assert response.status_code == 200, response.content
    assert json.loads(response.content)["opportunity_ids"] == [REAL.id, OTHER.id]
    ok, text = _query(labsdata, token, [OTHER.id])
    assert ok, text


@pytest.mark.django_db(transaction=True)
def test_only_the_persons_own_data_page_token_can_be_updated(labsdata, visitor):
    from connect_labs.labsdata.views import PageHoldsView
    from connect_labs.users.models import User

    panel = _render_data_page(visitor)
    someone = User.objects.create(username="someone-else", email="s@example.invalid")
    request = RequestFactory().post(
        "/labs/data/api/page-holds/",
        data=json.dumps({"page_token": panel["page_token"], "page_holds": panel["page_holds"], "opportunity_ids": []}),
        content_type="application/json",
    )
    request.user = someone
    request.session = {}
    response = PageHoldsView.as_view()(request)
    assert response.status_code == 403
