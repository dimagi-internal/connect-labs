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
