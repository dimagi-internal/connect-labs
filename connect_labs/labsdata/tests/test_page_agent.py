"""The Data page offers labsdata on ZDR runners when labs names it; every other page is unchanged."""

from unittest import mock

import pytest

from connect_labs.labs import canopy_panel
from connect_labs.labsdata import scope
from connect_labs.labsdata.scope import Opportunity

OPP = Opportunity(id=951, name="Opp P", llo="LLO P")


def test_unset_the_data_pages_offer_the_sites_agent(settings):
    settings.CANOPY_DATA_AGENT_SLUG = ""
    assert canopy_panel.data_agent() == canopy_panel.PageAgent()


def test_set_the_data_pages_offer_labsdata_on_zdr_runners(settings):
    settings.CANOPY_DATA_AGENT_SLUG = "labsdata"
    page = canopy_panel.data_agent()
    assert page.agent == "labsdata"
    assert page.runner_requirements == ("zdr",)
    assert page.panel == {"mode": "docked", "width": 560, "expandable": True}


def test_a_pages_zdr_requirement_counts_toward_clearance_but_canopy_must_still_vouch(settings):
    settings.CANOPY_PHI_AGENTS = ["labsdata"]
    settings.CANOPY_RUNNER_REQUIREMENTS = []  # the site does not require ZDR; the data page does
    without_page = canopy_panel.real_data_clearance("labsdata")
    assert not without_page.granted and "ZDR" in without_page.reason
    with mock.patch.object(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", False):  # an SDK that cannot verify
        with_page = canopy_panel.real_data_clearance("labsdata", ("zdr",))
    assert not with_page.granted and "vouch" in with_page.reason
    with mock.patch.object(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True):
        assert canopy_panel.real_data_clearance("labsdata", ("zdr",)).granted
        # The site's agent is not cleared just because the data agent is.
        assert not canopy_panel.real_data_clearance("ace", ("zdr",)).granted


def test_panel_signs_the_pages_agent_and_requirements_into_the_page():
    captured = {}

    def fake_panel_context(request, **kwargs):
        captured.update(kwargs)
        return {"ready": True}

    page = canopy_panel.PageAgent(agent="labsdata", runner_requirements=("zdr",), panel={"mode": "docked"})
    with mock.patch("canopy_sdk.django.pages.panel_context", fake_panel_context):
        canopy_panel.panel(None, reads=canopy_panel.NO_VISIT_DATA, page_agent=page, resource="labs-data://")
    assert captured["agent"] == "labsdata"
    assert captured["runner_requirements"] == ["zdr"]
    assert captured["panel"] == {"mode": "docked"}

    captured.clear()
    with mock.patch("canopy_sdk.django.pages.panel_context", fake_panel_context):
        canopy_panel.panel(None, reads=canopy_panel.NO_VISIT_DATA, resource="labs-data://")
    assert "agent" not in captured and "runner_requirements" not in captured and "panel" not in captured


@pytest.mark.django_db
def test_the_data_page_asks_for_labsdata_when_configured(client, django_user_model, settings):
    settings.CANOPY_DATA_AGENT_SLUG = "labsdata"
    user = django_user_model.objects.create_user(username="labsdata-page-agent", password="x")
    client.force_login(user)
    seen = {}

    def fake_panel(request, *, reads, page_agent=None, **kwargs):
        seen["page_agent"] = page_agent
        return None

    with (
        mock.patch.object(scope, "directory", return_value={OPP.id: OPP}),
        mock.patch.object(canopy_panel, "panel", fake_panel),
    ):
        assert client.get(f"/labs/data/?opps={OPP.id}").status_code == 200
    assert seen["page_agent"].agent == "labsdata"
    assert seen["page_agent"].runner_requirements == ("zdr",)
