"""Ingestion tests: the three capability tiers, idempotency, derived state.

The EPCIS payloads here follow the shapes in the official GS1 EPCIS 2.0
examples, so a supplier conformant to the standard would be accepted as-is.
"""
import json
from datetime import timedelta

import pytest
from django.test import Client

from connect_labs.supply import gs1
from connect_labs.supply.models import Discrepancy, Milestone, Shipment, SupplyEvent
from connect_labs.supply.services import tokens

from . import factories as f

pytestmark = pytest.mark.django_db


def _api(client, url, payload, token):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.fixture
def feed(db):
    """An awarded contract with a factory, a hub, and a live API token."""
    org = f.SupplierOrgFactory(legal_name="Savanna Nutrients Ltd", gs1_company_prefix="629123")
    factory = f.SupplyNodeFactory(name="Kano RUTF Plant", kind="factory", country="NG", gln="6291234500008")
    hub = f.SupplyNodeFactory(
        name="Maiduguri Distribution Hub", kind="distribution_hub", country="NG", gln="6291234500015"
    )
    contract = f.ContractFactory(org=org, reference="OES-C-0001", total_quantity=60000, unit_price=42)
    _token, raw = tokens.mint_token(org, "factory feed")
    return {
        "org": org,
        "factory": factory,
        "hub": hub,
        "contract": contract,
        "token": raw,
        "client": Client(),
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_ingestion_requires_bearer_token(feed):
    resp = feed["client"].post("/supply/api/v1/checkins/", data="{}", content_type="application/json")
    assert resp.status_code == 401


def test_invalid_and_revoked_tokens_rejected(feed):
    assert _api(feed["client"], "/supply/api/v1/checkins/", {}, "oes_nope").status_code == 401

    token_obj, raw = tokens.mint_token(feed["org"], "temp")
    tokens.revoke_token(feed["org"], token_obj.id)
    assert _api(feed["client"], "/supply/api/v1/checkins/", {}, raw).status_code == 401


def test_token_is_stored_hashed_not_in_clear(feed):
    from connect_labs.supply.models import ApiToken

    assert not ApiToken.objects.filter(token_hash=feed["token"]).exists()
    assert ApiToken.objects.filter(org=feed["org"]).exists()
    assert tokens.resolve_token(feed["token"]) == feed["org"]


# ---------------------------------------------------------------------------
# Tier 2 — despatch advice (creates the shipment other tiers reference)
# ---------------------------------------------------------------------------


def _asn_payload(feed, asn="ASN-2026-0587", qty=60000):
    return {
        "asn_reference": asn,
        "contract_reference": feed["contract"].reference,
        "po_reference": "PO-2026-0441",
        "ship_from_gln": feed["factory"].gln,
        "ship_to_gln": feed["hub"].gln,
        "departed_at": "2026-07-20T09:14:00+01:00",
        "eta": "2026-07-27T17:00:00+01:00",
        "packages": [
            {
                "sscc": gs1.make_sscc("629123", 1),
                "items": [
                    {
                        "gtin": gs1.make_gtin("629123", 7346),
                        "batch_lot": "LOT2606A",
                        "expiry_date": "2028-01-31",
                        "quantity": qty,
                        "unit": "cartons",
                    }
                ],
            }
        ],
    }


def test_asn_creates_shipment_lines_and_planned_legs(feed):
    resp = _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    assert resp.status_code == 201
    body = resp.json()["shipment"]
    assert body["asn_reference"] == "ASN-2026-0587"
    assert body["quantity"] == 60000
    # unit ladder is surfaced everywhere: 60,000 cartons x 150 x 92g = 828 MT
    assert body["metric_tonnes"] == pytest.approx(828.0)

    shipment = Shipment.objects.get(asn_reference="ASN-2026-0587")
    assert shipment.lines.count() == 1
    line = shipment.lines.get()
    assert line.batch_lot == "LOT2606A"
    assert gs1.is_valid(line.sscc)
    assert gs1.is_valid(line.gtin)

    # planned legs exist so ETA-vs-plan is renderable from despatch onward
    kinds = sorted(shipment.milestones.values_list("kind", flat=True))
    assert kinds == ["arrive", "depart"]
    # the ASN itself is a despatch event, so the shipment is already moving
    assert shipment.status == Shipment.Status.IN_TRANSIT
    assert shipment.events.count() == 1
    assert shipment.events.get().source_tier == SupplyEvent.SourceTier.ASN


def test_asn_is_idempotent(feed):
    first = _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    second = _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    assert first.status_code == 201
    assert second.status_code == 200  # already known, not an error
    assert Shipment.objects.filter(asn_reference="ASN-2026-0587").count() == 1


def test_asn_rejects_unknown_contract_and_locations(feed):
    bad_contract = _asn_payload(feed)
    bad_contract["contract_reference"] = "OES-C-9999"
    assert _api(feed["client"], "/supply/api/v1/shipments/", bad_contract, feed["token"]).status_code == 400

    bad_gln = _asn_payload(feed, asn="ASN-2")
    bad_gln["ship_to_gln"] = "0000000000000"
    assert _api(feed["client"], "/supply/api/v1/shipments/", bad_gln, feed["token"]).status_code == 400


def test_supplier_cannot_despatch_against_another_orgs_contract(feed):
    rival = f.SupplierOrgFactory(legal_name="Rival Foods")
    _t, rival_token = tokens.mint_token(rival, "rival")
    resp = _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), rival_token)
    assert resp.status_code == 400
    assert Shipment.objects.count() == 0


# ---------------------------------------------------------------------------
# Tier 1 — EPCIS 2.0
# ---------------------------------------------------------------------------


def _epcis_doc(feed, biz_step="arriving", event_id="evt-1", gln=None, qty=60000):
    return {
        "@context": ["https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld"],
        "type": "EPCISDocument",
        "schemaVersion": "2.0",
        "epcisBody": {
            "eventList": [
                {
                    "type": "ObjectEvent",
                    "action": "OBSERVE",
                    "eventID": event_id,
                    "bizStep": biz_step,
                    "disposition": "in_transit",
                    "eventTime": "2026-07-24T11:02:00+01:00",
                    "eventTimeZoneOffset": "+01:00",
                    "epcList": [gs1.digital_link("00", gs1.make_sscc("629123", 1))],
                    "quantityList": [
                        {
                            "epcClass": f"https://id.gs1.org/01/{gs1.make_gtin('629123', 7346)}/10/LOT2606A",
                            "quantity": qty,
                            "uom": "CT",
                        }
                    ],
                    "readPoint": {"id": gs1.digital_link("414", gln or feed["hub"].gln)},
                    "bizTransactionList": [
                        {"type": "desadv", "bizTransaction": "urn:oes:asn:ASN-2026-0587"},
                        {"type": "po", "bizTransaction": "urn:oes:po:PO-2026-0441"},
                    ],
                }
            ]
        },
    }


def test_epcis_capture_links_to_shipment_and_stamps_milestone(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    resp = _api(feed["client"], "/supply/api/v1/epcis/capture/", _epcis_doc(feed), feed["token"])
    assert resp.status_code == 201
    assert resp.json()["captured"] == 1

    shipment = Shipment.objects.get(asn_reference="ASN-2026-0587")
    event = shipment.events.filter(source_tier=SupplyEvent.SourceTier.EPCIS).get()
    assert event.biz_step == "arriving"
    assert event.read_point == feed["hub"]
    # quantityList with an embedded batch is decomposed into gtin + lot
    assert event.quantity_list[0]["batch_lot"] == "LOT2606A"

    arrive = shipment.milestones.get(kind=Milestone.Kind.ARRIVE)
    assert arrive.actual_at is not None


def test_epcis_capture_is_idempotent_on_event_id(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    doc = _epcis_doc(feed)
    first = _api(feed["client"], "/supply/api/v1/epcis/capture/", doc, feed["token"]).json()
    second = _api(feed["client"], "/supply/api/v1/epcis/capture/", doc, feed["token"]).json()
    assert first == {"captured": 1, "duplicates": 0, "event_ids": first["event_ids"]}
    assert second["captured"] == 0 and second["duplicates"] == 1
    assert SupplyEvent.objects.filter(source_tier="epcis").count() == 1


def test_epcis_receiving_advances_status_to_delivered(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    _api(
        feed["client"],
        "/supply/api/v1/epcis/capture/",
        _epcis_doc(feed, biz_step="receiving", event_id="evt-recv"),
        feed["token"],
    )
    shipment = Shipment.objects.get(asn_reference="ASN-2026-0587")
    assert shipment.status == Shipment.Status.DELIVERED
    assert shipment.delivered_at is not None


def test_epcis_shipping_alias_and_cbv_urn_accepted(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    doc = _epcis_doc(feed, event_id="evt-urn")
    doc["epcisBody"]["eventList"][0]["bizStep"] = "urn:epcglobal:cbv:bizstep:shipping"
    assert _api(feed["client"], "/supply/api/v1/epcis/capture/", doc, feed["token"]).status_code == 201
    assert SupplyEvent.objects.filter(biz_step="departing", source_tier="epcis").exists()


def test_epcis_rejects_unknown_bizstep_and_empty_document(feed):
    doc = _epcis_doc(feed, biz_step="teleporting", event_id="evt-bad")
    assert _api(feed["client"], "/supply/api/v1/epcis/capture/", doc, feed["token"]).status_code == 400
    assert _api(feed["client"], "/supply/api/v1/epcis/capture/", {"type": "x"}, feed["token"]).status_code == 400


def test_epcis_transformation_event_records_production(feed):
    doc = {
        "type": "EPCISDocument",
        "epcisBody": {
            "eventList": [
                {
                    "type": "TransformationEvent",
                    "eventID": "evt-make",
                    "bizStep": "commissioning",
                    "eventTime": "2026-07-18T06:00:00+01:00",
                    "readPoint": {"id": gs1.digital_link("414", feed["factory"].gln)},
                    "outputQuantityList": [
                        {
                            "epcClass": f"https://id.gs1.org/01/{gs1.make_gtin('629123', 7346)}/10/LOT2606A",
                            "quantity": 60000,
                            "uom": "CT",
                        }
                    ],
                }
            ]
        },
    }
    assert _api(feed["client"], "/supply/api/v1/epcis/capture/", doc, feed["token"]).status_code == 201
    event = SupplyEvent.objects.get(external_id="evt-make")
    assert event.event_type == SupplyEvent.EventType.TRANSFORMATION
    assert event.read_point == feed["factory"]
    assert event.quantity_list[0]["batch_lot"] == "LOT2606A"


# ---------------------------------------------------------------------------
# Tier 3 — check-ins
# ---------------------------------------------------------------------------


def test_checkin_advances_shipment(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    shipment = Shipment.objects.get(asn_reference="ASN-2026-0587")
    resp = _api(
        feed["client"],
        "/supply/api/v1/checkins/",
        {
            "shipment_reference": shipment.reference,
            "status": "arriving",
            "location_gln": feed["hub"].gln,
            "occurred_at": "2026-07-26T14:30:00Z",
            "checkin_id": "ci-001",
        },
        feed["token"],
    )
    assert resp.status_code == 201
    event = SupplyEvent.objects.get(external_id="ci-001")
    assert event.source_tier == SupplyEvent.SourceTier.CHECKIN
    assert event.read_point == feed["hub"]


def test_checkin_is_idempotent_and_scoped_to_org(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    shipment = Shipment.objects.get(asn_reference="ASN-2026-0587")
    payload = {"shipment_reference": shipment.reference, "status": "arriving", "checkin_id": "ci-dup"}
    _api(feed["client"], "/supply/api/v1/checkins/", payload, feed["token"])
    _api(feed["client"], "/supply/api/v1/checkins/", payload, feed["token"])
    assert SupplyEvent.objects.filter(external_id="ci-dup").count() == 1

    rival = f.SupplierOrgFactory(legal_name="Rival Foods")
    _t, rival_token = tokens.mint_token(rival, "rival")
    assert _api(feed["client"], "/supply/api/v1/checkins/", payload, rival_token).status_code == 400


# ---------------------------------------------------------------------------
# Reconciliation + pull parity
# ---------------------------------------------------------------------------


def test_short_receipt_raises_a_discrepancy(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    _api(
        feed["client"],
        "/supply/api/v1/epcis/capture/",
        _epcis_doc(feed, biz_step="receiving", event_id="evt-short", qty=58200),
        feed["token"],
    )
    disc = Discrepancy.objects.get()
    assert float(disc.expected_quantity) == 60000
    assert float(disc.received_quantity) == 58200
    assert float(disc.shortfall) == 1800
    assert disc.status == Discrepancy.Status.OPEN


def test_matching_receipt_raises_no_discrepancy(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    _api(
        feed["client"],
        "/supply/api/v1/epcis/capture/",
        _epcis_doc(feed, biz_step="receiving", event_id="evt-ok"),
        feed["token"],
    )
    assert Discrepancy.objects.count() == 0


def test_pull_api_matches_the_event_log(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    _api(feed["client"], "/supply/api/v1/epcis/capture/", _epcis_doc(feed), feed["token"])
    shipment = Shipment.objects.get(asn_reference="ASN-2026-0587")

    resp = feed["client"].get(
        f"/supply/api/v1/shipments/{shipment.id}/events/",
        HTTP_AUTHORIZATION=f"Bearer {feed['token']}",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [e["source_tier"] for e in body["events"]] == ["asn", "epcis"]
    assert body["shipment"]["reference"] == shipment.reference

    # another org cannot pull it
    rival = f.SupplierOrgFactory(legal_name="Rival Foods")
    _t, rival_token = tokens.mint_token(rival, "rival")
    assert (
        feed["client"]
        .get(
            f"/supply/api/v1/shipments/{shipment.id}/events/",
            HTTP_AUTHORIZATION=f"Bearer {rival_token}",
        )
        .status_code
        == 404
    )


def test_status_never_moves_backwards(feed):
    """Events arrive out of order in the real world; state must be monotonic."""
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    _api(
        feed["client"],
        "/supply/api/v1/epcis/capture/",
        _epcis_doc(feed, biz_step="receiving", event_id="evt-r"),
        feed["token"],
    )
    assert Shipment.objects.get().status == Shipment.Status.DELIVERED
    # a late-arriving departure event must not undo the delivery
    _api(
        feed["client"],
        "/supply/api/v1/epcis/capture/",
        _epcis_doc(feed, biz_step="departing", event_id="evt-late-dep"),
        feed["token"],
    )
    assert Shipment.objects.get().status == Shipment.Status.DELIVERED


def test_milestone_delta_days_reports_lateness(feed):
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    shipment = Shipment.objects.get()
    arrive = shipment.milestones.get(kind=Milestone.Kind.ARRIVE)
    arrive.actual_at = arrive.planned_at + timedelta(days=2, hours=12)
    arrive.save()
    assert arrive.delta_days == 2.5


# ---------------------------------------------------------------------------
# GS1 helpers
# ---------------------------------------------------------------------------


def test_gs1_check_digits_round_trip():
    sscc = gs1.make_sscc("629123", 42)
    gtin = gs1.make_gtin("629123", 7346)
    gln = gs1.make_gln("629123", 5)
    assert len(sscc) == 18 and gs1.is_valid(sscc)
    assert len(gtin) == 14 and gs1.is_valid(gtin)
    assert len(gln) == 13 and gs1.is_valid(gln)
    assert not gs1.is_valid(sscc[:-1] + str((int(sscc[-1]) + 1) % 10))


def test_gs1_digital_link_parsing():
    assert gs1.parse_digital_link("https://id.gs1.org/00/394123450000000018") == ("00", "394123450000000018")
    assert gs1.parse_digital_link("urn:epc:id:sgln:0614141.07346.1234")[0] == "414"
    assert gs1.parse_digital_link(None) == (None, None)


def test_unit_ladder_conversions():
    # 150 sachets x 92 g per carton
    assert gs1.cartons_to_mt(1000) == pytest.approx(13.8)
    assert gs1.cartons_to_children(60000) == 60000


def test_arriving_implies_in_transit(feed):
    """Goods cannot arrive without having left; feeds arrive out of order."""
    _api(feed["client"], "/supply/api/v1/shipments/", _asn_payload(feed), feed["token"])
    shipment = Shipment.objects.get()
    shipment.status = Shipment.Status.PLANNED
    shipment.save(update_fields=["status"])

    _api(
        feed["client"],
        "/supply/api/v1/epcis/capture/",
        _epcis_doc(feed, biz_step="arriving", event_id="evt-arr-only"),
        feed["token"],
    )
    shipment.refresh_from_db()
    assert shipment.status == Shipment.Status.IN_TRANSIT


def test_blank_gln_does_not_bind_an_arbitrary_node(feed):
    """A despatch with empty locations must be rejected, not silently bound.

    Regression: the lookup used to be a raw ``filter(gln=...)``, so a blank
    GLN matched the first node with an empty one and attached the consignment
    to whatever that happened to be.
    """
    from connect_labs.supply.models import SupplyNode

    # a node with no GLN, exactly what a raw blank-string filter would match
    SupplyNode.objects.create(name="Unregistered Depot", kind="warehouse", country="NG", gln="")

    payload = _asn_payload(feed, asn="ASN-BLANK")
    payload["ship_from_gln"] = ""
    payload["ship_to_gln"] = ""
    resp = _api(feed["client"], "/supply/api/v1/shipments/", payload, feed["token"])
    assert resp.status_code == 400
    assert "resolve to known locations" in resp.json()["error"]
    assert not Shipment.objects.filter(asn_reference="ASN-BLANK").exists()


def test_blank_gln_lookup_helper_returns_nothing(db):
    from connect_labs.supply.models import SupplyNode
    from connect_labs.supply.services.ingestion import node_by_gln

    SupplyNode.objects.create(name="No GLN", kind="warehouse", country="NG", gln="")
    assert node_by_gln("") is None
    assert node_by_gln(None) is None
    assert node_by_gln("   ") is None


def test_blank_contract_reference_is_rejected(feed):
    """Same bug class as the blank GLN: an empty value must match nothing."""
    from connect_labs.supply.models import Contract

    # a contract whose reference is blank, which a naive lookup would match
    Contract.objects.create(
        award=f.AwardFactory(),
        org=feed["org"],
        reference="",
        total_quantity=1,
        unit_price=1,
    )
    payload = _asn_payload(feed, asn="ASN-NOCONTRACT")
    payload["contract_reference"] = ""
    resp = _api(feed["client"], "/supply/api/v1/shipments/", payload, feed["token"])
    assert resp.status_code == 400
    assert "contract_reference is required" in resp.json()["error"]


def test_integrity_error_without_an_idempotency_key_is_not_swallowed(feed):
    """Only a duplicate external_id is recoverable; other failures must surface."""
    from unittest import mock

    from django.db import IntegrityError

    from connect_labs.supply.services import ingestion

    with mock.patch(
        "connect_labs.supply.services.ingestion._core.SupplyEvent.objects.create",
        side_effect=IntegrityError("something else entirely"),
    ):
        with pytest.raises(IntegrityError):
            ingestion.capture_event(
                feed["org"],
                biz_step="departing",
                event_time=ingestion.parse_event_time("2026-07-20T09:00:00Z"),
                source_tier="portal",
                external_id="",
            )
