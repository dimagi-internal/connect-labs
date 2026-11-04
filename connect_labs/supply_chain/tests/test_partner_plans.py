"""How a partner distributes: stated at setup, the network built from it, changes kept as history.

Placeholder names only (public repo).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import PartnerPlan, SupplyPoint
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10983


def _op(name, **payload):
    return call_operation(
        name, SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM), payload
    )


def _day(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def partner():
    return LabsOrg.objects.create(slug="partner-a", name="Partner A")


def _point(slug, kind, parent=None, org=None, **extra):
    extra.setdefault("connect_username", f"{slug}-user" if kind == "user_held" else "")
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug=slug,
        name=slug.title(),
        kind=kind,
        parent=parent,
        managed_by_org=org,
        source="we_recorded",
        **extra,
    )


def _plan(partner, model, days_ago=0, **over):
    data = {
        "org_id": partner.pk,
        "distribution_model": model,
        "starts_on": _day(days_ago),
        "source": "partner_reported",
    }
    data.update(over)
    return _op("partner_plan_set", data=data)


def test_stating_a_plan_without_building_leaves_the_network_alone(partner):
    result = _plan(partner, "through_facilities")
    assert result["plan"]["current"] and "network" not in result
    assert SupplyPoint.objects.filter(program_id=PROGRAM).count() == 0


def test_build_creates_the_warehouse_and_sets_bands_lower_nearer_the_field(partner):
    store = _point("store-a", "central_store", org=partner)
    phc = _point("phc-a", "facility", parent=store)
    flw = _point("flw-a", "user_held", parent=phc)
    result = _plan(partner, "through_facilities", build_network=True)
    assert result["network"]["created_warehouse"] is False
    for point in (store, phc, flw):
        point.refresh_from_db()
    assert (store.min_months_of_stock, store.max_months_of_stock) == (Decimal("1"), Decimal("3"))
    assert phc.max_months_of_stock < store.max_months_of_stock
    assert flw.max_months_of_stock <= phc.max_months_of_stock


def test_build_makes_a_warehouse_run_by_the_partner_when_there_is_none(partner):
    result = _plan(partner, "direct_to_workers", build_network=True)
    warehouse = SupplyPoint.objects.get(pk=result["network"]["warehouse"]["id"])
    assert warehouse.managed_by_org_id == partner.pk and warehouse.kind == "central_store"


def test_a_change_of_model_is_a_dated_change_of_plan_and_workers_are_not_moved_quietly(partner):
    store = _point("store-a", "central_store", org=partner)
    phc = _point("phc-a", "facility", parent=store)
    flw = _point("flw-a", "user_held", parent=phc)
    _plan(partner, "through_facilities", days_ago=60)

    result = _plan(partner, "direct_to_workers", days_ago=0, build_network=True)
    flw.refresh_from_db()
    assert flw.parent_id == phc.pk, "without move_workers nobody moves"
    assert result["network"]["workers_waiting"] == ["Flw-A"]
    assert result["changed_from"] == "through_facilities"
    history = PartnerPlan.objects.filter(org=partner).order_by("starts_on")
    assert [p.distribution_model for p in history] == ["through_facilities", "direct_to_workers"]
    assert history[0].ended_on == history[1].starts_on and history[1].ended_on is None


def test_moving_workers_is_explicit_dated_and_named_on_the_plan(partner):
    store = _point("store-a", "central_store", org=partner)
    phc = _point("phc-a", "facility", parent=store)
    flw = _point("flw-a", "user_held", parent=phc)
    _plan(partner, "through_facilities", days_ago=60)
    result = _plan(partner, "direct_to_workers", build_network=True, move_workers=True, note="stopped storing at PHCs")
    flw.refresh_from_db()
    assert flw.parent_id == store.pk
    assert result["network"]["workers_moved"] == ["Flw-A"]
    note = PartnerPlan.objects.get(org=partner, ended_on=None).note
    assert "stopped storing at PHCs" in note and "Flw-A" in note


def test_a_hand_set_band_survives_a_change_of_model(partner):
    store = _point("store-a", "central_store", org=partner)
    flw = _point(
        "flw-a", "user_held", parent=store, min_months_of_stock=Decimal("2"), max_months_of_stock=Decimal("5")
    )
    _plan(partner, "through_facilities", days_ago=10, build_network=True)
    _plan(partner, "direct_to_workers", build_network=True)
    flw.refresh_from_db()
    assert (flw.min_months_of_stock, flw.max_months_of_stock) == (Decimal("2"), Decimal("5"))


def test_a_new_plan_cannot_start_before_the_one_it_replaces(partner):
    _plan(partner, "mixed", days_ago=5)
    with pytest.raises(Exception, match="cannot start before"):
        _plan(partner, "direct_to_workers", days_ago=20)


def test_assigning_places_names_the_partner_and_leaves_other_partners_places(partner):
    other = LabsOrg.objects.create(slug="partner-b", name="Partner B")
    free = _point("store-free", "central_store")
    taken = _point("store-taken", "central_store", org=other)
    result = _op("partner_places_assign", org_id=partner.pk, supply_point_ids=[free.pk, taken.pk])
    free.refresh_from_db()
    taken.refresh_from_db()
    assert result["count"] == 1 and free.managed_by_org_id == partner.pk and taken.managed_by_org_id == other.pk


# ---- the screens ------------------------------------------------------------

from django.urls import reverse  # noqa: E402


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.partners import views as partner_views  # noqa: F401

    account = django_user_model.objects.create_user(username="pm", password="x", email="pm@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "partners.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


def test_partners_page_shows_each_partner_its_model_and_history(scoped, partner):
    _point("store-a", "central_store", org=partner)
    _plan(partner, "through_facilities", days_ago=60)
    _plan(partner, "direct_to_workers", note="stopped storing at PHCs")
    page = scoped.get(reverse("supply_chain:partners"))
    body = page.content.decode()
    assert page.status_code == 200
    assert "Partner A" in body and "Straight to field workers" in body
    assert "stopped storing at PHCs" in body and "History of changes of plan" in body


def test_partners_page_lists_places_with_no_partner_and_assigns_them(scoped, partner):
    free = _point("store-free", "central_store")
    body = scoped.get(reverse("supply_chain:partners")).content.decode()
    assert "Places with no partner" in body and "Store-Free" in body
    posted = scoped.post(reverse("supply_chain:partner_places"), {"org": partner.pk, "places": [free.pk]})
    assert posted.status_code == 302
    free.refresh_from_db()
    assert free.managed_by_org_id == partner.pk
    assert "Places with no partner" not in scoped.get(reverse("supply_chain:partners")).content.decode()


def test_the_plan_form_records_the_plan_and_builds_the_network(scoped, partner):
    posted = scoped.post(
        reverse("supply_chain:partner_plan"),
        {
            "org": partner.pk,
            "distribution_model": "direct_to_workers",
            "starts_on": date.today().isoformat(),
            "source": "partner_reported",
            "build_network": "on",
            "note": "",
        },
    )
    assert posted.status_code == 302, getattr(posted, "context", None) and posted.context["form"].errors
    assert PartnerPlan.objects.get(org=partner).distribution_model == "direct_to_workers"
    assert SupplyPoint.objects.filter(program_id=PROGRAM, managed_by_org=partner, kind="central_store").exists()
