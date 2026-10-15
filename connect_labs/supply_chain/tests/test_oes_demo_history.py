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
from connect_labs.supply_chain.models import Invoice, Movement, Outreach, Payment, Quote, Shipment, Tender
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


@pytest.fixture(scope="class")
def _seeded_once(django_db_setup, django_db_blocker):
    """Seed round 1 and round 2 ONCE for TestTheSeededHistory, in an outer transaction rolled back at class end.

    Each test still runs in its own savepoint (the `db` fixture nests inside this),
    so a test's writes -- a re-seed, a clarification -- roll back as before.
    """
    with django_db_blocker.unblock():
        outer = transaction.atomic()
        outer.__enter__()
        try:
            _register(RUTF)
            _register(MARKET_BUYER_PROGRAM)
            yield _seed(_document())
        finally:
            transaction.set_rollback(True)
            outer.__exit__(None, None, None)


@pytest.fixture
def seeded(_seeded_once, db):
    return _seeded_once


def _created(model, pk):
    return Revision.objects.get(content_type__model=model, object_id=str(pk), action="create")


class TestTheSeededHistory:
    def test_each_step_is_dated_the_day_it_happened_and_attributed_to_who_did_it(self, seeded, settings):
        _, result = seeded
        # What the agent typed from an email: through MCP, as the configured agent account, with its source.
        for quote in Quote.objects.filter(tender__program_id=RUTF):
            call = _created("quote", quote.id).call
            assert call.channel == "mcp" and call.actor_is_agent and call.source_ref and call.source_excerpt
        assert _created("quote", result["round_one"]["quotes"][0]["id"]).recorded_at == _ten_am(_DATES["quoted_on"])
        invoice_call = OperationCall.objects.filter(program_id=RUTF, operation="invoice_record").first()
        assert invoice_call.actor.email == settings.LABS_AGENT_ACCOUNT_EMAILS[0]

        # What a person did on the web: no agent, no email behind it.
        award = OperationCall.objects.get(program_id=RUTF, operation="award_create")
        assert award.channel == "web" and not award.actor_is_agent and award.source_ref == ""
        assert award.recorded_at == _ten_am(_DATES["decided_on"])

        # The ETA slip is one dated update, made by the agent from an email.
        shipment = Shipment.objects.get(contract_id=result["round_one"]["contract"]["id"])
        revisions = Revision.objects.filter(content_type__model="shipment", object_id=str(shipment.id))
        assert revisions.get(action="create").recorded_at == _ten_am(_SHIPMENT["dispatched_on"])
        (slip,) = (rev for rev in revisions if rev.action == "update" and "expected_on" in rev.changes)
        assert slip.changes["expected_on"] == [_SHIPMENT["eta_original"], _SHIPMENT["expected_on"]]
        assert slip.recorded_at == _ten_am(_SHIPMENT["eta_slip_learned_on"]) and slip.call.actor_is_agent

        # Receipt movements are in history too (rewinding past a receipt needs them).
        movements = Movement.objects.filter(program_id=RUTF, receipt__isnull=False)
        assert movements.exists()
        assert all(_created("movement", m.id).recorded_at == _ten_am(_DATES["received_on"]) for m in movements)

    def test_round_one_is_invoiced_once_and_paid_in_full_on_its_day(self, seeded):
        _, result = seeded
        invoice = result["round_one"]["invoice"]
        assert result["round_one"]["invoice_replayed"] is True
        assert Invoice.objects.filter(contract_id=result["round_one"]["contract"]["id"]).count() == 1
        payment = Payment.objects.get(invoice_id=invoice["id"])
        assert Decimal(str(payment.amount)) == Decimal(str(invoice["amount"]))
        assert str(payment.paid_on) == _DATES["paid_on"]

    def test_seeding_again_writes_nothing_new(self, seeded):
        def counts():
            return {
                "revisions": Revision.objects.filter(program_id=RUTF).count(),
                "sourced_calls": OperationCall.objects.filter(program_id=RUTF).exclude(source_ref="").count(),
                "quotes": Quote.objects.filter(tender__program_id=RUTF).count(),
                "outreach": Outreach.objects.filter(tender__program_id=RUTF).count(),
                "shipments": Shipment.objects.filter(contract__program_id=RUTF).count(),
                "invoices": Invoice.objects.filter(contract__program_id=RUTF).count(),
                "payments": Payment.objects.filter(invoice__contract__program_id=RUTF).count(),
                "tenders": Tender.objects.count(),
            }

        before = counts()
        _seed(_document())
        assert counts() == before

    def test_rewinding_to_before_the_slip_shows_the_original_eta_and_no_round_two(self, seeded):
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

    def test_the_clarification_is_recorded_once_and_replays(self, seeded):
        module, _ = seeded
        assert module.record_rutf_clarification(_document())
        before = (Quote.objects.count(), Revision.objects.count(), OperationCall.objects.count())
        assert module.record_rutf_clarification(_document())["replayed"] is True
        assert (Quote.objects.count(), Revision.objects.count(), OperationCall.objects.count()) == before

    def test_a_clarification_dated_in_the_future_is_refused(self, seeded):
        module, _ = seeded
        document = _document()
        document["rutf_rounds"]["round_two"]["clarification"]["answered_on"] = _ago(-1)
        with pytest.raises(ValueError, match="future"):
            module.record_rutf_clarification(document)
        assert not OperationCall.objects.filter(operation="quote_correct").exists()


# ---- refused before anything is written ------------------------------------


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


@pytest.mark.parametrize(
    "program_id, why",
    [(263, "not a registered synthetic"), (10690, "not a registered synthetic"), (RUTF, "demo's own")],
)
def test_a_market_buyer_on_a_program_that_is_not_its_own_synthetic_one_is_refused(synthetic, program_id, why):
    document = _document()
    document["market_buyers"][0]["program_id"] = program_id
    with pytest.raises(ValueError, match=why):
        _seed(document)
    assert not Tender.objects.filter(program_id=RUTF).exists()


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


def _round_one_with(**changes):
    document = _document()
    for section, (key, value) in changes.items():
        document["rutf_rounds"]["round_one"][section][key] = value
    return document


@pytest.mark.parametrize(
    "document, match",
    [
        (lambda: _round_one_with(dates=("counted_on", _ago(-2))), "future"),
        (lambda: _round_one_with(dates=("decided_on", _ago(90))), "decided_on"),
        (lambda: _round_one_with(shipment=("dispatched_on", _ago(69))), "consignment"),
    ],
    ids=["a-day-in-the-future", "days-that-run-backwards", "dispatched-before-signed"],
)
def test_round_one_days_that_cannot_have_happened_are_refused_before_any_write(synthetic, document, match):
    with pytest.raises(ValueError, match=match):
        _seed(document())
    assert not Shipment.objects.exists()
    assert not Tender.objects.filter(program_id=RUTF).exists()


def test_a_round_two_quote_received_before_the_requests_went_out_is_refused(synthetic):
    document = _document()
    document["rutf_rounds"]["round_two"]["quotes"][0]["received_on"] = _ago(21)
    with pytest.raises(ValueError, match="before the requests went out"):
        _seed(document)
    assert not Tender.objects.filter(program_id=RUTF).exists()
