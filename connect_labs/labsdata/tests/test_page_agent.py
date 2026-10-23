"""The Data page offers the agent for data work (a PHI-safe one, labsdata) read-only on ZDR
runners; with none, the default agent. Every other page is unchanged."""

from unittest import mock

import pytest

from connect_labs.labs import canopy_panel
from connect_labs.labsdata import scope
from connect_labs.labsdata.scope import Opportunity

OPP = Opportunity(id=951, name="Opp P", llo="LLO P")


def test_with_no_agent_for_data_work_the_data_pages_offer_the_default_agent(settings):
    settings.CANOPY_DATA_AGENT_SLUG = ""
    here = canopy_panel.data_page_agent()
    assert here.ok and here.page_agent == canopy_panel.PageAgent()


def test_a_phi_safe_agent_labs_shows_is_offered_on_the_data_pages_on_zdr_runners(settings):
    settings.CANOPY_DATA_AGENT_SLUG = "labsdata"  # shown, and the fake canopy declares it PHI-safe
    here = canopy_panel.data_page_agent()
    assert here.ok and here.read_only
    assert here.page_agent.agent == "labsdata"
    assert here.page_agent.runner_requirements == ("zdr",)
    assert here.page_agent.panel == {"mode": "docked", "width": 560, "expandable": True}


def test_labs_zdr_requirement_counts_toward_clearance_but_canopy_must_still_vouch(settings):
    settings.CANOPY_PHI_AGENTS = ["labsdata"]
    settings.CANOPY_RUNNER_REQUIREMENTS = []  # the site does not require ZDR; a read-only page does
    without_page = canopy_panel.real_data_clearance("labsdata")
    assert not without_page.granted and "ZDR" in without_page.reason
    with mock.patch.object(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", False):  # an SDK that cannot verify
        with_page = canopy_panel.real_data_clearance("labsdata", ("zdr",))
    assert not with_page.granted and "vouch" in with_page.reason
    with mock.patch.object(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True):
        assert canopy_panel.real_data_clearance("labsdata", ("zdr",)).granted
        # The default agent is not cleared just because the data agent is.
        assert not canopy_panel.real_data_clearance("ace", ("zdr",)).granted


def test_panel_signs_the_pages_agent_and_requirements_into_the_page():
    captured = {}

    def fake_panel_context(request, **kwargs):
        captured.update(kwargs)
        return {"ready": True}

    page = canopy_panel.PageAgent(agent="labsdata", runner_requirements=("zdr",), panel={"mode": "docked"})
    available = canopy_panel.Availability(True, "labsdata", True, (), page)
    with mock.patch("canopy_sdk.django.pages.panel_context", fake_panel_context):
        canopy_panel.panel(None, reads=canopy_panel.NO_VISIT_DATA, available=available, resource="labs-data://")
    assert captured["agent"] == "labsdata"
    assert captured["runner_requirements"] == ["zdr"]
    assert captured["panel"] == {"mode": "docked"}

    captured.clear()
    with mock.patch("canopy_sdk.django.pages.panel_context", fake_panel_context):
        canopy_panel.panel(None, reads=canopy_panel.NO_VISIT_DATA, resource="labs-data://")
    assert "agent" not in captured and "runner_requirements" not in captured and "panel" not in captured


def test_no_panel_at_all_when_canopy_has_said_nothing(fake_canopy):
    fake_canopy.set(None)
    with mock.patch("canopy_sdk.django.pages.panel_context") as sdk:
        assert canopy_panel.panel(None, reads=canopy_panel.NO_VISIT_DATA, resource="labs-data://") is None
    sdk.assert_not_called()


@pytest.mark.django_db
def test_the_data_page_asks_for_labsdata_when_configured(client, django_user_model, settings):
    settings.CANOPY_DATA_AGENT_SLUG = "labsdata"
    user = django_user_model.objects.create_user(username="labsdata-page-agent", password="x")
    client.force_login(user)
    seen = {}

    def fake_panel(request, *, reads, available=None, **kwargs):
        seen["available"] = available
        return None

    with (
        mock.patch.object(scope, "directory", return_value={OPP.id: OPP}),
        mock.patch.object(canopy_panel, "panel", fake_panel),
    ):
        assert client.get(f"/labs/data/?opps={OPP.id}").status_code == 200
    assert seen["available"].page_agent.agent == "labsdata"
    assert seen["available"].page_agent.runner_requirements == ("zdr",)
