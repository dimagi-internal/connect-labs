"""DDD 006 batch 6: quote cards line up, facts never look like moves, On us uses the detail's verbs."""

import re

from connect_labs.supply_chain.templatetags.supply_chain_extras import dot_parts, record_kind_lead


def _text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def test_a_quote_card_lists_its_terms_in_one_fixed_order_and_marks_what_was_not_stated():
    html = str(
        record_kind_lead("Quote · Kanem · recorded: USD 55.00 per carton (DDP Kano) · lead time 6 weeks", "X, Kanem")
    )
    terms = [_text(c) for c in re.findall(r'<span data-testid="quote-term".*?</span></span>', html, re.S)]
    assert terms == [
        "Incoterm DDP Kano",
        "Pack not stated",
        "Valid to not stated",
        "Lead time 6 weeks",
        "Minimum order not stated",
        "Shelf life not stated",
    ]


def test_dot_parts_splits_on_the_separator():
    assert dot_parts("USD 50.10 / carton · CPT Kano") == ["USD 50.10 / carton", "CPT Kano"]
    assert dot_parts("") == []


def test_overview_gaps_are_fact_chips_and_the_comparable_chip_leads():
    from django.template.loader import get_template

    source = get_template("supply_chain/home.html").template.source
    chip = get_template("supply_chain/_fact_chip.html").template.source
    assert '"supply_chain/_fact_chip.html"' in source and "status-chip--fact" in chip and "%}lead{%" in source
    assert 'data-testid="row-missing"' in source.split('data-testid="next-move"', 1)[1]
