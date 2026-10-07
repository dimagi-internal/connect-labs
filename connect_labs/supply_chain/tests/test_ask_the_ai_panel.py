"""Ask an AI about a tender's quotes: the canopy panel on the tender and comparison pages."""

from unittest import mock

import pytest
from django.test import RequestFactory

from connect_labs.labs import canopy
from connect_labs.supply_chain.procurement import views


def test_the_tender_and_comparison_pages_grant_only_supply_reads():
    for page in ("supply_chain:procurement_tender_detail", "supply_chain:procurement_comparison"):
        assert canopy.PAGE_SCOPES[page] == ("supply:read",)
    tools = canopy.SCOPE_TOOLS["supply:read"]
    assert "supply_chain_tender_compare" in tools and "supply_chain_quote_list" in tools
    # Reads only: nothing that records, corrects, awards or deletes.
    assert not [t for t in tools if any(w in t for w in ("record", "correct", "update", "create", "delete", "void"))]


def test_every_supply_read_tool_is_a_registered_read_operation():
    from connect_labs.supply_chain.operations import get_operation

    for tool in canopy.SCOPE_TOOLS["supply:read"]:
        operation = get_operation(tool.removeprefix("supply_chain_"))
        assert not operation.is_write, tool


@pytest.mark.django_db
def test_the_panel_names_the_tender_and_its_quotes_not_their_figures(django_user_model):
    request = RequestFactory().get("/supply/procurement/tenders/7/compare/?program_id=10690")
    request.user = django_user_model.objects.create_user(username="sophie", password="x")
    with (
        mock.patch.object(views, "_access", return_value=mock.Mock(program_id=10690)),
        mock.patch("canopy_sdk.django.pages.panel_context", side_effect=lambda request, **kw: kw) as built,
    ):
        panel = views._quotes_panel(request, 7, [31, 32, None], commodity="rutf")
    assert built.called
    assert panel["resource"] == "labs-supply://tenders/7"
    assert panel["backing_tool"] == "supply_chain_tender_compare"
    assert panel["visible_ids"] == ["31", "32"]
    assert panel["filters"] == {"tender_id": 7, "program_id": 10690, "commodity_slug": "rutf"}


@pytest.mark.django_db
def test_no_panel_on_a_page_showing_the_past_or_for_a_visitor_not_signed_in(django_user_model):
    from django.contrib.auth.models import AnonymousUser

    past = RequestFactory().get("/supply/procurement/tenders/7/?as_of=2026-09-01")
    past.user = django_user_model.objects.create_user(username="sophie", password="x")
    past.supply_as_of = "2026-09-01"
    anonymous = RequestFactory().get("/supply/procurement/tenders/7/")
    anonymous.user = AnonymousUser()
    assert views._quotes_panel(past, 7, [31]) is None
    assert views._quotes_panel(anonymous, 7, [31]) is None
