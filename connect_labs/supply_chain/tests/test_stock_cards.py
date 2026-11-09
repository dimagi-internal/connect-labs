"""The Stock page's cards say what the table says, in one plain sentence per supply point.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is invented.
"""

from connect_labs.supply_chain.stock import cards


def _point(**over):
    point = {
        "name": "Harmattan warehouse",
        "status": "ok",
        "on_hand": {"amount": "27", "unit": "jerrycan"},
        "days_to_stockout": "36",
        "months_of_stock": "1.2",
        "min_months_of_stock": "1",
        "max_months_of_stock": "2",
        "no_consumption_yet": False,
        "stocked_out": False,
        "restocked_from": "supplier",
        "resupply_quantity": {"amount": "18", "unit": "jerrycan"},
    }
    point.update(over)
    return point


def test_a_point_within_its_band_has_a_bar_and_says_so():
    (card,) = cards.build([_point()])
    assert card["state"] == "ok"
    assert card["bar"]["days"] == 36 and card["bar"]["min_days"] == 30 and card["bar"]["max_days"] == 60
    assert card["why"] == "36 days of cover, inside its 30 to 60 day band."
    assert 0 < card["bar"]["min_at"] < card["bar"]["max_at"] < 100
    assert card["needs_order"] and card["top_up"].endswith("to reorder")


def test_below_minimum_is_named_with_its_minimum():
    (card,) = cards.build([_point(status="below_min", days_to_stockout="12")])
    assert card["state"] == "below_min"
    assert card["why"] == "12 days of cover, under its 30-day minimum."


def test_a_negative_balance_is_negative_not_stocked_out_and_draws_no_bar():
    (card,) = cards.build([_point(status="unknown", on_hand={"amount": "-12", "unit": "jerrycan"}, stocked_out=True)])
    assert card["state"] == "negative"
    assert card["bar"] is None
    assert "more has left here than ever arrived" in card["why"]


def test_a_stocked_out_point_with_no_use_says_there_is_no_pace_yet():
    (card,) = cards.build(
        [_point(status="unknown", on_hand={"amount": "0", "unit": "jerrycan"}, stocked_out=True, no_consumption_yet=True)]
    )
    assert card["state"] == "stockout" and card["bar"] is None
    assert card["why"].startswith("Nothing left, and no use recorded yet")


def test_unknown_cover_draws_no_bar_and_gives_the_reason():
    (card,) = cards.build(
        [_point(status="unknown", days_to_stockout={"unconfirmed": ["no count yet"]}, months_of_stock={"unconfirmed": ["no count yet"]})]
    )
    assert card["bar"] is None
    assert card["why"] == "no count yet"


def test_the_tiles_count_what_needs_a_person():
    built = cards.build(
        [
            _point(),
            _point(status="below_min", days_to_stockout="5"),
            _point(status="unknown", on_hand={"amount": "0", "unit": "jerrycan"}, stocked_out=True, no_consumption_yet=True),
            _point(status="unknown", on_hand={"amount": "-1", "unit": "jerrycan"}, stocked_out=True),
        ]
    )
    assert cards.tiles(built) == {"reorder": 4, "empty": 2, "below_min": 1, "fine": 1}


def test_a_balance_counted_in_its_base_unit_leads_with_it():
    (card,) = cards.build(
        [_point(counted_in_base=True, on_hand_in_base={"amount": "540", "unit": "liter"}, on_hand={"amount": "27", "unit": "jerrycan"})]
    )
    assert card["lead"]["big"] == "540" and card["lead"]["side"] == "27 jerrycans"
