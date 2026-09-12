"""Invented fixtures.

THIS REPOSITORY IS PUBLIC. Every supplier, contact and price here is made up.
The fixtures exist to reproduce the SHAPES that break a comparison:
  - a per-carton quote that never stated its pack spec
  - a per-sachet quote whose quantity basis exceeds the round
  - a quote that excludes duties
  - a non-USD quote with no exchange rate
"""

import pytest

from connect_labs.supply_chain.models import CommodityRecord, ItemRecord, QuoteRecord, RoundRecord


def wrap(cls, data, record_id=1):
    # opportunity_id is a required LocalLabsRecord constructor field, but supply_chain
    # records are org/program-scoped, not opportunity-scoped (see records.py's docstring).
    # None matches the precedent set by other org/program-scoped record tests (e.g.
    # test_models.py's _wrap, and funder_dashboard's FundRecord fixtures).
    return cls(
        {
            "id": record_id,
            "experiment": "procurement",
            "type": "t",
            "data": data,
            "opportunity_id": None,
        }
    )


@pytest.fixture
def rutf():
    """Therapeutic food with a full unit ladder and a course definition."""
    return wrap(
        CommodityRecord,
        {
            "slug": "rutf",
            "name": "Ready-to-use therapeutic food",
            "category": "therapeutic_food",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "base_unit_grams": 92,
            "course_definition": {
                "base_units_per_day": 2,
                "days_per_course": 75,
                "base_units_per_course": 150,
                "source": "programme protocol",
            },
            "spec_requirements": [],
            "shelf_life_months_minimum": 18,
        },
    )


@pytest.fixture
def rutf_without_course():
    """Same commodity before the programme entered its treatment protocol."""
    return wrap(
        CommodityRecord,
        {
            "slug": "rutf",
            "name": "Ready-to-use therapeutic food",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "base_unit_grams": 92,
        },
    )


@pytest.fixture
def round_2000_cartons():
    return wrap(
        RoundRecord,
        {
            "label": "Round 2",
            "status": "open",
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
            "delivery_point": {"name": "Central store", "city": "Kano", "country": "NG", "incoterm_requested": "DDP"},
            "shelf_life_months_minimum": 18,
        },
    )


def quote(**overrides):
    """A fully-specified, comparable per-carton quote; override to break it.

    "Fully specified" covers both classes of fact procurement.services.questions
    distinguishes: the comparability facts pricing.py needs to derive a number
    (price, pack spec, quantity basis, freight/duties, FX rate) AND the
    acceptance facts that decide whether an otherwise-priceable offer is
    usable (shelf life, MOQ, lead time, quote validity). A quote silent on
    the latter is exactly the kind that still needs chasing in a follow-up.
    """
    data = {
        "round_id": 1,
        "supplier_id": 1,
        "commodity_slug": "rutf",
        "as_quoted_amount": "50.00",
        "as_quoted_currency": "USD",
        "as_quoted_unit": "per_pack",
        "quantity_basis": "2000",
        "quantity_basis_unit": "carton",
        "pack_spec_source": "stated_on_quote",
        "base_per_pack_stated": 150,
        "base_unit_grams_stated": 92,
        "freight_basis": "included",
        "duties_basis": "included",
        "incoterm": "DDP",
        "fx_rate_to_usd": "1",
        "stated_spec": {},
        "shelf_life_months_stated": 24,
        "moq": "500",
        "moq_unit": "carton",
        "lead_time_days": 30,
        "validity_until": "2026-12-31",
    }
    data.update(overrides)
    return wrap(QuoteRecord, data)


@pytest.fixture
def comparable_quote():
    return quote()


@pytest.fixture
def item_150():
    """A trade item packed 150 to the carton — agrees with the commodity."""
    return wrap(
        ItemRecord,
        {
            "sku": "northwind-rutf-92g",
            "name": "Northwind RUTF 92 g",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "base_unit_grams": 92,
            "spec_attributes": {},
        },
        record_id=11,
    )


@pytest.fixture
def item_144():
    """A trade item packed 144 to the carton — DISAGREES with the commodity's 150.

    This is the fixture the whole item layer exists for. A per-sachet figure derived
    from the commodity's 150 would be wrong by 4% and would look authoritative.
    """
    return wrap(
        ItemRecord,
        {
            "sku": "harmattan-rutf-92g",
            "name": "Harmattan RUTF 92 g",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 144,
            "base_unit_grams": 92,
            "spec_attributes": {},
        },
        record_id=12,
    )


@pytest.fixture
def item_100g():
    """A trade item weighing 100 g per sachet — DISAGREES with the commodity's 92 g.

    The unit-weight sibling of item_144: a per-tonne conversion derived from the
    commodity's 92 g would be wrong by ~8.7%, the same class of error as the
    pack-spec case.
    """
    return wrap(
        ItemRecord,
        {
            "sku": "sahel-rutf-100g",
            "name": "Sahel RUTF 100 g",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "base_unit_grams": 100,
            "spec_attributes": {},
        },
        record_id=13,
    )
