"""Invented fixtures.

THIS REPOSITORY IS PUBLIC. Every supplier, contact and price here is made up.
The fixtures exist to reproduce the SHAPES that break a comparison:
  - a per-carton quote that never stated its pack spec
  - a per-sachet quote whose quantity basis exceeds the round
  - a quote that excludes duties
  - a non-USD quote with no exchange rate

These build UNSAVED model instances. The services under test only read
attributes, so no database is needed and the tests stay fast -- but that
means the values here must already be the types the database would hand
back. A `DecimalField` only coerces on load, so a string left in a fixture
would reach the pricing rules as a string and multiply into nonsense. Hence
`Decimal(...)` and `date(...)` rather than the strings a JSON caller sends.
"""

from datetime import date
from decimal import Decimal

import pytest

from connect_labs.supply_chain.models import Commodity, Item, Quote, Round

SCOPE = "prog:10501"


def _rutf(**overrides) -> Commodity:
    fields = {
        "scope_key": SCOPE,
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
    }
    fields.update(overrides)
    return Commodity(**fields)


# Module-level so `quote()` below can attach it without needing the fixture.
# Assigning an unsaved related object is fine: the services read
# quote.commodity.slug off the cached instance, never through a query.
RUTF = _rutf()


def wrap(model, data: dict, record_id=None):
    """An unsaved `model` instance from a plain dict, with database types.

    The tests used to build proxy objects over a JSON blob, and the proxies
    coerced on read. Real models coerce on *load*, so a string left in a
    fixture would reach the pricing rules as a string. This runs each value
    through the field's own `to_python` -- the same conversion a database
    load performs -- so a fixture written the way a JSON caller writes it
    still arrives typed the way the services expect.

    `commodity_slug` is resolved to a commodity object, because that is a
    relation now rather than a string on a blob.
    """
    by_name = {f.name: f for f in model._meta.fields}
    attnames = {f.attname for f in model._meta.fields}
    # The services read `superseded_by_quote_id`, which is a property over a
    # self-relation. A fixture written in the old vocabulary must still land
    # on the column, or a superseded quote silently re-enters comparisons.
    aliases = {"superseded_by_quote_id": "superseded_by_id"}
    kwargs = {}
    for raw_key, value in data.items():
        key = aliases.get(raw_key, raw_key)
        if key == "commodity_slug":
            kwargs["commodity"] = RUTF if value == "rutf" else _rutf(slug=value, name=value)
            continue
        # A raw foreign key id (supplier_id, round_id) is already the stored
        # type and has no field under that name -- only under attname.
        if key in attnames and key not in by_name:
            kwargs[key] = value
            continue
        field = by_name.get(key)
        if field is None:
            continue
        if field.is_relation or value is None:
            kwargs[key] = value
            continue
        kwargs[key] = field.to_python(value)
    if record_id is not None:
        kwargs["id"] = record_id
    return model(**kwargs)


@pytest.fixture
def rutf():
    """Therapeutic food with a full unit ladder and a course definition."""
    return RUTF


@pytest.fixture
def rutf_without_course():
    """Same commodity before the programme entered its treatment protocol."""
    return _rutf(course_definition={}, category="", shelf_life_months_minimum=None)


@pytest.fixture
def round_2000_cartons():
    return Round(
        id=1,
        program_id=10501,
        label="Round 2",
        status="open",
        lines=[{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        delivery_point={
            "name": "Central store",
            "city": "Kano",
            "country": "NG",
            "country_name": "Nigeria",
            "incoterm_requested": "DDP",
        },
        shelf_life_months_minimum=18,
    )


def quote(**overrides) -> Quote:
    """A fully-specified, comparable per-carton quote; override to break it.

    "Fully specified" covers both classes of fact procurement.services.questions
    distinguishes: the comparability facts pricing.py needs to derive a number
    (price, pack spec, quantity basis, freight/duties, FX rate) AND the
    acceptance facts that decide whether an otherwise-priceable offer is
    usable (shelf life, MOQ, lead time, quote validity). A quote silent on
    the latter is exactly the kind that still needs chasing in a follow-up.
    """
    fields = {
        "id": 1,
        "round_id": 1,
        "supplier_id": 1,
        "commodity": RUTF,
        "as_quoted_amount": Decimal("50.00"),
        "as_quoted_currency": "USD",
        "as_quoted_unit": "per_pack",
        "quantity_basis": Decimal("2000"),
        "quantity_basis_unit": "carton",
        "pack_spec_source": "stated_on_quote",
        "base_per_pack_stated": 150,
        "base_unit_grams_stated": 92,
        "freight_basis": "included",
        "duties_basis": "included",
        "incoterm": "DDP",
        "fx_rate_to_usd": Decimal("1"),
        "stated_spec": {},
        "shelf_life_months_stated": 24,
        "moq": Decimal("500"),
        "moq_unit": "carton",
        "lead_time_days": 30,
        "validity_until": date(2026, 12, 31),
    }
    fields.update(overrides)
    record_id = fields.pop("id", None)
    return wrap(Quote, fields, record_id=record_id)


@pytest.fixture
def comparable_quote():
    return quote()


def _item(**overrides) -> Item:
    fields = {
        "scope_key": SCOPE,
        "commodity": RUTF,
        "base_unit": "sachet",
        "pack_unit": "carton",
        "spec_attributes": {},
    }
    fields.update(overrides)
    record_id = fields.pop("id", None)
    return wrap(Item, fields, record_id=record_id)


@pytest.fixture
def item_150():
    """A trade item packed 150 to the carton — agrees with the commodity."""
    return _item(
        id=11,
        sku="northwind-rutf-92g",
        name="Northwind RUTF 92 g",
        base_per_pack=150,
        base_unit_grams=92,
    )


@pytest.fixture
def item_144():
    """A trade item packed 144 to the carton — DISAGREES with the commodity's 150.

    This is the fixture the whole item layer exists for. A per-sachet figure derived
    from the commodity's 150 would be wrong by 4% and would look authoritative.
    """
    return _item(
        id=12,
        sku="harmattan-rutf-92g",
        name="Harmattan RUTF 92 g",
        base_per_pack=144,
        base_unit_grams=92,
    )


@pytest.fixture
def item_100g():
    """A trade item weighing 100 g per sachet — DISAGREES with the commodity's 92 g.

    The unit-weight sibling of item_144: a per-tonne conversion derived from the
    commodity's 92 g would be wrong by ~8.7%, the same class of error as the
    pack-spec case.
    """
    return _item(
        id=13,
        sku="sahel-rutf-100g",
        name="Sahel RUTF 100 g",
        base_per_pack=150,
        base_unit_grams=100,
    )
