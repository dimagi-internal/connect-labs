"""Supply data as a workflow data source (workflow/supply_sources.py).

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

The world: one programme, two opportunities. A partner store issues 300 sachets to
worker-acacia (opportunity A) and 200 to worker-baobab (opportunity B); acacia gives
out 120 over 12 days, baobab 40 over 10. The viewer holds both opportunities.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from connect_labs.supply_chain.models import Commodity, Item, Movement, SupplyPoint
from connect_labs.workflow import supply_sources

pytestmark = pytest.mark.django_db

PROGRAM = 20911
OPP_A, OPP_B, OPP_ELSEWHERE = 20912, 20913, 20999
TODAY = timezone.localdate()


@pytest.fixture
def world():
    commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}",
        slug="rutf",
        name="RUTF",
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    item = Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku="rutf-150",
        name="RUTF 150",
        commodity=commodity,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    store = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="partner", name="Partner store", kind="regional_store", source="we_recorded"
    )

    def move(kind, quantity, days_ago, frm=None, to=None):
        Movement.objects.create(
            program_id=PROGRAM,
            kind=kind,
            occurred_on=TODAY - timedelta(days=days_ago),
            from_supply_point=frm,
            to_supply_point=to,
            item=item,
            commodity=commodity,
            quantity=Decimal(quantity),
            quantity_unit="sachet",
            source="we_recorded",
        )

    move("receipt", 600, 30, to=store)
    workers = {}
    for name, opp, issued, given, days in (
        ("worker-acacia", OPP_A, 300, 120, 12),
        ("worker-baobab", OPP_B, 200, 40, 10),
    ):
        point = SupplyPoint.objects.create(
            program_id=PROGRAM,
            opportunity_id=opp,
            slug=name,
            name=name,
            kind="user_held",
            connect_username=name,
            parent=store,
            source="connect_visit",
        )
        move("distribution", issued, days + 1, frm=store, to=point)
        for d in range(days):
            move("consumption", given // days, d + 1, frm=point)
        workers[name] = point
    return {"item": item, "store": store, **workers}


def _request(opps=(OPP_A, OPP_B)):
    user, _ = get_user_model().objects.get_or_create(username="viewer")
    request = RequestFactory().get("/")
    request.user = user
    request.session = {
        "labs_oauth": {
            "access_token": "placeholder",
            "organization_data": {
                "organizations": [],
                "programs": [{"id": PROGRAM, "name": "Placeholder programme"}],
                "opportunities": [{"id": o, "name": f"opp {o}", "program": PROGRAM} for o in opps],
            },
        }
    }
    return request


def _definition(*sources):
    return SimpleNamespace(data={"supply_sources": list(sources)}, opportunity_ids=[OPP_A, OPP_B])


STOCK = {"alias": "stock", "source": "worker_stock", "item": "rutf"}
STORES = {"alias": "stores", "source": "network_stock", "item": "rutf"}


def test_a_worker_source_reads_every_opportunity_and_tags_its_rows(world):
    out = supply_sources.load(_request(), _definition(STOCK), opportunity_ids=[OPP_A, OPP_B])

    rows = out["stock"]["rows"]
    assert sorted((r["name"], r["opportunity_id"], r["program_id"]) for r in rows) == [
        ("worker-acacia", OPP_A, PROGRAM),
        ("worker-baobab", OPP_B, PROGRAM),
    ]
    assert out["stock"]["metadata"]["per_opp"][str(OPP_A)]["row_count"] == 1


def test_the_rollup_sums_counts_and_recomputes_shares(world):
    rollup = supply_sources.load(_request(), _definition(STOCK), opportunity_ids=[OPP_A, OPP_B])["stock"]["rollup"]

    sachets = rollup["by_unit"]["sachet"]
    assert (sachets["issued"], sachets["dispensed"], sachets["on_hand"]) == (500, 160, 340)
    assert rollup["workers"] == 2
    assert sum(rollup["runway"].values()) == 2


def test_a_programme_source_runs_once_however_many_of_its_opportunities_the_workflow_spans(world):
    rows = supply_sources.load(_request(), _definition(STORES), opportunity_ids=[OPP_A, OPP_B])["stores"]["rows"]

    stores = [r for r in rows if r.get("kind") != "user_held"]
    assert [r["name"] for r in stores] == ["Partner store"]
    # A programme-wide row keeps its own opportunity (a worker's), or none (a store).
    assert all(r["program_id"] == PROGRAM for r in rows)
    assert all(r["opportunity_id"] is None for r in stores)


def test_an_opportunity_the_viewer_does_not_hold_gets_an_error_not_rows(world):
    out = supply_sources.load(_request(opps=(OPP_A,)), _definition(STOCK), opportunity_ids=[OPP_A, OPP_ELSEWHERE])

    assert [r["name"] for r in out["stock"]["rows"]] == ["worker-acacia"]
    assert "not accessible" in out["stock"]["metadata"]["per_opp"][str(OPP_ELSEWHERE)]["error"]


def test_on_demand_sources_are_not_loaded_with_the_page_but_run_when_asked(world):
    worker = {"alias": "worker", "source": "worker_stock_get", "item": "rutf", "load": "on_demand"}
    definition = _definition(STOCK, worker)

    assert set(supply_sources.load(_request(), definition, opportunity_ids=[OPP_A])) == {"stock"}
    one = supply_sources.run(
        _request(),
        definition,
        worker,
        opportunity_ids=[OPP_A],
        args={"supply_point_id": world["worker-acacia"].pk},
    )
    assert one["rows"][0]["timeline"]["days"]


def test_a_source_refuses_an_argument_it_does_not_take(world):
    with pytest.raises(supply_sources.SupplySourceError, match="takes no"):
        supply_sources.run(_request(), _definition(STOCK), STOCK, opportunity_ids=[OPP_A], args={"limit": 5})


def test_declarations_name_what_is_wrong():
    problems = supply_sources.declaration_problems(
        [{"alias": "a", "source": "nope"}, {"alias": "a", "source": "worker_stock", "load": "later"}, {}]
    )
    assert any("'nope' is not one of" in p for p in problems)
    assert any("declared twice" in p for p in problems)
    assert any("load must be" in p for p in problems)
    assert any("needs an alias" in p for p in problems)
    assert supply_sources.declaration_problems([STOCK, STORES]) == []


def test_only_read_operations_are_sources():
    from connect_labs.supply_chain.operations import _REGISTRY

    for name, source in supply_sources.SOURCES.items():
        assert not _REGISTRY[source.operation].is_write, name


def test_a_snapshot_freezes_the_supply_aliases_its_manifest_names():
    from connect_labs.workflow.templates import _default_snapshot_from_inputs

    supply = {"stock": {"rows": [{"name": "w"}]}, "stores": {"rows": []}}
    out = _default_snapshot_from_inputs(
        snapshot_inputs={"supply": ["stock"]},
        pipelines={},
        state={},
        context={"supply": supply, "workers": []},
        opportunity_id=OPP_A,
    )
    assert out["supply"] == {"stock": supply["stock"]}


# ---- the endpoints and the first template ---------------------------------------


@pytest.fixture
def signed_in(client, monkeypatch):
    user, _ = get_user_model().objects.get_or_create(username="viewer")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = _request().session["labs_oauth"]
    session.save()
    definition = SimpleNamespace(data={"supply_sources": [STOCK]}, opportunity_ids=[OPP_A, OPP_B])
    monkeypatch.setattr(
        "connect_labs.workflow.supply_views.WorkflowDataAccess",
        lambda request: SimpleNamespace(get_definition=lambda _id: definition, close=lambda: None),
    )
    return client


def test_the_data_endpoint_loads_the_eager_sources(signed_in, world):
    from django.urls import reverse

    response = signed_in.get(reverse("labs:workflow:api_supply_data", args=[1]))

    assert response.status_code == 200
    assert {r["name"] for r in response.json()["supply"]["stock"]["rows"]} == {"worker-acacia", "worker-baobab"}


def test_the_query_endpoint_refuses_an_opportunity_the_workflow_does_not_span(signed_in, world):
    from django.urls import reverse

    url = reverse("labs:workflow:api_supply_query", args=[1])
    response = signed_in.post(
        url, data={"alias": "stock", "opportunity_id": OPP_ELSEWHERE}, content_type="application/json"
    )
    assert response.status_code == 403
    response = signed_in.post(url, data={"alias": "nope"}, content_type="application/json")
    assert response.status_code == 400 and "declares no supply source" in response.json()["error"]
    response = signed_in.post(url, data={"alias": "stock", "opportunity_id": OPP_A}, content_type="application/json")
    assert response.status_code == 200 and [r["name"] for r in response.json()["rows"]] == ["worker-acacia"]


def test_the_query_endpoint_takes_as_of_from_its_url_when_the_body_has_none(signed_in, world, monkeypatch):
    # A page showing a past day (a pinned supply tab with ?as_of=) puts the date on the endpoint's URL.
    from django.urls import reverse

    seen = []
    monkeypatch.setattr(
        "connect_labs.workflow.supply_views.supply_sources.run",
        lambda request, definition, spec, **kw: seen.append(kw["as_of"]) or {"rows": []},
    )
    url = reverse("labs:workflow:api_supply_query", args=[1])
    signed_in.post(url + "?as_of=2026-09-01", data={"alias": "stock"}, content_type="application/json")
    signed_in.post(
        url + "?as_of=2026-09-01", data={"alias": "stock", "as_of": "2026-08-01"}, content_type="application/json"
    )
    assert [d.isoformat() for d in seen] == ["2026-09-01", "2026-08-01"]


def test_the_stock_review_template_declares_valid_sources():
    from connect_labs.workflow.templates import TEMPLATES

    template = TEMPLATES["supply_stock_review"]
    assert template["multi_opp"] is True
    assert supply_sources.declaration_problems(template["definition"]["supply_sources"]) == []
