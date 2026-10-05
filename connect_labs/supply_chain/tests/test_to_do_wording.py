"""Whose step it is, in plain words: "To do" and "Waiting on ...", never "on us".

Owner decision, 2026-10-04: "on us" read as jargon. Ours is a to-do list, theirs
is who we are waiting on, and a quote's missing fact carries the same words as a
chip. Moves and facts stay separate (a fact is still not a move).

THIS REPOSITORY IS PUBLIC. Nothing here names a real company.
"""

from connect_labs.supply_chain import moves
from connect_labs.supply_chain.templatetags.supply_chain_extras import supplies


def test_one_fact_is_named_and_several_are_counted():
    assert moves.facts_chip(["tender duty terms"], moves.US) == "tender duty terms · to do"
    assert moves.facts_chip(["exchange rate", "freight estimate", "duty exemption"], moves.US) == "3 facts · to do"
    assert moves.facts_chip(["sachets per carton"], moves.SUPPLIERS) == "sachets per carton · waiting"
    assert moves.facts_chip([], moves.US) == ""


def test_the_fact_chip_and_the_rails_use_the_same_words():
    assert supplies(moves.US) == moves.OWNER_CHIP[moves.US] == "to do"
    assert supplies(moves.SUPPLIERS) == moves.OWNER_CHIP[moves.SUPPLIERS] == "waiting"
    assert moves.TO_DO == "To do"
    assert moves.WAITING_ON_SUPPLIERS == "Waiting on suppliers"


def test_no_supply_screen_says_on_us_as_a_label():
    """The rails' headings and chips: none of the old labels survive on a supply template."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "templates" / "supply_chain"
    old = re.compile(r'heading="On us"|heading="On suppliers"|>on us<|>Us \{\{|>Facts on us<|>Moves on us<')
    hits = [str(p) for p in root.rglob("*.html") if old.search(p.read_text())]
    assert hits == []


def test_a_suppliers_fact_is_to_ask_until_we_chase_after_its_quote():
    """Kanem was never asked for its pack: the fact is ours to ask, not theirs to send."""
    import datetime as dt
    from types import SimpleNamespace as NS

    from connect_labs.supply_chain.procurement.status import asked_since_quote, fact_owner

    quote = NS(pk=1, tender_id=7, supplier_id=3, received_on=dt.date(2026, 9, 22))
    chased_before = [NS(supplier_id=3, last_reminder_on=dt.date(2026, 9, 21))]
    chased_after = [NS(supplier_id=3, last_reminder_on=dt.date(2026, 9, 24))]
    someone_else = [NS(supplier_id=4, last_reminder_on=dt.date(2026, 9, 30))]

    assert not asked_since_quote(quote, chased_before)
    assert not asked_since_quote(quote, someone_else)
    assert asked_since_quote(quote, chased_after)
    assert fact_owner("sachets per carton", asked=False) == moves.TO_ASK
    assert fact_owner("sachets per carton", asked=True) == moves.SUPPLIERS
    # Our own facts are ours whatever has been sent.
    assert fact_owner("exchange rate", asked=False) == moves.US
    assert moves.facts_chip(["sachets per carton"], moves.TO_ASK) == "sachets per carton · to ask"
    assert supplies(moves.TO_ASK) == "to ask"


def test_the_last_stage_is_a_step_not_an_outcome():
    """An order held at customs is in its Delivery step; a bar reading "Delivered" claimed it done."""
    from connect_labs.supply_chain.standing import STAGES, stage_bars

    assert STAGES[-1] == "Delivery"
    assert stage_bars(5)[-1] == "now" and stage_bars(6)[-1] == "done"
