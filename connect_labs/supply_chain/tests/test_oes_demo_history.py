"""RUTF round 1, seeded as the dated, attributed history it would have had.

The Sophie narrative (docs/walkthroughs/supply-sophie-rutf.yaml) reads round 1
back through its timeline and rewinds the program to a past day, so the seed
has to write each step on the day it happened, as the person or agent who did
it, and -- for what the AI typed from an email -- with the source it read.

THIS REPOSITORY IS PUBLIC. Every name, date, quantity and price below is an
invented placeholder, built on the provenance tests' fixture chain. The real
ones live in the Drive seed document and are read at run time.
"""

import copy
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.history.rewind import rewind
from connect_labs.supply_chain.market.service import listed_tenders
from connect_labs.supply_chain.models import Invoice, Outreach, Payment, Quote, Shipment, Tender
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.tests.test_oes_demo_provenance import _CHAIN, _DOCUMENT, _load_seed_remote
from connect_labs.supply_chain.tests.test_oes_demo_reference import _ROUND_TWO

RUTF = 10672
MARKET_BUYER_PROGRAM = 10689


def _ago(days):
    return (timezone.localdate() - timedelta(days=days)).isoformat()


def _ten_am(iso):
    return timezone.make_aware(
        datetime.combine(datetime.fromisoformat(iso).date(), time(10)), timezone.get_default_timezone()
    )


# Round 1's days, oldest first. Relative to today so the order holds whenever
# this runs; the real document states absolute dates.
_DATES = {
    "requested_on": _ago(80),
    "quoted_on": _ago(74),
    "decided_on": _ago(70),
    "signed_on": _ago(68),
    "invoiced_on": _ago(66),
    "paid_on": _ago(40),
    "received_on": _ago(30),
    "counted_on": _ago(25),
}
_SHIPMENT = {
    "reference": "AWB-PLACEHOLDER-R1",
    "dispatched_on": _ago(60),
    "eta_original": _ago(45),
    "eta_slip_learned_on": _ago(50),
    "expected_on": _ago(31),
}


def _round_one():
    """The provenance tests' chain, bought from a manufacturer, with its days."""
    chain = copy.deepcopy(_CHAIN)
    chain.pop("distributor_slug")
    chain["supplier_label"] = "A Placeholder Manufacturer"
    chain["round"]["label"] = "A Placeholder RUTF Round 1"
    chain["quotes"][0]["commodity_slug"] = "a-product"
    # The goods received note, as something we did ourselves this time. The
    # provenance chain's own `we_did` payment row is dropped: round 1's
    # payment is no longer a document-listed ledger row, it is recorded
    # automatically from `dates.paid_on` (see `seed_chain`), and keeping both
    # would pay the invoice twice.
    receipt = {**chain["reported_to_us"][0], "source": "we_recorded"}
    chain["we_did"] = [receipt]
    chain["reported_to_us"] = []
    chain["partner_points"] = []
    chain["dates"] = dict(_DATES)
    chain["shipment"] = dict(_SHIPMENT)
    return chain


def _round_two():
    """The reference tests' round 2: A fails only on its pack spec, B and C on their own reasons."""
    round_two = copy.deepcopy(_ROUND_TWO)
    round_two["outreach"] = {"sent_on": _ago(20), "non_responders": ["Placeholder Silent Supplier"]}
    round_two["clarification"] = {
        "supplier_label": "Placeholder Supplier A",
        "answered_on": _ago(1),
        "corrections": {"pack_spec_source": "stated_on_quote", "base_per_pack_stated": 150},
    }
    return round_two


_THERAPEUTIC_FOOD = {
    "slug": "a-therapeutic-food",
    "name": "A Placeholder Therapeutic Food",
    "category": "therapeutic_food",
    "base_unit": "sachet",
    "pack_unit": "carton",
}


def _document(**changes):
    document = {
        "orgs": _DOCUMENT["orgs"] + [{"slug": "another-buyer", "name": "A Placeholder Other Buyer"}],
        "commodities": _DOCUMENT["commodities"] + [_THERAPEUTIC_FOOD],
        "chc_chain": {"round": {"lines": []}},
        "rutf_rounds": {"round_one": _round_one(), "round_two": _round_two()},
        "chlorine_blocked": {"round": {"lines": []}},
        "supply_only": {"round": {"lines": []}},
        "market_buyers": [
            {
                "program_id": MARKET_BUYER_PROGRAM,
                "org_slug": "another-buyer",
                "tender": {
                    "label": "A Placeholder Other Buyer's Tender",
                    "delivery_point": {"city": "A Placeholder Town"},
                    "lines": [{"commodity_slug": "a-product", "quantity": "50", "quantity_unit": "box"}],
                },
            }
        ],
    }
    document.update(changes)
    return document


def _register(program_id):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=program_id,
        program_id=program_id,
        labs_only=True,
        enabled=True,
        label="oes demo history tests",
        allowed_domains=["dimagi.com"],
    )


def _seed(document):
    module = _load_seed_remote()
    scopes = module.seed_scopes(document)
    return module, module.seed_rutf_rounds(document, scopes)


@pytest.fixture
def synthetic(db):
    _register(RUTF)
    _register(MARKET_BUYER_PROGRAM)


@pytest.fixture
def seeded(synthetic):
    return _seed(_document())


def _shipment_revisions(shipment_id):
    return Revision.objects.filter(content_type__model="shipment", object_id=str(shipment_id))


class TestTheEtaSlip:
    def test_the_slip_is_one_dated_update_the_agent_made_from_an_email(self, seeded):
        _, result = seeded
        shipment = Shipment.objects.get(contract_id=result["round_one"]["contract"]["id"])
        slips = [rev for rev in _shipment_revisions(shipment.id) if "expected_on" in rev.changes]
        update = [rev for rev in slips if rev.action == "update"]

        assert len(update) == 1
        assert update[0].changes["expected_on"] == [_SHIPMENT["eta_original"], _SHIPMENT["expected_on"]]
        assert update[0].recorded_at == _ten_am(_SHIPMENT["eta_slip_learned_on"])
        call = update[0].call
        assert call.channel == "mcp" and call.actor_is_agent is True
        assert call.source_ref and _SHIPMENT["expected_on"][:4] in call.source_excerpt

    def test_the_shipment_left_on_its_dispatch_day_with_the_original_eta(self, seeded):
        _, result = seeded
        shipment = Shipment.objects.get(contract_id=result["round_one"]["contract"]["id"])
        create = _shipment_revisions(shipment.id).get(action="create")
        assert create.changes["expected_on"] == [None, _SHIPMENT["eta_original"]]
        assert create.recorded_at == _ten_am(_SHIPMENT["dispatched_on"])

    def test_the_email_that_arrived_twice_is_recorded_once(self, seeded):
        _, result = seeded
        calls = OperationCall.objects.filter(program_id=RUTF, operation="shipment_update").exclude(source_ref="")
        assert calls.count() == 1
        assert result["round_one"]["eta_replayed"] is True

    def test_without_an_original_eta_there_is_no_slip(self, synthetic):
        document = _document()
        del document["rutf_rounds"]["round_one"]["shipment"]["eta_original"]
        _, result = _seed(document)

        shipment = Shipment.objects.get(contract_id=result["round_one"]["contract"]["id"])
        assert str(shipment.expected_on) == _SHIPMENT["expected_on"]
        assert not [
            rev for rev in _shipment_revisions(shipment.id) if rev.action == "update" and "expected_on" in rev.changes
        ]
        assert not OperationCall.objects.filter(program_id=RUTF, operation="shipment_update").exclude(source_ref="")


class TestWhoDidEachStep:
    def test_the_quotes_came_in_through_the_agent_with_their_sources(self, seeded):
        for quote in Quote.objects.filter(tender__program_id=RUTF):
            create = Revision.objects.get(content_type__model="quote", object_id=str(quote.id), action="create")
            assert create.call.channel == "mcp" and create.call.actor_is_agent
            assert create.call.source_ref.startswith("<rutf-r") and create.call.source_ref.endswith("@demo.invalid>")
            assert create.call.source_excerpt

    def test_round_one_quote_is_dated_the_day_it_arrived(self, seeded):
        _, result = seeded
        quote_id = result["round_one"]["quotes"][0]["id"]
        create = Revision.objects.get(content_type__model="quote", object_id=str(quote_id), action="create")
        assert create.recorded_at == _ten_am(_DATES["quoted_on"])

    def test_sophie_made_the_award_on_the_web_on_the_day_she_decided(self, seeded):
        _, result = seeded
        call = OperationCall.objects.get(program_id=RUTF, operation="award_create")
        assert call.channel == "web" and call.actor.username == "demo-sophie" and not call.actor_is_agent
        assert call.recorded_at == _ten_am(_DATES["decided_on"])
        assert call.source_ref == ""

    def test_the_agent_is_the_configured_agent_account(self, seeded, settings):
        call = OperationCall.objects.filter(program_id=RUTF, operation="invoice_record").first()
        assert call.actor.email == settings.LABS_AGENT_ACCOUNT_EMAILS[0]

    def test_round_two_is_recent_and_its_silent_supplier_is_still_waiting(self, seeded):
        _, result = seeded
        tender = Tender.objects.get(pk=result["round_two"]["round"]["id"])
        create = Revision.objects.get(content_type__model="tender", object_id=str(tender.id), action="create")
        assert create.recorded_at == _ten_am(_ago(20))
        for quote in Quote.objects.filter(tender=tender):
            rev = Revision.objects.get(content_type__model="quote", object_id=str(quote.id), action="create")
            assert rev.recorded_at == _ten_am(_ago(3))
        waiting = Outreach.objects.filter(tender=tender, responded=False)
        assert [o.supplier.name for o in waiting] == ["Placeholder Silent Supplier"]
        assert Outreach.objects.filter(tender=tender, responded=True).count() == 3


def _row(rows, title_start):
    return next(r for r in rows if r.title.startswith(title_start))


class TestRoundOneIsPaid:
    def test_a_stated_paid_on_pays_the_invoice_in_full_and_the_overview_shows_it(self, seeded):
        _, result = seeded
        contract, invoice = result["round_one"]["contract"], result["round_one"]["invoice"]
        payment = Payment.objects.get(invoice_id=invoice["id"])
        assert Decimal(str(payment.amount)) == Decimal(str(invoice["amount"]))
        assert str(payment.paid_on) == _DATES["paid_on"]

        row = _row(standing_rows(RUTF, timezone.localdate()), contract["reference"])
        assert row.stage == "delivered and paid"
        assert row.waiting_on == "—"

    def test_the_payment_is_recorded_by_sophie_on_the_web_on_the_day_it_was_paid(self, seeded):
        _, result = seeded
        invoice = result["round_one"]["invoice"]
        payment = Payment.objects.get(invoice_id=invoice["id"])
        create = Revision.objects.get(content_type__model="payment", object_id=str(payment.id), action="create")
        assert create.recorded_at == _ten_am(_DATES["paid_on"])
        assert create.call.channel == "web" and create.call.actor.username == "demo-sophie"
        assert not create.call.actor_is_agent
        # Sophie made the payment herself; there is no email to point at.
        assert create.call.source_ref == ""

    def test_without_a_stated_paid_on_no_payment_is_recorded(self, synthetic, capsys):
        document = _document()
        del document["rutf_rounds"]["round_one"]["dates"]["paid_on"]
        _, result = _seed(document)
        invoice = result["round_one"]["invoice"]

        assert not Payment.objects.filter(invoice_id=invoice["id"]).exists()
        row = _row(standing_rows(RUTF, timezone.localdate()), result["round_one"]["contract"]["reference"])
        assert row.stage == "invoiced"
        assert row.waiting_on == "payment"
        assert "paid_on" in capsys.readouterr().out

    def test_seeding_again_does_not_pay_twice(self, seeded):
        _, result = seeded
        invoice_id = result["round_one"]["invoice"]["id"]
        _seed(_document())
        assert Payment.objects.filter(invoice_id=invoice_id).count() == 1


class TestReplay:
    def test_the_repeated_invoice_email_replays_rather_than_billing_twice(self, seeded):
        _, result = seeded
        assert result["round_one"]["invoice_replayed"] is True
        assert Invoice.objects.filter(contract_id=result["round_one"]["contract"]["id"]).count() == 1

    def test_seeding_again_writes_nothing_new(self, seeded):
        def counts():
            return {
                "revisions": Revision.objects.filter(program_id=RUTF).count(),
                "sourced_calls": OperationCall.objects.filter(program_id=RUTF).exclude(source_ref="").count(),
                "quotes": Quote.objects.filter(tender__program_id=RUTF).count(),
                "outreach": Outreach.objects.filter(tender__program_id=RUTF).count(),
                "shipments": Shipment.objects.filter(contract__program_id=RUTF).count(),
                "invoices": Invoice.objects.filter(contract__program_id=RUTF).count(),
                "tenders": Tender.objects.count(),
            }

        before = counts()
        _seed(_document())
        assert counts() == before


def test_rewinding_to_before_the_slip_shows_the_original_eta_and_no_round_two(seeded):
    """The as-of beat: every row is dated no later than what depends on it.

    A catalogue or supplier left dated "now" would be deleted by the rewind
    while older quotes still point at it.
    """
    _, result = seeded
    shipment_id = Shipment.objects.get(contract_id=result["round_one"]["contract"]["id"]).id
    with transaction.atomic():
        rewind(RUTF, _ten_am(_ago(55)))
        assert str(Shipment.objects.get(pk=shipment_id).expected_on) == _SHIPMENT["eta_original"]
        assert not Tender.objects.filter(pk=result["round_two"]["round"]["id"]).exists()
        assert Quote.objects.filter(pk=result["round_one"]["quotes"][0]["id"]).exists()
        transaction.set_rollback(True)


def test_other_buyers_tenders_sit_beside_round_two_on_the_market(seeded):
    _, result = seeded
    listed = {row.tender.pk for row in listed_tenders()}
    assert result["round_two"]["round"]["id"] in listed
    other = Tender.objects.get(program_id=MARKET_BUYER_PROGRAM)
    assert other.pk in listed and other.owner_org.slug == "another-buyer"


def test_no_market_section_is_skipped_not_invented(synthetic, capsys):
    document = _document()
    del document["market_buyers"]
    _seed(document)
    assert not Tender.objects.filter(program_id=MARKET_BUYER_PROGRAM).exists()
    assert "market_buyers" in capsys.readouterr().out


@pytest.mark.parametrize(
    "program_id, why",
    [(263, "not a registered synthetic"), (10690, "not a registered synthetic"), (RUTF, "demo's own")],
)
def test_a_market_buyer_on_a_program_that_is_not_its_own_synthetic_one_is_refused(synthetic, program_id, why):
    """Refused before anything is written, round 1 included."""
    document = _document()
    document["market_buyers"][0]["program_id"] = program_id
    with pytest.raises(ValueError, match=why):
        _seed(document)
    assert not Tender.objects.filter(program_id=RUTF).exists()


def test_seeding_round_one_as_history_is_refused_on_a_program_that_is_not_synthetic(db):
    """No registration for the RUTF scope: backdating would rewrite a real program's history."""
    module = _load_seed_remote()
    document = _document()
    scopes = module.seed_scopes(document)
    with pytest.raises(PermissionError):
        module.seed_rutf_rounds(document, scopes)
    assert not Tender.objects.filter(program_id=RUTF).exists()
    # Refused before the demo's people are made, not just before its records.
    assert not get_user_model().objects.filter(username="demo-sophie").exists()


def test_the_personas_are_created_once(db):
    module = _load_seed_remote()
    first = module.demo_persona_users()
    second = module.demo_persona_users()
    assert first == second
    assert first["sophie"].username == "demo-sophie" and first["sophie"].name == "Sophie"
    assert get_user_model().objects.filter(username="ace-agent").count() == 1


def test_the_stock_a_receipt_posted_is_in_history_on_the_receipts_day(seeded):
    """Receipt movements used to be bulk-inserted, which sends no signal.

    They then escaped history, and rewinding past the receipt failed on the
    receipt they PROTECT.
    """
    from connect_labs.supply_chain.models import Movement

    movements = Movement.objects.filter(program_id=RUTF, receipt__isnull=False)
    assert movements.exists()
    for movement in movements:
        create = Revision.objects.get(content_type__model="movement", object_id=str(movement.id), action="create")
        assert create.recorded_at == _ten_am(_DATES["received_on"])


# ---- the clarification that lands mid-demo -------------------------------


def _supplier_a_reasons(tender_id):
    """Why supplier A's quote cannot be ranked, from the comparison the page draws."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.procurement.services.comparison import compare_tender

    access = SupplyDataAccess(access_token="unused", program_id=RUTF, caller=SYSTEM)
    tender = access.get_tender(tender_id)
    quotes = access.list_quotes(tender_id=tender_id)
    suppliers = {q.supplier_id: access.get_supplier(q.supplier_id) for q in quotes}
    comparison = compare_tender(tender, access.get_commodity("a-therapeutic-food"), quotes, suppliers)
    return {row.supplier_name: row.figures["landed_total_for_tender_quantity"].reasons for row in comparison.blocked}


class TestTheClarification:
    def test_the_seed_leaves_supplier_a_blocked_on_its_pack_spec_alone(self, seeded):
        _, result = seeded
        reasons = _supplier_a_reasons(result["round_two"]["round"]["id"])["Placeholder Supplier A"]
        assert reasons and all("pack spec" in reason for reason in reasons)

    def test_the_reply_makes_the_quote_comparable(self, seeded):
        module, result = seeded
        module.record_rutf_clarification(_document())
        assert "Placeholder Supplier A" not in _supplier_a_reasons(result["round_two"]["round"]["id"])

    def test_the_reply_forwarded_again_replays(self, seeded):
        module, _ = seeded
        module.record_rutf_clarification(_document())
        before = (Quote.objects.count(), Revision.objects.count(), OperationCall.objects.count())
        again = module.record_rutf_clarification(_document())
        assert again["replayed"] is True
        assert (Quote.objects.count(), Revision.objects.count(), OperationCall.objects.count()) == before

    def test_the_timeline_shows_the_agent_recording_the_reply_on_its_day(self, seeded):
        from connect_labs.supply_chain.history.timeline import timeline_for_tender

        module, result = seeded
        module.record_rutf_clarification(_document())
        entries = timeline_for_tender(result["round_two"]["round"]["id"], program_id=RUTF)
        replies = [e for e in entries if e.excerpt == "Each carton holds 150 sachet."]
        # One call, one line: the correction, naming what it changed.
        assert len(replies) == 1
        assert replies[0].sentence.startswith("Quote corrected: units per pack 150 (was not stated)"), replies[
            0
        ].sentence
        assert "base_per_pack_stated" in replies[0].fields
        for entry in replies:
            assert entry.actor == "ACE (agent)" and entry.is_ai
            assert entry.when == _ten_am(_ago(1))

    def test_no_clarification_in_the_document_is_skipped_and_said(self, seeded, capsys):
        module, _ = seeded
        document = _document()
        del document["rutf_rounds"]["round_two"]["clarification"]
        assert module.record_rutf_clarification(document) is None
        assert "clarification" in capsys.readouterr().out
        assert not OperationCall.objects.filter(operation="quote_correct").exists()

    def test_a_quote_with_no_received_on_is_refused_not_waved_through(self, seeded):
        """Without the day the quote arrived, "answered before it arrived" cannot
        be ruled out, so the reply is refused rather than the check skipped."""
        module, result = seeded
        Quote.objects.filter(tender_id=result["round_two"]["round"]["id"]).update(received_on=None)
        with pytest.raises(ValueError, match="no received_on"):
            module.record_rutf_clarification(_document())
        assert not OperationCall.objects.filter(operation="quote_correct").exists()

    def test_a_reply_dated_in_the_future_is_refused(self, seeded):
        module, _ = seeded
        document = _document()
        document["rutf_rounds"]["round_two"]["clarification"]["answered_on"] = _ago(-1)
        with pytest.raises(ValueError, match="future"):
            module.record_rutf_clarification(document)
        assert not OperationCall.objects.filter(operation="quote_correct").exists()


# ---- days that cannot have happened -------------------------------------


def test_a_step_dated_tomorrow_is_refused():
    module = _load_seed_remote()
    with pytest.raises(ValueError, match="future"):
        module._ten_am(_ago(-1))


def test_a_step_dated_today_is_never_recorded_after_now(monkeypatch):
    from django.utils import timezone as django_timezone

    early = timezone.make_aware(datetime.combine(timezone.localdate(), time(8)), timezone.get_default_timezone())
    monkeypatch.setattr(django_timezone, "now", lambda: early)
    module = _load_seed_remote()
    assert module._ten_am(early.date().isoformat()) == early


def test_a_round_one_day_in_the_future_is_refused_before_any_write(synthetic):
    document = _document()
    document["rutf_rounds"]["round_one"]["dates"]["counted_on"] = _ago(-2)
    with pytest.raises(ValueError, match="future"):
        _seed(document)
    assert not Tender.objects.filter(program_id=RUTF).exists()


def test_round_one_days_that_run_backwards_are_refused_before_any_write(synthetic):
    document = _document()
    document["rutf_rounds"]["round_one"]["dates"]["decided_on"] = _ago(90)
    with pytest.raises(ValueError, match="decided_on"):
        _seed(document)
    assert not Tender.objects.filter(program_id=RUTF).exists()


def test_a_consignment_dispatched_before_its_order_was_signed_is_refused_before_any_write(synthetic):
    document = _document()
    document["rutf_rounds"]["round_one"]["shipment"]["dispatched_on"] = _ago(69)
    with pytest.raises(ValueError, match="consignment"):
        _seed(document)
    assert not Shipment.objects.exists()
    assert not Tender.objects.filter(program_id=RUTF).exists()


def test_a_round_two_quote_received_before_the_requests_went_out_is_refused(synthetic):
    document = _document()
    document["rutf_rounds"]["round_two"]["quotes"][0]["received_on"] = _ago(21)
    with pytest.raises(ValueError, match="before the requests went out"):
        _seed(document)
    assert not Tender.objects.filter(program_id=RUTF).exists()


def test_an_invoice_email_that_bills_twice_stops_the_seed(synthetic):
    """If the second invoice email were written rather than replayed, the seed says so."""
    module = _load_seed_remote()
    real, seen = module.op, []

    def second_invoice_loses_its_source(access, name, **payload):
        if name == "invoice_record":
            seen.append(name)
            if len(seen) == 2:
                payload.pop("source", None)
        return real(access, name, **payload)

    module.op = second_invoice_loses_its_source
    document = _document()
    scopes = module.seed_scopes(document)
    with pytest.raises(RuntimeError, match="invoice"):
        module.seed_rutf_rounds(document, scopes)
