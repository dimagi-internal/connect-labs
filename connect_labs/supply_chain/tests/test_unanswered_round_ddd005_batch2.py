"""DDD 005 batch 2: one date shape in the overview, the Incoterm on a delivered card line."""

import datetime as dt
from types import SimpleNamespace

from connect_labs.supply_chain.procurement.services.comparison import _with_incoterm
from connect_labs.supply_chain.templatetags.supply_chain_extras import count_back, short_day

NOW = dt.datetime(2026, 10, 3, 12, tzinfo=dt.UTC)


def test_last_change_is_always_a_count_then_the_day():
    assert count_back(dt.datetime(2026, 10, 3, 9, tzinfo=dt.UTC), NOW) == "today"
    assert count_back(dt.datetime(2026, 9, 27, 9, tzinfo=dt.UTC), NOW) == "6 days ago"
    assert count_back(dt.datetime(2026, 7, 29, 9, tzinfo=dt.UTC), NOW) == "2 months ago"
    assert short_day(dt.datetime(2026, 7, 29, 9, tzinfo=dt.UTC), NOW) == "29 Jul"
    assert short_day(dt.datetime(2025, 12, 1, 9, tzinfo=dt.UTC), NOW) == "1 Dec 2025"


def test_a_delivered_line_leads_with_the_quoted_incoterm():
    quote = SimpleNamespace(incoterm="CPT Kano")
    assert _with_incoterm("Delivered to Kano · freight included, per quote", quote) == (
        "CPT Kano · delivered to Kano · freight included, per quote"
    )
    assert _with_incoterm("Ex works Niamey — delivery to Kano requested", quote) == (
        "Ex works Niamey — delivery to Kano requested"
    )
    assert _with_incoterm("Delivered to Kano", SimpleNamespace(incoterm="")) == "Delivered to Kano"
