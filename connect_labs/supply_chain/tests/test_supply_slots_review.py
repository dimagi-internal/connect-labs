"""Fixes from the independent review of #2418 (2026-10-09): Supply's tabs in Settings.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.supply_chain.tests import test_supply_slots as slots
from connect_labs.supply_chain.tests import test_workflow_views as pinned
from connect_labs.supply_chain.tests.test_workflow_views import OPP, PROGRAM, WORKFLOW, _tabs

pytestmark = pytest.mark.django_db

client_in_programme = pinned.client_in_programme
da = pinned.da
member_of_the_programme = slots.member_of_the_programme

ORG = Scope.of("organization", "owner-org")
PROG = Scope.of("program", PROGRAM)
TREE = {
    "organizations": [{"id": 1, "slug": "owner-org", "name": "Owner"}],
    "programs": [{"id": PROGRAM, "organization": "owner-org", "name": "P"}],
}


@pytest.fixture
def owned_by_an_org(monkeypatch):
    monkeypatch.setattr(service, "tree_for", lambda caller: TREE)
    monkeypatch.setattr("connect_labs.labs.context.get_org_data", lambda request: TREE)
    service.update("supply", ORG, {"tabs": {"ops": {"label": "Operations", "fill": {"workflow": WORKFLOW}}}}, SYSTEM)


def test_a_programme_can_hide_a_tab_its_organisation_added(owned_by_an_org, client_in_programme):
    """S2-4: checked layer by layer, {"hidden": true} on a tab with no fill of its own was refused."""
    service.update("supply", PROG, {"tabs": {"ops": {"hidden": True}}}, SYSTEM)
    assert "Operations" not in [label.strip() for _, _, label in _tabs(client_in_programme)]


def test_a_programme_can_relabel_a_tab_its_organisation_added(owned_by_an_org, client_in_programme):
    service.update("supply", PROG, {"tabs": {"ops": {"label": "Ops room"}}}, SYSTEM)
    assert "Ops room" in [label.strip() for _, _, label in _tabs(client_in_programme)]


def test_the_settings_hide_button_works_on_an_organisations_tab(owned_by_an_org, client_in_programme):
    url = reverse("labs:settings", args=["program", PROGRAM])
    client_in_programme.post(url, {"namespace": "supply", "action": "hide_tab", "tab": "ops", "version": "0"})
    assert "Operations" not in [label.strip() for _, _, label in _tabs(client_in_programme)]


def test_a_slug_another_layer_already_uses_is_refused(owned_by_an_org):
    with pytest.raises(service.Invalid, match="already used"):
        service.update(
            "supply", PROG, {"tabs": {"coverage": {"slug": "ops", "label": "C", "fill": {"workflow": 9}}}}, SYSTEM
        )


def test_an_added_tab_still_needs_a_fill_somewhere(owned_by_an_org):
    with pytest.raises(service.Invalid, match="needs a fill"):
        service.update("supply", PROG, {"tabs": {"mystery": {"hidden": True}}}, SYSTEM)


def test_a_tab_whose_opportunity_the_viewer_cannot_use_is_not_found_and_starts_no_run(
    client_in_programme, monkeypatch
):
    """S1-2 (Supply): the tab's opportunity came from Settings; a run was read or created under it unchecked."""
    from connect_labs.supply_chain.workflow_views import views as page_views

    def no_runs(request, pin):
        raise AssertionError("no run may be read or created under an opportunity the viewer cannot use")

    monkeypatch.setattr(page_views, "current_run_id", no_runs)
    monkeypatch.setattr("connect_labs.labs.context.validate_context_access", lambda request, context: {})
    service.update(
        "supply",
        PROG,
        {"tabs": {"ops": {"label": "Ops", "fill": {"workflow": WORKFLOW, "opportunity_id": OPP}}}},
        SYSTEM,
    )

    response = client_in_programme.get(reverse("supply_chain:workflow_view", args=["ops"]))

    assert response.status_code == 404


@pytest.mark.parametrize(
    "form",
    [
        {"namespace": "supply", "action": "save", "version": "two", "data": "{}"},
        {"namespace": "supply", "action": "undo", "change_id": "latest"},
    ],
)
def test_a_settings_form_with_a_non_number_is_a_bad_request_not_a_crash(client_in_programme, form):
    response = client_in_programme.post(reverse("labs:settings", args=["program", PROGRAM]), form)
    assert response.status_code == 400


def test_pages_are_left_out_of_the_programme_run_list():
    from unittest.mock import MagicMock

    from connect_labs.workflow import program_view

    page = MagicMock(id=1, data={"kind": "page"})
    report = MagicMock(id=2, data={})
    dao = MagicMock(**{"list_definitions.return_value": [page, report]})
    program_view_owned = program_view.owned_by_program
    try:
        program_view.owned_by_program = lambda d, pid: True
        found = program_view.collect_program_workflows(PROGRAM, [OPP], dao_factory=lambda opp: dao)
    finally:
        program_view.owned_by_program = program_view_owned

    assert [d.id for d in found] == [2]


def test_the_pin_migration_keeps_the_old_order_when_positions_tie(client_in_programme):
    """The header ordered pins by (position, id); config orders by (position, slug)."""
    import importlib

    from django.apps import apps

    from connect_labs.supply_chain.workflow_views.models import SupplyWorkflowView

    for slug in ("zeta", "alpha"):  # created in this order, same position
        SupplyWorkflowView.objects.create(
            program_id=PROGRAM, slug=slug, label=slug.title(), workflow_definition_id=WORKFLOW, opportunity_id=OPP
        )
    importlib.import_module("connect_labs.scope_config.migrations.0002_supply_pins").forward(apps, None)

    labels = [label.strip() for _, _, label in _tabs(client_in_programme)]
    assert labels.index("Zeta") < labels.index("Alpha")
