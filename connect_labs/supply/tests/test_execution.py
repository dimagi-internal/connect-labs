"""Session-authenticated execution surface.

The load-bearing property here: **anything a supplier can post through the
machine API, they can also key in through the portal**, producing the same
shipment, the same events and the same derived state — only the recorded
source tier differs, so hand-entered data is never disguised as a system feed.
"""
import json

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
