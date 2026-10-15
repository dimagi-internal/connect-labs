"""The RUTF rehearsal world: one open tender, Kanem invited on 6 Jul, Northgate a second supplier.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Every address uses `.example.invalid`.

Not a test module: the tender, order and overview feature tests import its fixtures and
helpers from here, so no test module imports another.
"""

import datetime
import html
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.models import (
    Quote,
    Tender,
)
from connect_labs.supply_chain.operations import call_operation

PROGRAM = 20877


TODAY = datetime.date(2026, 10, 1)


WHEN = datetime.datetime(2026, 10, 1, 9, 0, tzinfo=datetime.UTC)


KANEM_EMAIL = "Our price for RUTF is USD 54.50 per carton, CPT Kano. Quote valid 45 days."


def _synthetic(program_id):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=program_id,
        program_id=program_id,
        labs_only=True,
        enabled=True,
        label="tracking reality tests",
        allowed_domains=["dimagi.com"],
    )


@pytest.fixture
def da(db):
    _synthetic(PROGRAM)
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


def op(da, name, *, channel="mcp", **payload):
    with seed_overrides(PROGRAM, recorded_at=WHEN):
        return call_operation(name, da, payload, channel=channel)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
    us = op(da, "org_upsert", data={"slug": "tracking-buyer", "name": "Rehearsal buyer"})
    tender = op(
        da,
        "tender_create",
        data={
            "label": "RUTF round",
            "lines": [{"commodity_slug": "rutf", "quantity": 2000, "quantity_unit": "carton"}],
            "delivery_points": [{"key": "kano", "name": "Warehouse, Kano", "city": "Kano", "country": "NG"}],
        },
    )
    op(da, "tender_open", tender_id=tender["id"])
    kanem = op(
        da,
        "supplier_create",
        data={
            "name": "Kanem Foods Rehearsal",
            "contacts": [{"name": "Grace", "email": "grace@kanem.example.invalid"}],
        },
    )
    northgate = op(da, "supplier_create", data={"name": "Northgate Rehearsal Commodities"})
    outreach = op(
        da, "outreach_log", data={"tender_id": tender["id"], "supplier_id": kanem["id"], "sent_on": "2026-07-06"}
    )
    return {"us": us, "tender": tender, "kanem": kanem, "northgate": northgate, "outreach": outreach}


def _kanem_quote(world, **overrides):
    return {
        "tender_id": world["tender"]["id"],
        "supplier_id": world["kanem"]["id"],
        "commodity_slug": "rutf",
        "as_quoted_amount": "54.50",
        "as_quoted_unit": "per_pack",
        "as_quoted_currency": "USD",
        "freight_basis": "included",
        "duties_basis": "excluded",
        "delivery_point_keys": ["kano"],
        **overrides,
    }


def _contract(da, world, **overrides):
    return op(
        da,
        "contract_create",
        channel="web",
        data={
            "tender_id": world["tender"]["id"],
            "supplier_id": world["kanem"]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": world["us"]["id"],
            "reference": "PO-REH-1",
            "status": "placed",
            "currency": "USD",
            "quantity": 2000,
            "quantity_unit": "carton",
            "unit_price": "49.80",
            "unit_price_unit": "per_pack",
            "freight_basis": "excluded",
            "freight_amount": "7200.00",
            "source": "we_recorded",
            **overrides,
        },
    )


def _held_on_our_form_m(da, world):
    contract = _contract(da, world, signed_on="2026-07-28", promised_lead_time_days=35)
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "PO-REH-1",
            "status": "dispatched",
            "dispatched_on": "2026-08-26",
            "expected_on": "2026-09-05",
            "source": "supplier_reported",
            "lines": [{"quantity": 2000, "quantity_unit": "carton"}],
        },
    )
    op(
        da,
        "shipment_update",
        source={"ref": "<cfc-1@forwarder.example.invalid>", "sender": "Crescent Rehearsal Freight"},
        shipment_id=shipment["id"],
        data={
            "status": "at_customs",
            "expected_on": "2026-09-19",
            "required_documents": [{"kind": "import_permit", "owed_by_org_id": world["us"]["id"]}],
        },
    )
    return contract, shipment


class _ProgramContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def client_in_program(client, monkeypatch, settings, da, django_user_model):
    from connect_labs.supply_chain import views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    sophie = django_user_model.objects.create_user(
        username="sophie-reh", email="sophie@example.invalid", name="Sophie", password="x"
    )
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
    monkeypatch.setattr("connect_labs.supply_chain.views._access", lambda request: da)
    monkeypatch.setattr("connect_labs.supply_chain.procurement.views._access", lambda request: da)
    client.force_login(sophie)
    session = client.session
    session["labs_oauth"] = {"organization_data": {"programs": [{"id": PROGRAM, "name": "Connect-RUTF"}]}}
    session.save()
    return client


@pytest.fixture
def web(client_in_program, da, monkeypatch):
    """The same signed-in client, able to submit the write screens too."""
    from connect_labs.supply_chain import form_views

    monkeypatch.setattr(form_views, "_access", lambda request: da)
    monkeypatch.setattr(form_views, "has_program_context", lambda request: True)
    return client_in_program


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


def _tender_page(client, tender_id, query=""):
    return client.get(reverse("supply_chain:procurement_tender_detail", args=[tender_id]) + query).content.decode()


def _chase(da, outreach_id, day, recorded_at):
    with seed_overrides(PROGRAM, recorded_at=recorded_at):
        call_operation(
            "outreach_update", da, {"outreach_id": outreach_id, "data": {"last_reminder_on": day}}, channel="web"
        )


def _row(body, outreach_id):
    return re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)


def _order(client, contract_id):
    return client.get(reverse("supply_chain:order_detail", args=[contract_id])).content.decode()


def _comparable(world, **overrides):
    """Kanem's quote with everything the comparison needs stated."""
    return _kanem_quote(
        world,
        duties_basis="included",
        pack_spec_source="stated_on_quote",
        base_per_pack_stated=150,
        base_unit_grams_stated=92,
        quantity_basis=2000,
        quantity_basis_unit="carton",
        **overrides,
    )


def _answer_as_sophie(da, commitment_id, resolution):
    """Resolved on the web by Sophie (the page fixture's user), as the screen would."""
    from django.contrib.auth import get_user_model

    sophie = get_user_model().objects.get(username="sophie-reh")
    with seed_overrides(PROGRAM, actor=sophie, recorded_at=datetime.datetime(2026, 10, 2, 9, tzinfo=datetime.UTC)):
        call_operation(
            "commitment_resolve",
            da,
            {"commitment_id": commitment_id, "resolution": resolution, "resolved_on": "2026-10-02"},
            channel="web",
        )


def _question(da, world, text="Who is the importer of record?"):
    return op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": world["northgate"]["id"],
            "tender_id": world["tender"]["id"],
            "text": text,
            "raised_on": "2026-07-11",
            "source": "supplier_reported",
        },
    )


def _quote(da, world, **overrides):
    """Kanem's quote: freight included, duties excluded with no amount."""
    recorded = op(da, "quote_record", data=_kanem_quote(world, **overrides))
    return Quote.objects.select_related("tender", "commodity").get(pk=recorded["id"])


def _set(da, world, terms, estimate=None):
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms=terms, duty_estimate_percent=estimate)
    return Tender.objects.get(pk=world["tender"]["id"])


def _waiver(da, world):
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="buyer_waiver")
    return _quote(da, world)


def _overbilled(da, world):
    contract = _contract(da, world)
    invoice = op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "reference": "INV-REH-1",
            "issued_on": "2026-09-21",
            "currency": "USD",
            "amount": "110350.00",
            "unit_price": "51.20",
            "freight_amount": "7950.00",
            "quantity_billed": 2000,
            "quantity_unit": "carton",
            "source": "supplier_reported",
        },
    )
    return contract, invoice


WAIVED = {"duties_basis": "excluded", "duties_amount": "0.00", "vat_basis": "included", "incoterm": "CPT Kano"}


FORWARDER = "Rehearsal Freight & Clearing"


HELD_EMAIL = "HELD AT BORDER - DOCUMENTATION (FORM M) / CONSIGNEE TO PROVIDE FORM M / PAAR."


def _held_by_the_forwarders_email(da, world):
    """An order costed at no duty on a relief, its shipment held on Form M and PAAR by an email."""
    contract = _contract(da, world, **WAIVED)
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "TRK-B3",
            "status": "dispatched",
            "dispatched_on": "2026-08-28",
            "expected_on": "2026-09-14",
            "source": "supplier_reported",
        },
    )
    op(
        da,
        "shipment_update",
        source={"ref": "<held-1@forwarder.example.invalid>", "sender": FORWARDER, "excerpt": HELD_EMAIL},
        shipment_id=shipment["id"],
        data={
            "status": "at_customs",
            "required_documents": [
                {"kind": "import_permit", "name": "Form M", "owed_by_org_id": world["us"]["id"]},
                {"kind": "customs_declaration", "name": "PAAR", "owed_by_org_id": world["us"]["id"]},
            ],
        },
    )
    return contract


def _estimate(da, world, per_unit="1.20"):
    op(da, "tender_set_import_estimates", tender_id=world["tender"]["id"], clearing_estimate_per_unit=per_unit)


def _shipment(da, contract, world, *, status="at_customs", required=True):
    return op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "TRK-1",
            "status": status,
            "carrier": "Rehearsal Freight & Clearing",
            "source": "supplier_reported",
            "required_documents": (
                [
                    {"kind": "import_permit", "name": "Form M", "owed_by_org_id": world["us"]["id"]},
                    {"kind": "customs_declaration", "name": "PAAR", "owed_by_org_id": world["us"]["id"]},
                ]
                if required
                else []
            ),
        },
    )


REPLY_TODAY_REF = "<reply-b2@mail.kanem.example.invalid>"


def _reply_today(da, world):
    """A reply recorded the day it came in (the world's own recording day)."""
    source = {"ref": REPLY_TODAY_REF, "excerpt": "Our price is USD 54.50 a carton, CPT Kano.", "sender": "Grace"}
    op(
        da,
        "outreach_update",
        source=source,
        outreach_id=world["outreach"]["id"],
        data={
            "responded": True,
            "response_kind": "quote",
            "responded_on": timezone.localtime(WHEN).date().isoformat(),
        },
    )
    op(da, "quote_record", source=source, data=_kanem_quote(world))


def _attach(da, shipment, kind):
    return op(
        da,
        "document_attach",
        data={
            "kind": kind,
            "shipment_id": shipment["id"],
            "external_url": "https://example.invalid/doc.pdf",
            "source": "document",
        },
    )
