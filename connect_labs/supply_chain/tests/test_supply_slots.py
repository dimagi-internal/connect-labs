"""Supply's tabs as slots in the programme's Settings (supply_chain/config.py).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.scope_config.scopes import Scope
from connect_labs.supply_chain.navigation import SUPPLY_TABS
from connect_labs.supply_chain.tests import test_workflow_views as pinned
from connect_labs.supply_chain.tests.test_workflow_views import OPP, PROGRAM, WORKFLOW, _pin, _tabs

pytestmark = pytest.mark.django_db

# The pinned-view tests' world: a signed-in person in PROGRAM, with the supply pages bound.
client_in_programme = pinned.client_in_programme
da = pinned.da

SCOPE = Scope.of("program", PROGRAM)


@pytest.fixture(autouse=True)
def member_of_the_programme(monkeypatch):
    """Whoever is asking holds the programme (membership is scopes.may_use's job, tested there)."""

    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        return None if program_id in (None, PROGRAM) and organization_id is None else "not yours"

    monkeypatch.setattr(service.access, "may_use", may_use)


def _set(patch):
    return service.update("supply", SCOPE, {"tabs": patch}, SYSTEM)


def test_a_programme_with_nothing_set_shows_every_built_in_tab(client_in_programme):
    assert [label.strip() for _, _, label in _tabs(client_in_programme)] == [label for _, label in SUPPLY_TABS]


def test_a_hidden_tab_leaves_the_nav_and_its_page_explains_itself(client_in_programme):
    _set({"supply_chain:alerts": {"hidden": True}})

    assert "Alerts" not in [label.strip() for _, _, label in _tabs(client_in_programme)]
    response = client_in_programme.get(reverse("supply_chain:alerts"))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-testid="tab-hidden"' in body and "Alerts is turned off for this programme" in body
    assert f"/labs/settings/program/{PROGRAM}/#supply" in body


def test_a_page_under_a_hidden_tab_is_hidden_with_it(client_in_programme):
    _set({"supply_chain:alerts": {"hidden": True}})
    assert 'data-testid="tab-hidden"' in client_in_programme.get(reverse("supply_chain:alert_create")).content.decode()


def test_overview_cannot_be_hidden():
    with pytest.raises(service.Invalid, match="Overview cannot be hidden"):
        _set({"supply_chain:home": {"hidden": True}})


def test_an_added_tab_needs_a_workflow():
    with pytest.raises(service.Invalid, match="needs a fill"):
        _set({"coverage": {"label": "Coverage"}})


def test_an_added_tab_goes_after_the_tab_it_names(client_in_programme):
    _set({"coverage": {"label": "Coverage", "fill": {"workflow": WORKFLOW}, "after": "supply_chain:orders"}})
    labels = [label.strip() for _, _, label in _tabs(client_in_programme)]
    assert labels.index("Coverage") == labels.index("Orders") + 1


def test_an_organisation_fills_a_tab_for_every_programme_it_owns(client_in_programme, monkeypatch):
    org = Scope.of("organization", "owner-org")
    service.update(
        "supply", org, {"tabs": {"supply_chain:workers": {"label": "Org review", "fill": {"workflow": 9}}}}, SYSTEM
    )
    monkeypatch.setattr(
        "connect_labs.labs.context.get_org_data",
        lambda request: {"programs": [{"id": PROGRAM, "organization": "owner-org", "name": "P"}]},
    )
    tabs = [(href, label.strip()) for href, _, label in _tabs(client_in_programme)]
    assert (f"/supply/views/org-review/?program_id={PROGRAM}", "Org review") in tabs


def test_the_migration_turns_pins_into_the_same_tabs(client_in_programme):
    """Every programme's tabs read the same before and after scope_config/0002."""
    import importlib

    from django.apps import apps

    from connect_labs.supply_chain.workflow_views.models import SupplyWorkflowView

    SupplyWorkflowView.objects.create(
        program_id=PROGRAM, slug="stock-review", label="Stock review", workflow_definition_id=WORKFLOW,
        opportunity_id=OPP, replaces="supply_chain:workers", created_by="pm",
    )  # fmt: skip
    SupplyWorkflowView.objects.create(
        program_id=PROGRAM, slug="forecast", label="Forecast", workflow_definition_id=WORKFLOW + 1,
        opportunity_id=OPP, position=1,
    )  # fmt: skip
    migration = importlib.import_module("connect_labs.scope_config.migrations.0002_supply_pins")
    migration.forward(apps, None)

    tabs = [(href, label.strip()) for href, _, label in _tabs(client_in_programme)]
    labels = [label for _, label in tabs]
    assert "Workers" not in labels
    assert labels.index("Stock review") == labels.index("Stock") + 1
    assert labels.index("Forecast") == labels.index("Where it went") + 1
    assert (f"/supply/views/forecast/?program_id={PROGRAM}", "Forecast") in tabs
    row = ScopeConfig.objects.get(scope_type="program", scope_key=str(PROGRAM), namespace="supply")
    assert row.data["tabs"]["supply_chain:workers"]["pinned_by"] == "pm"
    assert row.changes.get().via == "migration:supply_pins"


def test_view_list_and_unpin_read_and_write_settings(da):
    from connect_labs.supply_chain.operations import call_operation

    _pin(da, label="Coverage", slug="coverage")
    assert [p["slug"] for p in call_operation("view_list", da, {})] == ["coverage"]
    call_operation("view_unpin", da, {"slug": "coverage"})
    assert call_operation("view_list", da, {}) == []


# --- the Settings page ---------------------------------------------------------


def test_settings_shows_each_tab_and_who_set_it(client_in_programme):
    _set({"supply_chain:alerts": {"hidden": True}})
    body = client_in_programme.get(reverse("labs:settings", args=["program", PROGRAM])).content.decode()
    assert 'data-testid="settings-supply-tabs"' in body
    alerts = body.split('data-testid="settings-tab-supply_chain:alerts"', 1)[1].split("</tr>", 1)[0]
    assert "Hidden" in alerts and "programme" in alerts


def test_showing_a_tab_from_settings_puts_it_back(client_in_programme):
    _set({"supply_chain:alerts": {"hidden": True}})
    url = reverse("labs:settings", args=["program", PROGRAM])
    client_in_programme.post(
        url, {"namespace": "supply", "action": "show_tab", "tab": "supply_chain:alerts", "version": 1}
    )
    assert "Alerts" in [label.strip() for _, _, label in _tabs(client_in_programme)]


def test_a_stale_save_is_refused_and_says_why(client_in_programme):
    _set({"supply_chain:alerts": {"hidden": True}})
    url = reverse("labs:settings", args=["program", PROGRAM])
    response = client_in_programme.post(
        url, {"namespace": "supply", "action": "save", "version": 0, "data": "{}"}, follow=True
    )
    assert "someone else changed it" in response.content.decode()
    assert ScopeConfig.objects.get().data == {"tabs": {"supply_chain:alerts": {"hidden": True}}}


def test_undo_from_settings(client_in_programme):
    _set({"supply_chain:alerts": {"hidden": True}})
    change = ScopeConfig.objects.get().changes.get()
    client_in_programme.post(
        reverse("labs:settings", args=["program", PROGRAM]),
        {"namespace": "supply", "action": "undo", "change_id": change.id},
    )
    assert ScopeConfig.objects.get().data == {}


def test_settings_for_a_scope_you_cannot_use_is_not_found(client_in_programme):
    assert client_in_programme.get(reverse("labs:settings", args=["program", PROGRAM + 1])).status_code == 404


def test_the_supply_header_offers_settings_to_its_members(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:catalogue")).content.decode()
    assert f'data-testid="supply-settings" href="/labs/settings/program/{PROGRAM}/#supply"' in body
