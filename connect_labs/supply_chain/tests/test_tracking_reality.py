"""The record tracks reality: the fixes from Sophie's email rehearsal.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

docs/superpowers/specs/2026-10-02-supply-tracking-reality.md holds the
rulings; each test names the one it pins. The rehearsal's findings are in
docs/superpowers/specs/2026-10-01-sophie-email-rehearsal.md (local branch).
"""

import datetime

import jsonschema
import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.checks import run_checks
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.models import Contract, Payment, Quote, Shipment
from connect_labs.supply_chain.operations import call_operation, declared_only
from connect_labs.supply_chain.procurement.repository import SuspectedDuplicate
from connect_labs.supply_chain.standing import standing_rows

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


# ---- ruling 1: an undeclared field is refused, by name, and nothing is written


class TestUnknownFieldsAreRefused:
    def test_a_guessed_field_is_refused_naming_it_and_the_real_ones(self, da, world):
        before = Quote.objects.count()
        with pytest.raises(jsonschema.ValidationError) as refused:
            op(da, "quote_record", data=_kanem_quote(world, quoted_on="2026-07-09"))
        assert "'quoted_on'" in refused.value.message
        assert "Nothing was recorded" in refused.value.message
        assert "received_on" in refused.value.message  # the real name, so the retry is right
        assert Quote.objects.count() == before

    def test_an_invoice_unit_price_is_kept_now_rather_than_dropped(self, da, world):
        contract = _contract(da, world)
        invoice = op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "amount": "110350.00",
                "unit_price": "51.20",
                "freight_amount": "7950.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        assert (invoice["unit_price"], invoice["freight_amount"]) == ("51.2", "7950")

    def test_the_fields_the_rehearsal_needed_are_declared_and_stored(self, da, world):
        quote = op(
            da,
            "quote_record",
            data=_kanem_quote(
                world,
                received_on="2026-07-09",
                validity_until="2026-08-23",
                incoterm="CPT Kano",
                quantity_basis=2000,
                quantity_basis_unit="carton",
                moq=500,
                moq_unit="carton",
                payment_terms="50% with order, 50% before loading",
                supplier_reference="KF/Q/2611",
            ),
        )
        stored = Quote.objects.get(pk=quote["id"])
        assert stored.received_on == datetime.date(2026, 7, 9)
        assert stored.validity_until == datetime.date(2026, 8, 23)
        assert (stored.incoterm, stored.quantity_basis_unit, stored.moq_unit) == ("CPT Kano", "carton", "carton")
        assert stored.payment_terms == "50% with order, 50% before loading"
        assert stored.supplier_reference == "KF/Q/2611"
        reply = op(
            da,
            "outreach_update",
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        assert reply["responded_on"] == "2026-07-09"

    def test_a_nested_line_refuses_an_unknown_key_too(self, da, world):
        contract = _contract(da, world)
        with pytest.raises(jsonschema.ValidationError, match="'pallets'"):
            op(
                da,
                "shipment_record",
                data={
                    "contract_id": contract["id"],
                    "source": "supplier_reported",
                    "lines": [{"quantity": 10, "quantity_unit": "carton", "pallets": 2}],
                },
            )

    def test_a_seeder_cuts_what_an_operation_does_not_take_and_is_told(self, db):
        payload, dropped = declared_only("commodity_upsert", {"data": {"slug": "x", "components": []}})
        assert payload == {"data": {"slug": "x"}}
        assert dropped == ["data.components"]


# ---- ruling 2: a forwarded copy is not a second live quote


class TestDuplicateForwards:
    def test_the_same_offer_forwarded_inline_is_refused_with_the_first_ones_evidence(self, da, world):
        first = op(
            da,
            "quote_record",
            source={"ref": "<kanem-1@kanem.example.invalid>", "excerpt": KANEM_EMAIL},
            data=_kanem_quote(world, validity_until="2026-08-23"),
        )
        # The inline forward: only the forward's own Message-ID, and the AI read
        # the validity slightly differently (left it out).
        with pytest.raises(SuspectedDuplicate) as refused:
            op(da, "quote_record", source={"ref": "<fwd-77@mail.example.invalid>"}, data=_kanem_quote(world))
        message = str(refused.value)
        assert f"quote {first['id']}" in message
        assert "<kanem-1@kanem.example.invalid>" in message and "USD 54.50 per carton" in message
        assert Quote.objects.filter(supplier_id=world["kanem"]["id"], voided=False).count() == 1

    def test_the_same_supplier_reference_is_the_same_offer_whatever_the_price_read(self, da, world):
        op(da, "quote_record", data=_kanem_quote(world, supplier_reference="KF/Q/2611"))
        with pytest.raises(SuspectedDuplicate):
            op(da, "quote_record", data=_kanem_quote(world, as_quoted_amount="45.50", supplier_reference="kf/q/2611"))

    def test_a_named_second_offer_is_recorded(self, da, world):
        first = op(da, "quote_record", data=_kanem_quote(world))
        second = op(da, "quote_record", data=_kanem_quote(world), distinct_from_quote_ids=[first["id"]])
        assert second["id"] != first["id"]

    def test_a_different_offer_from_the_same_supplier_is_not_refused(self, da, world):
        op(da, "quote_record", data=_kanem_quote(world, received_on="2026-07-09"))
        other = op(da, "quote_record", data=_kanem_quote(world, as_quoted_amount="52.00", received_on="2026-07-20"))
        assert other["id"]


# ---- ruling 3: real dates are domain fields; history keeps the recorded time


class TestRealDates:
    def test_the_tender_page_shows_when_the_reply_came_not_when_it_was_recorded(self, da, world, client_in_program):
        op(
            da,
            "outreach_update",
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        assert "9 Jul 2026" in body

    def test_the_tender_page_shows_when_a_silent_supplier_was_last_chased(self, da, world, client_in_program):
        op(
            da,
            "outreach_update",
            channel="web",
            outreach_id=world["outreach"]["id"],
            data={"last_reminder_on": "2026-07-13"},
        )
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        assert "Last chased" in body
        cell = body.split('data-testid="last-chased"', 1)[1].split("</td>", 1)[0]
        assert "13 Jul 2026" in cell


# ---- ruling 4: an invoice above the contract is a check, on the order page


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


class TestInvoiceAboveContract:
    def _bill(self, da, contract, **data):
        return op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
                **data,
            },
        )

    def test_a_higher_unit_price_freight_and_total_are_each_named(self, da, world):
        contract = _contract(da, world)
        self._bill(da, contract, amount="110350.00", unit_price="51.20", freight_amount="7950.00")
        found = [c for c in run_checks(da, kinds=["invoice_above_contract"]) if c["subject"]["id"] == contract["id"]]
        assert len(found) == 1 and found[0]["audience"] == "supplier"
        above = {line["field"]: line for line in found[0]["facts"]["above"]}
        assert (above["unit_price"]["billed"], above["unit_price"]["agreed"]) == ("51.2", "49.8")
        assert above["freight_amount"]["billed"] == "7950"
        assert above["total"]["difference"] == "3550"

    def test_an_invoice_at_the_contract_price_raises_nothing(self, da, world):
        contract = _contract(da, world)
        self._bill(da, contract, amount="106800.00", unit_price="49.80", freight_amount="7200.00")
        assert not run_checks(da, kinds=["invoice_above_contract"])

    def test_the_order_page_says_so(self, da, world, client_in_program):
        contract = _contract(da, world)
        self._bill(da, contract, amount="110350.00", unit_price="51.20")
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert 'data-testid="invoice-above-contract"' in body
        assert "51.20 per carton against 49.80 agreed" in body


# ---- ruling 5: a delay of ours does not read as the supplier's


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


class TestWhoseMoveItIs:
    def test_the_overview_says_waiting_on_us_not_arrival(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.contract_id == contract["id"])
        # Rule (a): the held document is ours to provide, and the row's next move says so.
        assert row.whose == "us"
        assert row.next_move.rule == "owed"
        assert row.next_move.text.startswith("Provide 1 document to ")
        assert "import permit" in row.next_move.detail

    def test_the_lateness_check_is_ours_to_answer_and_says_why(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        late = next(
            c
            for c in run_checks(da, kinds=["contract_delivery_overdue"], as_of=TODAY)
            if c["subject"]["id"] == contract["id"]
        )
        assert late["audience"] == "internal"
        assert late["facts"]["waiting_on"] == "us"
        assert late["facts"]["held_on_us"][0]["what"] == "import permit"

    def test_once_the_document_is_on_file_the_supplier_has_the_move_again(self, da, world):
        contract, shipment = _held_on_our_form_m(da, world)
        op(
            da,
            "document_attach",
            data={
                "kind": "import_permit",
                "shipment_id": shipment["id"],
                "external_url": "https://docs.example.invalid/form-m",
                "source": "we_recorded",
            },
        )
        late = next(
            c
            for c in run_checks(da, kinds=["contract_delivery_overdue"], as_of=TODAY)
            if c["subject"]["id"] == contract["id"]
        )
        assert late["audience"] == "supplier" and late["facts"]["waiting_on"] == "supplier"

    def test_the_order_page_names_our_move(self, da, world, client_in_program):
        contract, _ = _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert 'data-testid="waiting-on-us"' in body and "import permit" in body
        # Objective state, with whose move defined on hover, not a verdict sentence (DDD 003 batch 1).
        assert 'data-testid="waiting-on-us-help"' in body and "the next move is ours" not in body


# ---- ruling 6: an advance is paid against the order, and its invoice acknowledges it


class TestAdvancePayments:
    def test_an_advance_is_recorded_before_any_invoice(self, da, world):
        contract = _contract(da, world, payment_terms="advance")
        paid = op(
            da,
            "payment_record",
            channel="web",
            data={
                "contract_id": contract["id"],
                "paid_on": "2026-07-28",
                "amount": "53400.00",
                "source": "we_recorded",
            },
        )
        assert paid["invoice_id"] is None and paid["contract_id"] == contract["id"]
        match = op(da, "contract_match", contract_id=contract["id"])
        assert match["paid_amount"]["amount"] == "53400"

    def test_the_invoice_that_acknowledges_it_matches_and_confirms_it(self, da, world):
        contract = _contract(da, world, payment_terms="advance")
        paid = op(
            da,
            "payment_record",
            channel="web",
            data={
                "contract_id": contract["id"],
                "paid_on": "2026-07-28",
                "amount": "53400.00",
                "source": "we_recorded",
            },
        )
        assert [c for c in run_checks(da, kinds=["payment_unconfirmed"], as_of=TODAY)]
        invoice = op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "issued_on": "2026-09-21",
                "amount": "110350.00",
                "acknowledges_payment_ids": [paid["id"]],
                "source": "supplier_reported",
            },
        )
        payment = Payment.objects.get(pk=paid["id"])
        assert payment.invoice_id == invoice["id"]
        assert payment.confirmed_by_payee_on == datetime.date(2026, 9, 21)
        assert not run_checks(da, kinds=["payment_unconfirmed"], as_of=TODAY)
        assert op(da, "invoice_list", contract_id=contract["id"])[0]["status"] == "part_paid"

    def test_an_invoice_cannot_acknowledge_another_orders_payment(self, da, world):
        mine = _contract(da, world)
        theirs = _contract(da, world, reference="PO-REH-2")
        paid = op(
            da,
            "payment_record",
            channel="web",
            data={"contract_id": theirs["id"], "paid_on": "2026-07-28", "amount": "10.00", "source": "we_recorded"},
        )
        with pytest.raises(ValueError, match="paid against order"):
            op(
                da,
                "invoice_record",
                data={
                    "contract_id": mine["id"],
                    "acknowledges_payment_ids": [paid["id"]],
                    "source": "supplier_reported",
                },
            )


# ---- ruling 7: what we owe them has a home, and reads as waiting on us


class TestWhatWeOwe:
    def _questions(self, da, world):
        for text in ("One warehouse in Kano, or several facilities?", "Who is the importer of record?"):
            op(
                da,
                "commitment_record",
                source={"ref": "<ng-1@northgate.example.invalid>"},
                data={
                    "kind": "question",
                    "supplier_id": world["northgate"]["id"],
                    "tender_id": world["tender"]["id"],
                    "text": text,
                    "raised_on": "2026-07-11",
                    "source": "supplier_reported",
                },
            )

    def test_the_overview_says_we_owe_answers_first(self, da, world):
        self._questions(da, world)
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.tender_id == world["tender"]["id"])
        assert row.whose == "us"
        assert row.next_move.text == "Reply to Northgate Rehearsal Commodities (2 questions)"
        assert row.next_move.detail == "open since 11 Jul"

    def test_the_drafts_include_our_reply_listing_the_questions(self, da, world):
        self._questions(da, world)
        drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"], today="2026-07-12")["drafts"]
        reply = next(d for d in drafts if d["kind"] == "reply")
        assert reply["supplier_name"] == "Northgate Rehearsal Commodities"
        assert "1. One warehouse in Kano" in reply["text"] and "2. Who is the importer of record?" in reply["text"]
        assert len(reply["commitment_ids"]) == 2

    def test_answering_clears_it_and_keeps_the_answer(self, da, world):
        self._questions(da, world)
        for commitment in op(da, "commitment_list", tender_id=world["tender"]["id"], open_only=True):
            op(da, "commitment_resolve", channel="web", commitment_id=commitment["id"], resolution="One warehouse.")
        assert op(da, "commitment_list", tender_id=world["tender"]["id"], open_only=True) == []
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.tender_id == world["tender"]["id"])
        assert not any("Northgate" in m.text for m in row.ours)
        assert not run_checks(da, kinds=["commitment_open"])


# ---- ruling 8: a partial update needs no contract or source and keeps who told us


class TestPartialUpdates:
    def test_an_eta_slip_moves_one_date_and_keeps_the_first_teller(self, da, world):
        contract, shipment = _held_on_our_form_m(da, world)
        stored = Shipment.objects.get(pk=shipment["id"])
        assert stored.source == "supplier_reported"
        assert stored.expected_on == datetime.date(2026, 9, 19)
        assert stored.contract_id == contract["id"]

    def test_a_different_teller_on_an_update_is_refused_not_applied(self, da, world):
        _, shipment = _held_on_our_form_m(da, world)
        with pytest.raises(ValueError, match="does not change who told us"):
            op(da, "shipment_update", shipment_id=shipment["id"], data={"source": "partner_reported"})
        assert Shipment.objects.get(pk=shipment["id"]).source == "supplier_reported"

    def test_an_update_cannot_move_a_record_to_another_order(self, da, world):
        _, shipment = _held_on_our_form_m(da, world)
        other = Contract.objects.exclude(pk=Shipment.objects.get(pk=shipment["id"]).contract_id).first()
        other_id = other.pk if other else 999999
        with pytest.raises(ValueError, match="cannot move it"):
            op(da, "shipment_update", shipment_id=shipment["id"], data={"contract_id": other_id})

    def test_a_forwarder_is_a_source(self, da, world):
        contract = _contract(da, world)
        made = op(da, "shipment_record", data={"contract_id": contract["id"], "source": "forwarder_reported"})
        assert made["source"] == "forwarder_reported"


# ---- ruling 9: the timeline names the actual sender


class TestTheSender:
    def test_an_update_names_who_sent_its_email_not_the_records_first_teller(self, da, world):
        from connect_labs.supply_chain.history.timeline import timeline_for_contract

        contract, _ = _held_on_our_form_m(da, world)
        entries = timeline_for_contract(contract["id"], program_id=PROGRAM)
        slip = next(e for e in entries if "expected_on" in e.fields)
        assert slip.sender == "Crescent Rehearsal Freight"
        created = next(e for e in entries if e.sentence.startswith("Shipment recorded"))
        assert created.sender == "Kanem Foods Rehearsal"


# ---- the page fixture ------------------------------------------------------


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
