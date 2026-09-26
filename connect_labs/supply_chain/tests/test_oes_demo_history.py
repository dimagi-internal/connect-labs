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

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.history.rewind import rewind
from connect_labs.supply_chain.market.service import listed_tenders
from connect_labs.supply_chain.models import Invoice, Outreach, Quote, Shipment, Tender
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
    # The goods received note, as something we did ourselves this time.
    receipt = {**chain["reported_to_us"][0], "source": "we_recorded"}
    chain["we_did"] = [receipt, *chain["we_did"]]
    chain["reported_to_us"] = []
    chain["partner_points"] = []
    chain["dates"] = dict(_DATES)
    chain["shipment"] = dict(_SHIPMENT)
    return chain


def _round_two():
    round_two = copy.deepcopy(_ROUND_TWO)
    for quote in round_two["quotes"]:
        quote["commodity_slug"] = "a-product"
    round_two["round"]["lines"][0]["commodity_slug"] = "a-product"
    # Supplier B's email said freight was included; the record says not specified.
    round_two["quotes"][1]["freight_stated_in_email"] = "included"
    round_two["outreach"] = {"sent_on": _ago(20), "non_responders": ["Placeholder Silent Supplier"]}
    return round_two


def _document(**changes):
    document = {
        "orgs": _DOCUMENT["orgs"] + [{"slug": "another-buyer", "name": "A Placeholder Other Buyer"}],
        "commodities": _DOCUMENT["commodities"],
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

    def test_the_misread_quote_has_a_source_that_states_what_the_record_does_not(self, seeded):
        _, result = seeded
        misread = next(
            q for q in result["round_two"]["quotes"] if q["supplier_id"] == result["round_two"]["suppliers"][1]["id"]
        )
        quote = Quote.objects.get(pk=misread["id"])
        call = Revision.objects.get(content_type__model="quote", object_id=str(quote.id), action="create").call
        assert quote.freight_basis == "not_specified"
        assert "freight included" in call.source_excerpt.lower()

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


def test_a_market_buyer_on_a_real_program_is_refused(synthetic):
    document = _document()
    document["market_buyers"][0]["program_id"] = 263
    with pytest.raises(ValueError, match="263"):
        _seed(document)


def test_seeding_round_one_as_history_is_refused_on_a_program_that_is_not_synthetic(db):
    """No registration for the RUTF scope: backdating would rewrite a real program's history."""
    module = _load_seed_remote()
    document = _document()
    scopes = module.seed_scopes(document)
    with pytest.raises(PermissionError):
        module.seed_rutf_rounds(document, scopes)
    assert not Tender.objects.filter(program_id=RUTF).exists()


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
