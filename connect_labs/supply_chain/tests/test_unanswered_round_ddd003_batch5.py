"""DDD 003 batch 5: a quote that states duty included under the waiver is a real blocker;
the quote's commercial terms read on its history line; an event's lines drop its sender."""

from types import SimpleNamespace

from connect_labs.supply_chain.history.labels import quote_commercial_terms
from connect_labs.supply_chain.procurement.services.questions import (
    DUTY_RESTATE_KEY,
    needs_duty_restated,
)
from connect_labs.supply_chain.templatetags.supply_chain_extras import record_kind_lead


def test_duty_included_under_the_waiver_needs_restating():
    waiver = SimpleNamespace(duty_terms="buyer_waiver")
    assert needs_duty_restated(SimpleNamespace(duties_basis="included"), waiver)
    assert not needs_duty_restated(SimpleNamespace(duties_basis="excluded"), waiver)
    assert not needs_duty_restated(SimpleNamespace(duties_basis="included"), SimpleNamespace(duty_terms=""))
    assert DUTY_RESTATE_KEY == "duty_restated"


def test_the_quote_line_carries_its_commercial_terms():
    words = quote_commercial_terms(
        {
            "validity_until": "2026-11-02",
            "lead_time_days": 35,
            "moq": 500,
            "moq_unit": "carton",
            "shelf_life_months_stated": 24,
        }
    )
    assert words == "valid to 2 Nov 2026 · lead time 5 weeks · minimum order 500 cartons · shelf life 24 months"
    assert quote_commercial_terms({}) == ""
    assert quote_commercial_terms({"lead_time_days": 10}) == "lead time 10 days"


def test_a_line_under_an_email_does_not_repeat_its_sender():
    line = "Quote · Sahel Nutrition Industries · recorded: EUR 0.31 per sachet"
    html = str(record_kind_lead(line, "Amadou Issoufou, Sahel Nutrition Industries"))
    assert "Sahel Nutrition Industries" not in html and "recorded: EUR 0.31" in html
    assert "Sahel Nutrition Industries" in str(record_kind_lead(line, "Someone, Other Co"))
