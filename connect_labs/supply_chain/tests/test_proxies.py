from decimal import Decimal

from connect_labs.supply_chain.proxies import CommodityRecord, ItemRecord, QuoteRecord, RoundRecord


def _wrap(cls, data):
    # opportunity_id is a required LocalLabsRecord constructor field, but supply_chain
    # records are org/program-scoped, not opportunity-scoped (see records.py's docstring).
    # None matches the precedent set by other org/program-scoped record tests (e.g.
    # funder_dashboard's FundRecord fixtures).
    return cls({"id": 1, "experiment": "procurement", "type": "t", "data": data, "opportunity_id": None})


def test_commodity_exposes_the_unit_ladder():
    c = _wrap(CommodityRecord, {"slug": "rutf", "base_per_pack": 150, "base_unit_grams": 92})
    assert c.slug == "rutf"
    assert c.base_per_pack == 150
    assert c.base_unit_grams == 92


def test_commodity_without_a_course_definition_reports_none():
    c = _wrap(CommodityRecord, {"slug": "rutf"})
    assert c.course_definition == {}
    assert c.base_units_per_course is None


def test_commodity_with_a_course_definition_reports_the_count():
    c = _wrap(
        CommodityRecord,
        {"slug": "rutf", "course_definition": {"base_units_per_course": 150}},
    )
    assert c.base_units_per_course == 150


def test_quote_amounts_are_decimals_not_floats():
    q = _wrap(QuoteRecord, {"as_quoted_amount": "52.42", "fx_rate_to_usd": "1"})
    assert q.as_quoted_amount == Decimal("52.42")
    assert isinstance(q.as_quoted_amount, Decimal)
    assert q.fx_rate_to_usd == Decimal("1")


def test_quote_missing_amount_is_none_not_zero():
    q = _wrap(QuoteRecord, {})
    assert q.as_quoted_amount is None
    assert q.fx_rate_to_usd is None


def test_quote_basis_flags_default_to_the_honest_answer():
    q = _wrap(QuoteRecord, {})
    assert q.freight_basis == "not_specified"
    assert q.duties_basis == "not_specified"
    assert q.pack_spec_source == "not_stated"
    assert q.item_id is None
    assert q.voided is False


def test_quote_records_which_kind_of_pack_spec_statement_it_has():
    stated = _wrap(QuoteRecord, {"pack_spec_source": "stated_on_quote", "base_per_pack_stated": 150})
    assert stated.pack_spec_source == "stated_on_quote"
    assert stated.base_per_pack_stated == 150

    confirmed = _wrap(QuoteRecord, {"pack_spec_source": "trade_item_confirmed", "item_id": 7})
    assert confirmed.pack_spec_source == "trade_item_confirmed"
    assert confirmed.item_id == 7


def test_item_exposes_its_pack_ladder_and_gs1_keys():
    item = _wrap(
        ItemRecord,
        {
            "sku": "northwind-rutf-92g",
            "name": "Northwind RUTF 92 g",
            "commodity_slug": "rutf",
            "base_per_pack": 150,
            "base_unit_grams": 92,
            "gtin_pack": "05012345678900",
            "unicef_material_no": "S0000001",
        },
    )
    assert item.sku == "northwind-rutf-92g"
    assert item.commodity_slug == "rutf"
    assert item.base_per_pack == 150
    assert item.gtin_pack == "05012345678900"
    assert item.unicef_material_no == "S0000001"


def test_item_without_gs1_keys_reports_none_not_empty_string():
    item = _wrap(ItemRecord, {"sku": "x", "commodity_slug": "rutf"})
    assert item.gtin_base is None
    assert item.spec_attributes == {}
    assert item.status == "active"


def test_round_reports_the_quantity_for_a_commodity():
    r = _wrap(
        RoundRecord,
        {"lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}]},
    )
    assert r.quantity_for("rutf") == (Decimal("2000"), "carton")
    assert r.quantity_for("vitamin-a") is None
