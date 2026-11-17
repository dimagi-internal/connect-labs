"""Territories are levelled to the country's current figure, when every one of them can be."""

import pytest

from connect_labs.effectiveness import baseline
from connect_labs.effectiveness.tests.helpers import boundary, value
from connect_labs.labs.indicators.models import Source

pytestmark = pytest.mark.django_db


@pytest.fixture
def country():
    ng = boundary(0, "Nigeria", "NGA", 0, 0, size=2)
    kano, lagos = boundary(1, "Kano", "NGA-KN", 0, 0), boundary(1, "Lagos", "NGA-LA", 1, 0)
    value(ng, "nmr", 30.0, source=Source.IGME)
    value(kano, "nmr", 40.0, source=Source.IGME_SUBNATIONAL)
    value(lagos, "nmr", 20.0, source=Source.IGME_SUBNATIONAL)
    value(kano, "births", 80_000.0, source=Source.DERIVED)
    value(lagos, "births", 120_000.0, source=Source.DERIVED)
    return {"ng": ng, "kano": kano, "lagos": lagos}


def nmr(country, key):
    return baseline.Baselines([country[key]]).newborn_mortality("NGA", country[key])


def test_territories_are_scaled_so_their_births_weighted_mean_is_the_national_figure(country):
    # (40*80 + 20*120) / 200 = 28 against a national 30: every territory x 30/28
    kano, lagos = nmr(country, "kano"), nmr(country, "lagos")
    assert kano.value == pytest.approx(40 * 30 / 28) and lagos.value == pytest.approx(20 * 30 / 28)
    assert (kano.value * 80 + lagos.value * 120) / 200 == pytest.approx(30.0)
    assert kano.published == 40.0 and kano.level == pytest.approx(30 / 28) and "levelled" in kano.how


def test_a_country_with_a_unit_that_has_no_figure_of_its_own_is_not_levelled(country):
    country["lagos"].indicator_values.filter(indicator="nmr").delete()
    got = nmr(country, "kano")
    assert got.value == 40.0 and got.published is None and got.level == 1.0


def test_a_country_with_a_unit_that_has_no_births_is_not_levelled(country):
    country["lagos"].indicator_values.filter(indicator="births").delete()
    assert nmr(country, "kano").value == 40.0


def test_a_selection_of_one_state_is_levelled_on_the_whole_country_not_on_itself(country):
    # Baselines holds only Kano, yet the mean is over both states: 28, not 40
    assert baseline.Baselines([country["kano"]]).newborn_mortality("NGA", country["kano"]).level == pytest.approx(
        30 / 28
    )


def test_a_large_adjustment_is_applied_and_said_to_be_large(country):
    country["ng"].indicator_values.filter(indicator="nmr").update(value=60.0)
    got = nmr(country, "kano")
    assert got.level == pytest.approx(60 / 28) and "disagree a lot" in got.how


def test_the_country_itself_and_a_figure_made_from_the_countrys_are_never_levelled(country):
    assert baseline.Baselines([country["ng"]]).newborn_mortality("NGA", None).value == 30.0
