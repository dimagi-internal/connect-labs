"""Unanswered round, 2026-10-04 batch 1: one list of what a quote still lacks, read alike
by the comparison, the tender's Suppliers table, its tiles, the drafted emails and the
overview -- and one Suppliers table on the tender page, the Invitations folded into it.

THIS REPOSITORY IS PUBLIC. Every supplier, person and price here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.models import Quote, Tender
from connect_labs.supply_chain.procurement.services.comparison import _GAP_WORDS
from connect_labs.supply_chain.procurement.services.questions import _REASON_QUESTIONS, INTERNAL
from connect_labs.supply_chain.procurement.status import (
    comparisons,
    gap_owner,
    quote_open_facts,
    split_gaps,
    tender_status,
    waiver_on_file,
)
from connect_labs.supply_chain.standing import _missing_facts
from connect_labs.supply_chain.tests import test_unanswered_round_ddd005_batch5_estimates as estimates

pytestmark = pytest.mark.django_db

PROGRAM = estimates.PROGRAM
TODAY = estimates.TODAY
op = estimates.op
da = estimates.da
world = estimates.world
_quote = estimates._quote

_ACCEPTANCE = dict(
    shelf_life_months_stated=24, lead_time_days=35, validity_until="2026-11-02", moq=500, moq_unit="carton"
)


def _sahel(da, world):
    """EUR per sachet, ex works, no exchange rate recorded: every fact it lacks is ours."""
    return _quote(
        da,
        world,
        "exw",
        "EXW Niamey",
        as_quoted_amount="0.31",
        as_quoted_unit="per_base_unit",
        as_quoted_currency="EUR",
        freight_basis="excluded",
        **_ACCEPTANCE,
    )


def _harmattan(da, world):
    """USD per carton, carriage paid: comparable, costed on the tender's duty waiver."""
    return _quote(da, world, "cpt", "CPT Kano", freight_basis="included", **_ACCEPTANCE)


def _tender(world):
    return Tender.objects.get(pk=world["tender"]["id"])


# ---- one rule for whose a fact is ---------------------------------------------


def test_every_gap_a_question_puts_to_us_is_ours_and_every_supplier_question_theirs():
    """The comparison's owner split and the follow-up's audience are one table."""
    for _, key, _, audience in _REASON_QUESTIONS:
        if key not in _GAP_WORDS:
            continue
        expected = rules.US if audience == INTERNAL else rules.SUPPLIERS
        assert gap_owner(_GAP_WORDS[key]) == expected, key


def test_the_exchange_rate_is_ours_to_record_never_a_supplier_question():
    fx = next(row for row in _REASON_QUESTIONS if row[1] == "fx_rate")
    assert fx[3] == INTERNAL
    assert gap_owner("exchange rate") == rules.US


def test_no_follow_up_is_drafted_for_facts_that_are_ours(da, world):
    _sahel(da, world)
    drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"])["drafts"]
    sahel_id = world["suppliers"]["exw"]["id"]
    assert not [d for d in drafts if d["kind"] == "followup" and d.get("supplier_id") == sahel_id]


def test_a_follow_up_is_still_drafted_for_a_fact_the_supplier_owes(da, world):
    _quote(da, world, "ddp", "DDP Kano", base_per_pack_stated=None, pack_spec_source=None, **_ACCEPTANCE)
    drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"])["drafts"]
    kanem_id = world["suppliers"]["ddp"]["id"]
    assert [d for d in drafts if d["kind"] == "followup" and d.get("supplier_id") == kanem_id]


# ---- one list of open facts per quote -------------------------------------------


def test_the_open_facts_carry_the_waiver_document_for_a_comparable_quote(da, world):
    _harmattan(da, world)
    tender = _tender(world)
    quotes = list(Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item"))
    assert waiver_on_file(tender) is False
    (compared,) = comparisons(tender, quotes)
    (row,) = compared.comparable
    facts = quote_open_facts(tender, row, quotes[0], waiver_on_file=False)
    assert "duty exemption" in facts
    ours, theirs = split_gaps(facts)
    assert "duty exemption" in ours and not theirs


def test_the_overview_reads_the_same_facts_the_comparison_counts(da, world):
    _harmattan(da, world)
    _sahel(da, world)
    tender = _tender(world)
    quotes = list(Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item"))
    missing = {fact: (n, owner) for fact, n, owner in _missing_facts(tender, quotes)}
    # The duty exemption is on every quote we import under the waiver, comparable or not.
    assert missing["duty exemption"] == (2, rules.US)
    assert missing["exchange rate"][1] == rules.US
    assert all(owner == rules.US for _, owner in missing.values())


def test_the_suppliers_table_splits_a_quotes_facts_as_the_comparison_does(da, world):
    _sahel(da, world)
    status = tender_status(_tender(world), TODAY, program_id=PROGRAM, draft_anchors={"draft-supplier-0"})
    (row,) = (r for r in status["suppliers"] if r["supplier_id"] == world["suppliers"]["exw"]["id"])
    assert row["missing"] == []
    assert row["on_us"].endswith(" · ours to fill")
    assert reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) in row["on_us_href"]
    assert row["action"]["label"] == "Open quote"
    tile = next(t for t in status["tiles"] if t["label"] == "Comparable quotes")
    assert "ours to fill on 1 quote" in tile["sub"]
    assert "missing from" not in tile["sub"]


def test_a_supplier_fact_is_missing_from_the_quote_and_asked_for(da, world):
    _quote(da, world, "ddp", "DDP Kano", base_per_pack_stated=None, pack_spec_source=None, **_ACCEPTANCE)
    sid = world["suppliers"]["ddp"]["id"]
    status = tender_status(_tender(world), TODAY, program_id=PROGRAM, draft_anchors={f"draft-supplier-{sid}"})
    (row,) = (r for r in status["suppliers"] if r["supplier_id"] == sid)
    assert row["missing"] == ["sachets per carton"]
    assert row["action"] == {"label": "Ask", "href": f"#draft-supplier-{sid}"}
