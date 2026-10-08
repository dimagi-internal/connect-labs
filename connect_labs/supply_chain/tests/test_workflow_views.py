"""A workflow pinned into a programme's supply navigation (workflow_views/).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.workflow_views import views as page_views

pytestmark = pytest.mark.django_db

PROGRAM, OPP, WORKFLOW = 20931, 20932, 8801


class _InProgramme:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)


@pytest.fixture
def client_in_programme(client, django_user_model, settings, monkeypatch, da):
    from connect_labs.supply_chain import api_views, form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.stock import visit_views  # noqa: F401

    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._InProgramme"]
    for module in ("api_views", "form_views", "views", "stock.visit_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)
    client.force_login(django_user_model.objects.create_user(username="pm", password="x"))
    # The runner's page data is WorkflowRunView's; here only the supply frame around it is under test.
    monkeypatch.setattr(
        page_views.WorkflowRunView,
        "_run_context_data",
        lambda self, **kw: {
            "has_context": True,
            "render_code": "function WorkflowUI(){return null}",
            "workflow_data": {"render_code": "x"},
            "definition": {"name": "Supply Stock Review"},
        },
    )
    monkeypatch.setattr(page_views, "current_run_id", lambda request, pin: 77)
    return client


def _pin(da, **data):
    return call_operation(
        "view_pin", da, {"data": {"workflow_definition_id": WORKFLOW, "label": "Stock review", **data}}
    )


def _tabs(client, name="supply_chain:catalogue"):
    """[(href, active, label)] of the supply nav on a page."""
    import re

    body = client.get(reverse(name)).content.decode()
    nav = body.split("<nav", 1)[1].split("</nav>", 1)[0]
    out = []
    for href, cls, label in re.findall(r'<a href="([^"]+)"\s+class="([^"]*)">([^<]+)</a>', nav):
        out.append((href, "bg-brand-indigo text-white" in cls, label))
    return out


def test_a_pin_that_replaces_workers_takes_its_place_in_the_nav(client_in_programme, da):
    _pin(da, replaces="supply_chain:workers")

    tabs = [(href, label.strip()) for href, _, label in _tabs(client_in_programme)]
    labels = [label for _, label in tabs]
    assert "Workers" not in labels
    assert ("/supply/views/stock-review/", "Stock review") in tabs
    assert labels.index("Stock review") == labels.index("Stock") + 1


def test_the_classic_page_still_highlights_the_pinned_tab(client_in_programme, da):
    _pin(da, replaces="supply_chain:workers")

    active = [label.strip() for _, on, label in _tabs(client_in_programme, "supply_chain:workers") if on]
    assert active == ["Stock review"]


def test_a_pin_that_adds_a_tab_sits_with_the_stock_pages_and_unpinning_restores(client_in_programme, da):
    view = _pin(da, label="Coverage", slug="coverage")
    labels = [label.strip() for _, _, label in _tabs(client_in_programme)]
    assert labels.index("Coverage") == labels.index("Where it went") + 1
    assert "Workers" in labels

    call_operation("view_unpin", da, {"view_id": view["id"]})
    assert "Coverage" not in [label.strip() for _, _, label in _tabs(client_in_programme)]


def test_the_pinned_page_mounts_the_runner_with_the_editor_and_classic_links(client_in_programme, da):
    _pin(da, replaces="supply_chain:workers", opportunity_id=OPP)

    response = client_in_programme.get(reverse("supply_chain:workflow_view", args=["stock-review"]))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'id="workflow-root"' in body and "workflow-runner-bundle.js" in body
    assert 'data-testid="supply-banner"' in body
    edit = body.split('data-testid="workflow-edit" href="', 1)[1].split('"', 1)[0]
    assert f"/labs/workflow/{WORKFLOW}/run/" in edit and "edit=true" in edit and "run_id=77" in edit
    assert 'data-testid="classic-view" href="/supply/workers/"' in body


def test_the_run_is_read_by_the_pinned_opportunity_alone(client_in_programme, da, monkeypatch):
    # Records scoped to an opportunity carry no programme: a programme in the scope too finds none.
    seen = {}

    def run_context(self, **kw):
        seen.update(self.request.labs_context)
        return {"has_context": True, "render_code": "x", "workflow_data": {}, "definition": {}}

    monkeypatch.setattr(page_views.WorkflowRunView, "_run_context_data", run_context)
    _pin(da, replaces="supply_chain:workers", opportunity_id=OPP)

    body = client_in_programme.get(reverse("supply_chain:workflow_view", args=["stock-review"])).content.decode()

    assert seen == {"opportunity_id": OPP}
    assert "Stock review" in body.split("<nav", 1)[1].split("</nav>", 1)[0]  # the frame still has its programme


def test_a_past_day_rides_on_the_runners_supply_endpoints_and_shows_in_the_header(
    client_in_programme, da, monkeypatch
):
    def run_context(self, **kw):
        endpoints = {"getSupplyData": "/labs/workflow/api/8801/supply-data/", "querySupply": "/q/", "other": "/o/"}
        return {"has_context": True, "render_code": "x", "workflow_data": {"apiEndpoints": endpoints}}

    monkeypatch.setattr(page_views.WorkflowRunView, "_run_context_data", run_context)
    _pin(da, replaces="supply_chain:workers", opportunity_id=OPP)
    url = reverse("supply_chain:workflow_view", args=["stock-review"])

    body = client_in_programme.get(url + "?as_of=2026-09-01").content.decode()

    assert "/labs/workflow/api/8801/supply-data/?as_of=2026-09-01" in body
    assert "/q/?as_of=2026-09-01" in body and '"/o/"' in body
    assert 'var asOf = "2026-09-01"' in body  # the supply frame's own past-day handling is on
    assert "as_of" not in client_in_programme.get(url).content.decode().split('id="workflow-data"', 1)[1]
    assert client_in_programme.get(url + "?as_of=yesterday").status_code == 400


def test_an_unknown_pin_is_not_found(client_in_programme):
    assert client_in_programme.get(reverse("supply_chain:workflow_view", args=["nope"])).status_code == 404


def test_pinning_the_same_slug_again_updates_it(da):
    _pin(da)
    _pin(da, label="Stock review", replaces="supply_chain:workers")

    views = call_operation("view_list", da, {})
    assert len(views) == 1 and views[0]["replaces"] == "supply_chain:workers"
