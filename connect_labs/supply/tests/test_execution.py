"""Session-authenticated execution surface.

The load-bearing property here: **anything a supplier can post through the
machine API, they can also key in through the portal**, producing the same
shipment, the same events and the same derived state — only the recorded
source tier differs, so hand-entered data is never disguised as a system feed.
"""
import json
from datetime import timedelta

import pytest

from connect_labs.supply import gs1
from connect_labs.supply.models import Discrepancy, Shipment, SupplyEvent
from connect_labs.supply.services import tokens

from . import factories as f

pytestmark = pytest.mark.django_db


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _api(client, url, payload, token):
    return client.post(
        url, data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}"
    )


@pytest.fixture
def ops(supplier_client):
    """A logged-in supplier with a contract and two nodes."""
    client, member = supplier_client
    factory_node = f.SupplyNodeFactory(name="Kano RUTF Plant", kind="factory", gln="6291234500008")
    hub = f.SupplyNodeFactory(name="Maiduguri Hub", kind="distribution_hub", gln="6291234500015")
    contract = f.ContractFactory(org=member.org, reference="OES-C-0100", total_quantity=60000, unit_price=42)
    return {
        "client": client,
        "org": member.org,
        "factory": factory_node,
        "hub": hub,
        "contract": contract,
    }


def _despatch_payload(ops, asn="ASN-PORTAL-1", qty=60000):
    return {
        "asn_reference": asn,
        "contract_reference": ops["contract"].reference,
        "ship_from_gln": ops["factory"].gln,
        "ship_to_gln": ops["hub"].gln,
        "departed_at": "2026-07-20T09:00:00+01:00",
        "eta": "2026-07-27T17:00:00+01:00",
        "packages": [
            {
                "sscc": gs1.make_sscc("629123", 9),
                "items": [
                    {
                        "gtin": gs1.make_gtin("629123", 7346),
                        "batch_lot": "LOT2607B",
                        "expiry_date": "2028-02-28",
                        "quantity": qty,
                        "unit": "cartons",
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Webform parity with the machine API
# ---------------------------------------------------------------------------


def test_portal_despatch_form_creates_the_same_shipment_as_the_api(ops):
    resp = _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops))
    assert resp.status_code == 200
    body = resp.json()["shipment"]
    assert body["asn_reference"] == "ASN-PORTAL-1"
    assert body["quantity"] == 60000
    assert body["metric_tonnes"] == pytest.approx(828.0)

    shipment = Shipment.objects.get(asn_reference="ASN-PORTAL-1")
    assert shipment.lines.count() == 1
    assert shipment.status == Shipment.Status.IN_TRANSIT
    assert sorted(shipment.milestones.values_list("kind", flat=True)) == ["arrive", "depart"]
    # identical to the API path in every respect except declared provenance
    assert shipment.events.get().source_tier == SupplyEvent.SourceTier.PORTAL


def test_portal_and_api_despatch_produce_equivalent_records(ops):
    """Same payload, two doors: the resulting shipment must match field for field."""
    _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops, asn="ASN-BY-HAND"))
    _t, token = tokens.mint_token(ops["org"], "machine")
    _api(
        ops["client"],
        "/supply/api/v1/shipments/",
        _despatch_payload(ops, asn="ASN-BY-MACHINE"),
        token,
    )

    by_hand = Shipment.objects.get(asn_reference="ASN-BY-HAND")
    by_machine = Shipment.objects.get(asn_reference="ASN-BY-MACHINE")

    def shape(s):
        return {
            "quantity": float(s.quantity),
            "status": s.status,
            "origin": s.origin_id,
            "destination": s.destination_id,
            "lines": sorted((line.gtin, line.batch_lot, float(line.quantity), line.sscc) for line in s.lines.all()),
            "milestones": sorted(s.milestones.values_list("kind", flat=True)),
            "steps": sorted(s.events.values_list("biz_step", flat=True)),
        }

    assert shape(by_hand) == shape(by_machine)
    assert by_hand.events.get().source_tier == "portal"
    assert by_machine.events.get().source_tier == "asn"


def test_portal_event_form_matches_epcis_capture(ops):
    _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops))
    shipment = Shipment.objects.get()

    resp = _post(
        ops["client"],
        f"/supply/api/shipments/{shipment.id}/events/",
        {
            "biz_step": "arriving",
            "node_id": ops["hub"].id,
            "event_time": "2026-07-26T10:00:00Z",
            "gtin": gs1.make_gtin("629123", 7346),
            "batch_lot": "LOT2607B",
            "quantity": 60000,
            "note": "Keyed in from the driver's paper waybill.",
        },
    )
    assert resp.status_code == 200

    event = shipment.events.filter(source_tier=SupplyEvent.SourceTier.PORTAL, biz_step="arriving").get()
    assert event.read_point == ops["hub"]
    assert event.quantity_list[0]["batch_lot"] == "LOT2607B"
    assert event.raw["entered_by_hand"] is True
    # and it advances derived state exactly as an EPCIS event would
    arrive = shipment.milestones.get(kind="arrive")
    assert arrive.actual_at is not None


def test_portal_event_form_rejects_unknown_step(ops):
    _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops))
    shipment = Shipment.objects.get()
    resp = _post(ops["client"], f"/supply/api/shipments/{shipment.id}/events/", {"biz_step": "teleporting"})
    assert resp.status_code == 400


def test_portal_checkin_and_confirm(ops):
    _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops))
    shipment = Shipment.objects.get()

    _post(
        ops["client"],
        f"/supply/api/shipments/{shipment.id}/checkin/",
        {"status": "arriving", "location_gln": ops["hub"].gln, "occurred_at": "2026-07-26T09:00:00Z"},
    )
    assert shipment.events.filter(source_tier=SupplyEvent.SourceTier.CHECKIN).exists()

    resp = _post(ops["client"], f"/supply/api/shipments/{shipment.id}/confirm/", {"quantity": 60000})
    assert resp.status_code == 200
    shipment.refresh_from_db()
    assert shipment.status == Shipment.Status.CONFIRMED


def test_portal_confirm_with_short_quantity_raises_discrepancy(ops):
    _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops))
    shipment = Shipment.objects.get()
    _post(ops["client"], f"/supply/api/shipments/{shipment.id}/confirm/", {"quantity": 59000})
    disc = Discrepancy.objects.get()
    assert float(disc.shortfall) == 1000


def test_cannot_confirm_a_shipment_that_has_not_departed(ops):
    shipment = f.ShipmentFactory(contract=ops["contract"], status=Shipment.Status.PLANNED)
    resp = _post(ops["client"], f"/supply/api/shipments/{shipment.id}/confirm/", {})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------


def test_supplier_sees_only_own_contracts_and_shipments(ops):
    rival_contract = f.ContractFactory(org=f.SupplierOrgFactory(legal_name="Rival Foods"))
    rival_shipment = f.ShipmentFactory(contract=rival_contract)

    body = ops["client"].get("/supply/api/contracts/").json()["contracts"]
    assert [c["reference"] for c in body] == [ops["contract"].reference]

    assert ops["client"].get(f"/supply/api/shipments/{rival_shipment.id}/").status_code == 404
    assert (
        _post(
            ops["client"], f"/supply/api/shipments/{rival_shipment.id}/events/", {"biz_step": "arriving"}
        ).status_code
        == 404
    )


def test_staff_see_all_contracts_but_cannot_report(admin_client):
    client, _user = admin_client
    f.ContractFactory(reference="OES-C-9001")
    f.ContractFactory(reference="OES-C-9002")
    body = client.get("/supply/api/contracts/").json()["contracts"]
    assert len(body) == 2
    # reporting is the supplier's job; staff resolve discrepancies instead
    assert _post(client, "/supply/api/shipments/", {}).status_code == 403


def test_staff_resolve_discrepancy_supplier_cannot(ops, admin_client):
    _post(ops["client"], "/supply/api/shipments/", _despatch_payload(ops))
    shipment = Shipment.objects.get()
    _post(ops["client"], f"/supply/api/shipments/{shipment.id}/confirm/", {"quantity": 100})
    disc = Discrepancy.objects.get()

    assert _post(ops["client"], f"/supply/api/discrepancies/{disc.id}/resolve/", {}).status_code == 403

    staff_client, _user = admin_client
    resp = _post(staff_client, f"/supply/api/discrepancies/{disc.id}/resolve/", {"note": "Damage in transit"})
    assert resp.status_code == 200
    disc.refresh_from_db()
    assert disc.status == Discrepancy.Status.RESOLVED
    assert "Damage in transit" in disc.note


# ---------------------------------------------------------------------------
# API tokens (self-service, so a supplier can automate what they keyed by hand)
# ---------------------------------------------------------------------------


def test_supplier_mints_and_revokes_tokens(ops):
    resp = _post(ops["client"], "/supply/api/tokens/", {"label": "factory feed"})
    assert resp.status_code == 200
    body = resp.json()
    secret = body["secret"]
    assert secret.startswith("oes_")
    assert body["token"]["prefix"] == secret[:12]
    assert tokens.resolve_token(secret) == ops["org"]

    listed = ops["client"].get("/supply/api/tokens/").json()["tokens"]
    assert len(listed) == 1
    assert "secret" not in listed[0]  # the raw token is shown exactly once

    assert _post(ops["client"], f"/supply/api/tokens/{body['token']['id']}/revoke/", {}).status_code == 200
    assert tokens.resolve_token(secret) is None


def test_token_label_required_and_scoped_to_own_org(ops):
    assert _post(ops["client"], "/supply/api/tokens/", {"label": ""}).status_code == 400

    rival = f.SupplierOrgFactory(legal_name="Rival Foods")
    rival_token, _raw = tokens.mint_token(rival, "theirs")
    assert _post(ops["client"], f"/supply/api/tokens/{rival_token.id}/revoke/", {}).status_code == 404


def test_staff_have_no_token_management(admin_client):
    client, _user = admin_client
    assert client.get("/supply/api/tokens/").status_code == 403


def test_no_consignment_is_dated_before_the_contract_that_paid_for_it():
    """An award cannot postdate the deliveries it authorised.

    `Award.awarded_at` is `auto_now_add`, so every award is stamped with the
    moment the seeder ran, while shipment dates are authored in the past — the
    demo world has to open mid-flight with deliveries already made. That put
    thirty consignments before the contract that paid for them: SHP-2026-0805
    dated 23 April against a contract awarded 31 July, and a *delivered*
    consignment under a contract awarded eleven days later.

    It regenerated on every reseed, and in a demo whose subject is auditable
    procurement it is the most damaging detail on the screen. Pinned as a
    property over the whole world, because the fix reconciles against real dates
    and so has to keep holding as the seed data moves.
    """
    from django.core.management import call_command
    from django.utils import timezone

    from connect_labs.supply.models.execution import Contract

    call_command("seed_supply_demo", "--reset")

    def as_date(value):
        return timezone.localtime(value).date() if hasattr(value, "tzinfo") else value

    offenders = []
    for contract in Contract.objects.select_related("award").prefetch_related("shipments"):
        if contract.award is None:
            continue
        awarded_on = as_date(contract.award.awarded_at)
        for shipment in contract.shipments.all():
            for field in ("departed_at", "eta"):
                moment = getattr(shipment, field, None)
                if moment and as_date(moment) < awarded_on:
                    offenders.append(
                        f"{contract.reference} awarded {awarded_on} but "
                        f"{shipment.reference}.{field} is {as_date(moment)}"
                    )

    assert offenders == [], "consignments predating their own award:\n  " + "\n  ".join(offenders)


def test_nothing_claims_to_have_arrived_on_a_day_that_has_not_happened():
    """A future-dated "Delivered" row is a control failure, not a rounding issue.

    Savanna Nutrients' 15,000 cartons into Maiduguri showed Jul 31 under a
    Delivered chip while today was the 30th — and that single row carried 92% of
    Borno's reported coverage, so the figure a whole scene rests on was sourced
    from an arrival that had not occurred.

    The world is authored around a moving today, which is exactly why this is a
    property over the seeded world rather than a fix to one row: the offending
    date was not wrong when it was written, it became wrong as today caught up
    with it, and it will do so again.
    """
    from django.core.management import call_command
    from django.utils import timezone

    from connect_labs.supply.models.execution import Shipment

    call_command("seed_supply_demo", "--reset")
    today = timezone.localdate()

    offenders = []
    landed = (Shipment.Status.DELIVERED, Shipment.Status.CONFIRMED)
    for shipment in Shipment.objects.filter(status__in=landed):
        for field in ("delivered_at", "departed_at", "eta"):
            moment = getattr(shipment, field, None)
            if moment and timezone.localtime(moment).date() > today:
                offenders.append(
                    f"{shipment.reference} is {shipment.status} but "
                    f"{field} is {timezone.localtime(moment).date()} (today {today})"
                )

    assert offenders == [], "arrivals recorded in the future:\n  " + "\n  ".join(offenders)


def test_a_contract_does_not_start_before_it_was_awarded():
    from django.core.management import call_command
    from django.utils import timezone

    from connect_labs.supply.models.execution import Contract

    call_command("seed_supply_demo", "--reset")

    bad = []
    for contract in Contract.objects.select_related("award"):
        if contract.award is None or not contract.starts_on:
            continue
        awarded_on = timezone.localtime(contract.award.awarded_at).date()
        if contract.starts_on < awarded_on:
            bad.append(f"{contract.reference} starts {contract.starts_on}, awarded {awarded_on}")

    assert bad == [], "contracts starting before their award:\n  " + "\n  ".join(bad)


def test_a_milestone_delta_says_whether_it_is_measured_or_a_forecast():
    """A leg that DID arrive nine days late and one merely expected to are
    different claims, and the same chip rendered both."""
    from django.utils import timezone

    from connect_labs.supply.models.execution import Milestone

    planned = timezone.now() - timedelta(days=10)

    measured = Milestone(planned_at=planned, actual_at=planned + timedelta(days=9))
    assert measured.delta_days == 9.0
    assert measured.delta_basis == "measured"
    assert measured.is_overdue_unreported is False

    forecast = Milestone(planned_at=planned, estimated_at=planned + timedelta(days=9))
    assert forecast.delta_days == 9.0
    assert forecast.delta_basis == "estimated"

    unknown = Milestone(planned_at=planned)
    assert unknown.delta_days is None
    assert unknown.delta_basis is None


def test_an_overdue_milestone_with_nothing_reported_does_not_read_as_on_time():
    """The state that produced "0d vs plan" on a late, silent leg.

    Planned date passed, no actual, and an estimate still sitting on the original
    plan: delta_days computes 0 and the row rendered a confident "0d vs plan" for
    a milestone that was overdue and simply had not been heard from.
    """
    from django.utils import timezone

    from connect_labs.supply.models.execution import Milestone

    planned = timezone.now() - timedelta(days=6)

    silent = Milestone(planned_at=planned, estimated_at=planned)
    assert silent.delta_days == 0.0, "the misleading delta is still computed…"
    assert silent.is_overdue_unreported is True, "…but the row must not present it as on time"

    # A genuinely revised estimate is a forecast, not silence.
    revised = Milestone(planned_at=planned, estimated_at=timezone.now() + timedelta(days=2))
    assert revised.is_overdue_unreported is False

    # Arrived is arrived, however late.
    arrived = Milestone(planned_at=planned, actual_at=timezone.now())
    assert arrived.is_overdue_unreported is False

    # A milestone not yet due is not overdue.
    future = Milestone(planned_at=timezone.now() + timedelta(days=3))
    assert future.is_overdue_unreported is False
