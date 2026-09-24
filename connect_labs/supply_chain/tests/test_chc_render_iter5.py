"""What the CHC co-pack render showed at iteration 4, fixed before iteration 5.

THIS REPOSITORY IS PUBLIC. Every organisation, person and number is invented.
"""

from datetime import date
from decimal import Decimal

from connect_labs.supply_chain.alerts.forms import check_kind_choices
from connect_labs.supply_chain.forms import _plain_decimal
from connect_labs.supply_chain.procurement.views import folded_columns, table_columns
from connect_labs.supply_chain.templatetags.supply_chain_extras import check_readout, day


class TestOneDateRule:
    def test_iso_strings_dates_and_blanks(self):
        assert day("2026-09-17") == "17 Sep 2026"
        assert day(date(2026, 10, 18)) == "18 Oct 2026"
        assert day("2026-09-24T14:59:00+00:00") == "24 Sep 2026"
        assert day(None) is None and day("") == ""
        assert day("not a date") == "not a date"


class TestPlainDecimals:
    def test_storage_scale_is_not_shown(self):
        assert str(_plain_decimal(Decimal("30000.0000"))) == "30000"
        assert str(_plain_decimal(Decimal("0.6000"))) == "0.6"
        assert str(_plain_decimal(Decimal("18000.00"))) == "18000"


class TestAlertChecksReadAsWords:
    def test_no_snake_case_and_no_machine_phrasing(self):
        for kind, label in check_kind_choices():
            assert "_" not in label, label
            assert "a fact nobody supplied" not in label
            assert "bound in your own data" not in label
        labels = dict(check_kind_choices())
        assert labels["stock_below_minimum"].startswith("Below its own minimum")

    def test_the_sent_log_readout_says_how_far_below(self):
        readout = check_readout(
            {"kind": "stock_below_minimum", "facts": {"months_of_stock": "2.8", "min_months_of_stock": "3"}}
        )
        assert readout == "2.8 months of stock · minimum 3"


class TestTheComparisonFitsTheScreen:
    def _comparison(self, per_course="0.60"):
        def figures(unit, course):
            return {
                "usd_per_base_unit": {"amount": unit},
                "usd_per_course": {"amount": course},
                "usd_per_child_treated": {"amount": course},
                "landed_total_for_round_quantity": {"amount": "18000"},
            }

        return {
            "columns": [
                {"key": "usd_per_base_unit", "label": "USD per co-pack"},
                {"key": "usd_per_course", "label": "USD per course"},
                {"key": "usd_per_child_treated", "label": "USD per child treated"},
                {"key": "landed_total_for_round_quantity", "label": "Landed total (this round)"},
            ],
            "comparable": [{"figures": figures("0.60", per_course)}, {"figures": figures("0.64", "0.64")}],
        }

    def test_course_figures_equal_to_the_unit_price_are_folded_and_named(self):
        comparison = self._comparison()
        assert [c["key"] for c in table_columns(comparison)] == [
            "usd_per_base_unit",
            "landed_total_for_round_quantity",
        ]
        assert list(folded_columns(comparison).values()) == ["USD per course", "USD per child treated"]

    def test_a_course_figure_that_differs_stays(self):
        comparison = self._comparison(per_course="1.20")
        assert "usd_per_course" in [c["key"] for c in table_columns(comparison)]
        assert folded_columns(comparison) == {}
