"""No vocabulary code reaches a person as the code.

Five filmed procurements put these screens in front of judges, and every one
found the same thing somewhere: `jerry_can`, `programme_org`, `at_customs`,
`stock_below_minimum` on a page meant for a procurement lead. Each was fixed
where it was seen and the next walkthrough found another. This test is the net
under all of them: it seeds one programme that touches every vocabulary, renders
every main supply page, and fails on any snake_case value from `records.py` (or
a check kind, or a unit) that appears in the text a reader sees.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.
"""

import re
import sys
from datetime import date, timedelta
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import records
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.templatetags.supply_chain_extras import CHECK_LABELS

pytestmark = pytest.mark.django_db

PROGRAM = 10631
TODAY = date.today()

_VOCABULARIES = (
    records.BUYER_OF_RECORD,
    records.SOURCES,
    records.SUPPLY_POINT_KINDS,
    records.MOVEMENT_KINDS,
    records.STOCK_COUNT_KINDS,
    records.SHIPMENT_STATUSES,
    records.CONTRACT_STATUSES,
    records.INVOICE_STATUSES,
    records.DOCUMENT_KINDS,
    records.APPROVAL_ROLES,
    records.APPROVAL_STATUSES,
    records.CHARGE_KINDS,
    records.BASIS,
    records.CONSIDERATIONS,
    records.PAYMENT_TERMS,
    records.STOCK_CLASSES,
    records.SUPPLIER_TYPES,
    records.COMPONENTS_PER,
    tuple(CHECK_LABELS),
    # The units and pricing bases the seed below uses.
    ("jerry_can", "per_base_unit", "per_pack", "per_lot_total", "per_metric_tonne"),
)

# Only codes that are not already a word: "planned" or "donor" on a page IS the
# word, and would match every sentence that uses it.
CODES = sorted({code for vocabulary in _VOCABULARIES for code in vocabulary if "_" in code})


class _VisibleText(HTMLParser):
    """The text a reader sees: element content, not attributes, scripts or styles."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self._hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template") and self._hidden:
            self._hidden -= 1

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def visible_text(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    return " ".join(parser.parts)


def raw_codes_in(html: str) -> list[str]:
    text = visible_text(html)
    return [code for code in CODES if re.search(rf"(?<![\w-]){re.escape(code)}(?![\w-])", text)]


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def client_in_programme(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import (  # noqa: F401  -- import every screen module before patching
        distribution_views,
        form_views,
        fulfilment_views,
        network_views,
        reference_views,
        stock_views,
        views,
    )
    from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401
    from connect_labs.supply_chain.update_links import views as link_views  # noqa: F401

    account = django_user_model.objects.create_user(username="codes", password="x", email="codes@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for name, module in list(sys.modules.items()):
        if not name.startswith("connect_labs.supply_chain") or name.endswith("api_views"):
            continue
        if hasattr(module, "_access"):
            monkeypatch.setattr(module, "_access", _scoped)
        if hasattr(module, "has_program_context"):
            monkeypatch.setattr(module, "has_program_context", lambda request: True)
    return client


@pytest.fixture
def world(da):
    """One programme touching every vocabulary a page can show."""
    op(
        da,
        "commodity_upsert",
        data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L", "pack_unit": "jerry_can", "base_per_pack": 20},
    )
    op(da, "commodity_upsert", data={"slug": "dispenser", "name": "Chlorine dispenser", "category": "equipment"})
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "cl-20",
            "name": "Chlorine 20 L",
            "commodity_slug": "chlorine",
            "base_unit": "L",
            "pack_unit": "jerry_can",
            "base_per_pack": 20,
        },
    )
    dispenser = op(
        da,
        "item_upsert",
        data={"sku": "disp", "name": "Wall dispenser", "commodity_slug": "dispenser", "stock_class": "durable"},
    )
    supplier = op(da, "supplier_create", data={"name": "Sahel Chemicals", "type": "distributor"})
    donor = op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    customs = op(da, "org_upsert", data={"slug": "customs", "name": "Customs service"})
    regulator = op(da, "org_upsert", data={"slug": "regulator", "name": "The regulator"})
    round_ = op(
        da,
        "round_create",
        data={
            "label": "Stop-gap chlorine",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "chlorine", "quantity": "600", "quantity_unit": "jerry_can"}],
        },
    )
    quote = op(
        da,
        "quote_record",
        data={
            "round_id": round_["id"],
            "commodity_slug": "chlorine",
            "supplier_id": supplier["id"],
            "item_id": item["id"],
            "as_quoted_amount": "4.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "jerry_can",
            "pack_spec_source": "trade_item_confirmed",
            "freight_basis": "included",
            "duties_basis": "not_specified",
        },
    )
    award = op(da, "award_create", round_id=round_["id"], quote_id=quote["id"], rationale="registered locally")
    approval = op(
        da,
        "approval_request",
        data={
            "award_id": award["id"],
            "approver_org_id": regulator["id"],
            "role": "regulatory",
            "requested_on": (TODAY - timedelta(days=3)).isoformat(),
        },
    )
    # A kit stated per pack, with a requirement on its product and a figure
    # stated for a product inside it: the specification editors and the
    # "one kit holds" line render these.
    op(da, "commodity_upsert", data={"slug": "dpd1_reagent", "name": "DPD No. 1 tablet", "base_unit": "tablet"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "test_kit",
            "name": "Free chlorine test kit",
            "category": "diagnostic",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "spec_requirements": [
                {
                    "field": "range_max_mg_per_l",
                    "operator": ">=",
                    "value": 2.0,
                    "unit": "mg/L",
                    "rationale": "reads the dose",
                }
            ],
        },
    )
    kit = op(
        da,
        "item_upsert",
        data={
            "sku": "kit-50",
            "name": "Kit of 50",
            "commodity_slug": "test_kit",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "components_per": "pack",
            "spec_attributes": {"range_max_mg_per_l": 1.5},
            "components": [
                {
                    "commodity_slug": "dpd1_reagent",
                    "quantity": "50",
                    "base_unit": "tablet",
                    "spec_attributes": {"shelf_life_months": 24},
                }
            ],
        },
    )
    # The approver's own link, answered through, so its page and the award's
    # attribution line render with an answer on them.
    approver_link = op(da, "update_link_issue", data={"org_id": regulator["id"], "approval_ids": [approval["id"]]})
    store = op(
        da,
        "supply_point_upsert",
        data={"slug": "wh", "name": "Central warehouse", "kind": "central_store", "source": "we_recorded"},
    )
    op(
        da,
        "supply_point_upsert",
        data={"slug": "port", "name": "Port bond", "kind": "customs", "source": "we_recorded"},
    )
    field = op(
        da,
        "supply_point_upsert",
        data={"slug": "clinic", "name": "Clinic", "kind": "facility", "source": "partner_reported"},
    )
    contract = op(
        da,
        "contract_create",
        data={
            "round_id": round_["id"],
            "supplier_id": supplier["id"],
            "item_id": item["id"],
            "commodity_slug": "chlorine",
            "buyer_of_record": "partner_org",
            "buyer_org_id": us["id"],
            "reference": "CL-1",
            "quantity": "600",
            "quantity_unit": "jerry_can",
            "unit_price": "4.00",
            "unit_price_unit": "per_pack",
            "currency": "USD",
            "freight_basis": "included",
            "duties_basis": "not_specified",
            "vat_basis": "not_specified",
            "payment_terms": "on_delivery",
            "status": "part_received",
            "source": "we_recorded",
        },
    )
    in_kind = op(
        da,
        "contract_create",
        data={
            "supplier_id": donor["id"],
            "item_id": dispenser["id"],
            "commodity_slug": "dispenser",
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "reference": "DON-1",
            "quantity": "40",
            "quantity_unit": "dispenser",
            "consideration": "in_kind",
            "source": "we_recorded",
        },
    )
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "AWB-1",
            "status": "at_customs",
            "source": "supplier_reported",
            "expected_on": (TODAY - timedelta(days=5)).isoformat(),
            "required_documents": [
                {"kind": "airway_bill", "owed_by_org_id": us["id"]},
                {"kind": "product_registration", "owed_by_org_id": regulator["id"]},
            ],
            "lines": [
                {"item_id": item["id"], "batch": "B1", "quantity": "83.7209", "quantity_unit": "jerry_can"},
            ],
        },
    )
    op(
        da,
        "charge_record",
        data={
            "shipment_id": shipment["id"],
            "kind": "customs_fee",
            "payee_org_id": customs["id"],
            "amount": "612000",
            "currency": "NGN",
            "source": "we_recorded",
        },
    )
    op(
        da,
        "document_attach",
        data={
            "kind": "certificate_of_analysis",
            "contract_id": contract["id"],
            "external_url": "https://example.org/coa.pdf",
            "source": "document",
        },
    )
    op(
        da,
        "receipt_record",
        data={
            "contract_id": contract["id"],
            "supply_point_id": store["id"],
            "received_on": TODAY.isoformat(),
            "reference": "GRN-1",
            "source": "partner_reported",
            "lines": [{"item_id": item["id"], "batch": "B1", "quantity_accepted": "1", "quantity_unit": "jerry_can"}],
        },
    )
    op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "reference": "INV-1",
            "amount": "2400",
            "currency": "USD",
            "quantity_billed": "600",
            "quantity_unit": "jerry_can",
            "source": "supplier_reported",
        },
    )
    op(
        da,
        "movement_record",
        data={
            "kind": "transfer",
            "occurred_on": TODAY.isoformat(),
            "commodity_slug": "chlorine",
            "item_id": item["id"],
            "from_supply_point_id": store["id"],
            "to_supply_point_id": field["id"],
            "quantity": "1",
            "quantity_unit": "jerry_can",
            "source": "we_recorded",
        },
    )
    op(
        da,
        "stock_count_record",
        data={
            "kind": "physical_count",
            "supply_point_id": store["id"],
            "commodity_slug": "chlorine",
            "counted_on": TODAY.isoformat(),
            "quantity": "3",
            "quantity_unit": "jerry_can",
            "source": "partner_reported",
        },
    )
    op(
        da,
        "alert_subscription_create",
        data={"check_kinds": ["stock_below_minimum", "shipment_overdue"], "recipient_email": "stores@example.org"},
    )
    return {
        "round": round_,
        "quote": quote,
        "award": award,
        "contract": contract,
        "in_kind": in_kind,
        "shipment": shipment,
        "supplier": supplier,
        "donor": donor,
        "item": item,
        "kit": kit,
        "approval": approval,
        "approver_link": approver_link,
    }


PAGES = [
    ("home", lambda w: []),
    ("catalogue", lambda w: []),
    ("product_detail", lambda w: ["chlorine"]),
    ("item_detail", lambda w: [w["item"]["id"]]),
    ("suppliers", lambda w: []),
    ("supplier_detail", lambda w: [w["supplier"]["id"]]),
    ("supplier_detail", lambda w: [w["donor"]["id"]]),
    ("procurement_round_board", lambda w: []),
    ("procurement_round_detail", lambda w: [w["round"]["id"]]),
    ("procurement_quote_detail", lambda w: [w["quote"]["id"]]),
    ("award_detail", lambda w: [w["award"]["id"]]),
    ("orders", lambda w: []),
    ("order_detail", lambda w: [w["contract"]["id"]]),
    ("order_detail", lambda w: [w["in_kind"]["id"]]),
    ("shipment_detail", lambda w: [w["shipment"]["id"]]),
    ("checks", lambda w: []),
    ("network", lambda w: []),
    ("stock", lambda w: []),
    ("distribution", lambda w: []),
    ("alerts", lambda w: []),
    ("update_links", lambda w: []),
    ("organisations", lambda w: []),
    # The forms: a select's options are text a reader sees too.
    ("contract_edit", lambda w: [w["contract"]["id"]]),
    ("document_attach", lambda w: [w["contract"]["id"]]),
    ("shipment_record", lambda w: [w["contract"]["id"]]),
    ("receipt_record", lambda w: [w["contract"]["id"]]),
    ("invoice_record", lambda w: [w["contract"]["id"]]),
    ("shipment_status", lambda w: [w["shipment"]["id"]]),
    ("shipment_document_attach", lambda w: [w["shipment"]["id"]]),
    ("charge_record", lambda w: [w["shipment"]["id"]]),
    ("approval_request", lambda w: [w["award"]["id"]]),
    ("approval_decide", lambda w: [w["approval"]["id"]]),
    ("product_edit", lambda w: ["test_kit"]),
    ("product_detail", lambda w: ["test_kit"]),
    ("item_detail", lambda w: [w["kit"]["id"]]),
    ("item_edit", lambda w: [w["kit"]["id"]]),
    ("supplier_create", lambda w: []),
    ("supplier_edit", lambda w: [w["donor"]["id"]]),
    ("item_edit", lambda w: [w["item"]["id"]]),
    ("movement_record", lambda w: []),
    ("stock_count_record", lambda w: []),
    ("distribution_record", lambda w: []),
    ("supply_point_create", lambda w: []),
    ("alert_create", lambda w: []),
    ("update_link_issue", lambda w: []),
]


@pytest.mark.parametrize("name,args", PAGES, ids=[f"{name}-{i}" for i, (name, _) in enumerate(PAGES)])
def test_no_vocabulary_code_reaches_the_page(client_in_programme, world, name, args):
    response = client_in_programme.get(reverse(f"supply_chain:{name}", args=args(world)))
    assert response.status_code == 200
    assert raw_codes_in(response.content.decode()) == []


def test_the_comparison_reads_as_words(client_in_programme, world):
    url = reverse("supply_chain:procurement_comparison", args=[world["round"]["id"]]) + "?commodity=chlorine"
    response = client_in_programme.get(url)
    assert response.status_code == 200
    assert raw_codes_in(response.content.decode()) == []


def test_the_approvers_own_page_reads_as_words(client, world):
    """The one page an outside organisation reads with no labs account."""
    url = reverse("supply_chain:update_link_public", args=[world["approver_link"]["token"]])
    response = client.get(url)
    assert response.status_code == 200
    assert "Record your answer" in response.content.decode()
    assert raw_codes_in(response.content.decode()) == []


def test_the_answered_award_reads_as_words(client_in_programme, world):
    from connect_labs.supply_chain.models import AwardApproval
    from connect_labs.supply_chain.update_links import service
    from connect_labs.supply_chain.update_links.models import UpdateLink

    service.submit(
        UpdateLink.objects.get(pk=world["approver_link"]["id"]),
        "record_answer",
        {"approval": AwardApproval.objects.get(pk=world["approval"]["id"]), "status": "approved", "note": "ok"},
    )
    response = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]]))
    body = response.content.decode()
    assert "own link" in body
    assert raw_codes_in(body) == []


def test_the_detector_finds_a_code_and_ignores_attributes():
    assert raw_codes_in("<p>3 jerry_can</p>") == ["jerry_can"]
    assert raw_codes_in('<a href="?kind=airway_bill">Airway bill</a>') == []
    assert raw_codes_in("<script>var k = 'at_customs';</script>") == []
