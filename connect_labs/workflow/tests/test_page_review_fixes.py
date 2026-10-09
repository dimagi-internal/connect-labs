"""Fixes from the independent review of #2418/#2419/#2422 (2026-10-09): pages and workflow sources.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.http import Http404
from django.test import RequestFactory

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.workflow.tests import test_page_mode as pm

pytestmark = pytest.mark.django_db

member_of_acme = pm.member_of_acme
VICTIM_OPP = 10999


def _labs_only_victim(monkeypatch):
    """Opportunity 10999 is a labs-only opp someone else made; the viewer cannot use it."""
    from connect_labs.labs.synthetic import access

    monkeypatch.setattr(access, "is_labs_only_opportunity_id", lambda opp: int(opp) == VICTIM_OPP)
    monkeypatch.setattr(access, "is_labs_only_program_id", lambda prog: int(prog) == VICTIM_OPP)
    monkeypatch.setattr(access, "user_can_access_labs_only_opp", lambda user, opp: False)
    monkeypatch.setattr(access, "user_can_access_labs_only_program", lambda user, prog: False)


def test_a_workflow_source_cannot_read_a_labs_only_opp_the_viewer_cannot_use(monkeypatch):
    """S1-1, the reported exploit: a page declaring a source owned by a victim's labs-only
    opportunity read that opportunity's run name and snapshot summary from labs' own
    database, which has no Connect check behind it."""
    from connect_labs.workflow import workflow_sources

    _labs_only_victim(monkeypatch)
    with patch("connect_labs.workflow.workflow_sources.WorkflowDataAccess") as MockWDA:
        out = workflow_sources.read(
            pm._request(), {"alias": "loot", "workflow": 77, "read": "latest_run", "opportunity_id": VICTIM_OPP}
        )
    assert "cannot open it" in out["error"] and "latest_run" not in out
    MockWDA.assert_not_called()


def test_a_workflow_source_in_a_labs_only_programme_the_viewer_cannot_use_is_refused(monkeypatch):
    from connect_labs.workflow import workflow_sources

    _labs_only_victim(monkeypatch)
    with patch("connect_labs.workflow.workflow_sources.WorkflowDataAccess") as MockWDA:
        out = workflow_sources.read(pm._request(), {"alias": "x", "workflow": 77, "program_id": VICTIM_OPP})
    assert "error" in out
    MockWDA.assert_not_called()


def test_a_home_owned_by_a_labs_only_programme_the_viewer_cannot_use_is_not_served(member_of_acme, monkeypatch):
    """S1-2 (read side): a home fill naming someone else's labs-only programme 404s, nothing read."""
    _labs_only_victim(monkeypatch)
    service.update(
        "labs",
        Scope.of("organization", "acme"),
        {"home": {"fill": {"workflow": 47, "program_id": VICTIM_OPP}}},
        SYSTEM,
    )
    with pytest.raises(Http404):
        _, wda, _ = pm._serve("/labs/p/org/acme/", scope_type="org", scope_key="acme")


def test_the_home_still_opens_when_its_owner_is_usable(member_of_acme):
    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )
    response, _, _ = pm._serve("/labs/p/org/acme/", scope_type="org", scope_key="acme")
    assert response.status_code == 200


def test_the_overview_does_not_redirect_to_a_home_that_would_not_open(member_of_acme, monkeypatch):
    from connect_labs.labs.views import LabsOverviewView

    _labs_only_victim(monkeypatch)
    service.update(
        "labs",
        Scope.of("organization", "acme"),
        {"home": {"fill": {"workflow": 47, "program_id": VICTIM_OPP}}},
        SYSTEM,
    )
    named = pm._request(
        "/labs/overview/?organization_id=acme", context={"organization_slug": "acme", "organization_id": 5}
    )
    with patch("connect_labs.labs.views.LabsOverviewView.get_context_data", return_value={}):
        assert LabsOverviewView.as_view()(named).status_code == 200


def test_the_overview_does_not_redirect_an_auto_selected_org(member_of_acme):
    """The person holds only this organisation, so labs picked it: they did not come in for it."""
    from connect_labs.labs.context import AUTO_SELECTED_KEY
    from connect_labs.labs.views import LabsOverviewView

    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )
    request = pm._request(
        "/labs/overview/?organization_id=acme", context={"organization_slug": "acme", "organization_id": 5}
    )
    request.session[AUTO_SELECTED_KEY] = {"organization_id": "acme"}
    with patch("connect_labs.labs.views.LabsOverviewView.get_context_data", return_value={}):
        assert LabsOverviewView.as_view()(request).status_code == 200


def test_edit_page_follows_the_records_own_author_not_its_data(member_of_acme):
    from connect_labs.workflow.data_access import WorkflowDefinitionRecord
    from connect_labs.workflow.page_views import PageView

    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )

    def serve(record_user, data_user):
        definition = WorkflowDefinitionRecord(
            {"id": 47, "experiment": "workflow", "type": "workflow_definition", "opportunity_id": None,
             "program_id": 25, "username": record_user, "data": {**pm.PAGE, "username": data_user}}
        )  # fmt: skip
        request = pm._request("/labs/p/org/acme/", context={})
        with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
            MockWDA.return_value.get_definition.return_value = definition
            MockWDA.return_value.get_render_code.return_value = MagicMock(data={"component_code": "x"})
            MockWDA.return_value.get_workers.return_value = []
            response = PageView.as_view()(request, scope_type="org", scope_key="acme")
            response.render()
        return response.content.decode()

    assert 'data-testid="labs-page-edit"' not in serve(record_user="someone-else", data_user="amina")
    assert 'data-testid="labs-page-edit"' in serve(record_user="amina", data_user="someone-else")


def test_a_page_cannot_be_started_as_a_run():
    from connect_labs.workflow.views import start_run_api

    request = RequestFactory().post("/labs/workflow/api/47/start-run/", {"program_id": "25"})
    request.user = MagicMock(is_authenticated=True, username="amina")
    request.labs_context = {"program_id": 25}
    request.session = {"labs_oauth": {"access_token": "t"}}
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = pm._definition(pm.PAGE)
        response = start_run_api(request, 47)
    assert response.status_code == 400 and b"page" in response.content
    MockWDA.return_value.create_run.assert_not_called()
