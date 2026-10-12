"""Where /supply/ opens: Stock review for a programme whose stock is held in the field (supply_chain/landing.py).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.tests import test_workflow_views as pinned
from connect_labs.supply_chain.tests.test_workflow_views import OPP, PROGRAM, WORKFLOW, _tabs

pytestmark = pytest.mark.django_db

client_in_programme = pinned.client_in_programme
da = pinned.da

SCOPE = Scope.of("program", PROGRAM)
STOCK_REVIEW = {"label": "Stock review", "slug": "stock-review", "fill": {"workflow": WORKFLOW}}


@pytest.fixture(autouse=True)
def member_of_the_programme(monkeypatch):
    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        return None if program_id in (None, PROGRAM) and organization_id is None else "not yours"

    monkeypatch.setattr(service.access, "may_use", may_use)


def _set(data):
    return service.update("supply", SCOPE, data, SYSTEM)


def _worker(status="active"):
    store = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="store-l", name="Store L", kind="central_store", source="we_recorded"
    )
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="flw-l", name="Worker L", kind="user_held", parent=store,
        opportunity_id=OPP, connect_username="flw-l", source="we_recorded", status=status,
    )  # fmt: skip


def test_a_programme_with_no_stock_in_the_field_opens_on_the_overview(client_in_programme):
    response = client_in_programme.get(reverse("supply_chain:home"))
    assert response.status_code == 200
    assert 'data-testid="tab-hidden"' not in response.content.decode()


def test_a_programme_whose_workers_hold_stock_opens_on_stock_review(client_in_programme):
    _worker()
    _set({"tabs": {"supply_chain:workers": STOCK_REVIEW}})

    response = client_in_programme.get(reverse("supply_chain:home"))

    assert response.status_code == 302
    assert response["Location"] == f"/supply/views/stock-review/?program_id={PROGRAM}"


def test_with_no_workflow_in_the_tab_it_opens_on_the_workers_page(client_in_programme):
    _worker()
    response = client_in_programme.get(reverse("supply_chain:home"))
    assert response["Location"] == f"/supply/workers/?program_id={PROGRAM}"


def test_only_active_workers_count(client_in_programme):
    _worker(status="inactive")
    assert client_in_programme.get(reverse("supply_chain:home")).status_code == 200


def test_the_date_and_other_parameters_ride_along(client_in_programme):
    _worker()
    _set({"tabs": {"supply_chain:workers": STOCK_REVIEW}})

    response = client_in_programme.get(reverse("supply_chain:home"), {"as_of": "2026-09-01", "commodity": "rutf"})

    assert response.status_code == 302
    assert response["Location"] == (
        f"/supply/views/stock-review/?as_of=2026-09-01&commodity=rutf&program_id={PROGRAM}"
    )


def test_the_overview_stays_a_tab_and_does_not_redirect(client_in_programme):
    _worker()
    _set({"tabs": {"supply_chain:workers": STOCK_REVIEW}})

    tabs = [(href, on, label.strip()) for href, on, label in _tabs(client_in_programme, "supply_chain:overview")]

    assert (f"/supply/overview/?program_id={PROGRAM}", True, "Overview") in tabs
    assert client_in_programme.get(reverse("supply_chain:overview")).status_code == 200


def test_settings_can_keep_a_field_programme_on_the_overview(client_in_programme):
    _worker()
    _set({"landing": "supply_chain:home"})
    assert client_in_programme.get(reverse("supply_chain:home")).status_code == 200


def test_settings_can_open_any_programme_on_another_tab(client_in_programme):
    _set({"landing": "supply_chain:orders"})
    assert client_in_programme.get(reverse("supply_chain:home"))["Location"] == f"/supply/orders/?program_id={PROGRAM}"


def test_settings_can_open_on_an_added_tab(client_in_programme):
    _set({"landing": "forecast", "tabs": {"forecast": {"label": "Forecast", "fill": {"workflow": WORKFLOW}}}})
    response = client_in_programme.get(reverse("supply_chain:home"))
    assert response["Location"] == f"/supply/views/forecast/?program_id={PROGRAM}"


def test_an_organisation_sets_the_landing_for_every_programme_it_owns(client_in_programme, monkeypatch):
    service.update("supply", Scope.of("organization", "owner-org"), {"landing": "supply_chain:stock"}, SYSTEM)
    monkeypatch.setattr(
        "connect_labs.labs.context.get_org_data",
        lambda request: {"programs": [{"id": PROGRAM, "organization": "owner-org", "name": "P"}]},
    )
    assert client_in_programme.get(reverse("supply_chain:home"))["Location"] == f"/supply/stock/?program_id={PROGRAM}"


def test_a_programme_overrides_its_organisations_landing(client_in_programme, monkeypatch):
    service.update("supply", Scope.of("organization", "owner-org"), {"landing": "supply_chain:stock"}, SYSTEM)
    _set({"landing": "supply_chain:home"})
    monkeypatch.setattr(
        "connect_labs.labs.context.get_org_data",
        lambda request: {"programs": [{"id": PROGRAM, "organization": "owner-org", "name": "P"}]},
    )
    assert client_in_programme.get(reverse("supply_chain:home")).status_code == 200


def test_a_landing_tab_that_is_turned_off_opens_on_the_overview(client_in_programme):
    _worker()
    _set({"tabs": {"supply_chain:workers": {"hidden": True}}})
    assert client_in_programme.get(reverse("supply_chain:home")).status_code == 200


def test_a_landing_that_names_no_tab_is_refused():
    with pytest.raises(service.Invalid, match="landing"):
        _set({"landing": "nowhere"})


def test_no_programme_in_view_shows_the_overview_asking_for_one(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="nobody", password="x"))
    assert client.get(reverse("supply_chain:home")).status_code == 200


def test_signed_out_is_sent_to_sign_in_not_to_a_tab(client):
    response = client.get(reverse("supply_chain:home"))
    assert response.status_code == 302 and "login" in response["Location"]


def test_breadcrumbs_back_to_the_overview_do_not_bounce(client_in_programme):
    """'Overview › Checks' names the Overview; following it must not land on Stock review."""
    _worker()
    body = client_in_programme.get(reverse("supply_chain:checks")).content.decode()
    assert f'href="{reverse("supply_chain:overview")}"' in body


# --- the Settings page says where Supply opens ---------------------------------


def _landing_line(client):
    body = client.get(reverse("labs:settings", args=["program", PROGRAM])).content.decode()
    return body.split('data-testid="settings-supply-landing">', 1)[1].split("</p>", 1)[0]


def test_settings_says_a_field_programme_opens_on_stock_review_and_why(client_in_programme):
    _worker()
    _set({"tabs": {"supply_chain:workers": STOCK_REVIEW}})
    line = _landing_line(client_in_programme)
    assert "opens on Stock review" in line and "workers hold stock" in line


def test_settings_says_any_other_programme_opens_on_the_overview(client_in_programme):
    assert "opens on Overview" in _landing_line(client_in_programme)


def test_settings_names_a_chosen_landing_and_who_chose_it(client_in_programme):
    _set({"landing": "supply_chain:orders"})
    line = _landing_line(client_in_programme)
    assert "opens on Orders" in line and "this programme" in line
