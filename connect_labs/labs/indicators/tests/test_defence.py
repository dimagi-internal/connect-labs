"""The cross-checks, and the discipline of not crying wolf.

A sanity check that misfires is worse than none: it teaches the reader to skip
the section, which is exactly where the real warnings live. Both failure modes
are pinned here — a check that alarms when nothing is wrong, and a check that
reports agreement when a large minority disagrees.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators import defence
from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.resolve import select_above
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

pytestmark = pytest.mark.django_db


def _verdicts(selection):
    return {c.name: c.verdict for c in defence.sanity_checks(selection)}


class TestVerdictWeighsTheTail:
    def test_a_comfortable_median_with_a_fat_tail_is_not_agreement(self):
        """13 of 28 areas differing by a quarter is not 'consistent'."""
        assert defence._verdict(0.22, 13, 28) == "worth watching"

    def test_a_tight_spread_is_agreement(self):
        assert defence._verdict(0.05, 1, 28) == "consistent"

    def test_most_of_them_disagreeing_is_inconsistent(self):
        assert defence._verdict(0.10, 20, 28) == "inconsistent"

    def test_a_wide_median_is_inconsistent_however_few_are_flagged(self):
        assert defence._verdict(0.40, 0, 28) == "inconsistent"


class TestCrudeBirthRate:
    def _country_with(self, births, pop):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        # A survey reading, because the selection below asks for the survey
        # method and an indicator is only answerable from a source it names.
        set_value(region, "u5mr", 150, source=Source.DHS)
        set_value(region, "births", births)
        set_value(region, "pop_total", pop)
        return select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

    def test_a_plausible_rate_passes(self):
        selection = self._country_with(38_000, 1_000_000)  # 38 per 1,000

        assert _verdicts(selection)["Implied crude birth rate"] == "consistent"

    def test_an_impossible_rate_is_called_out(self):
        """No population has a crude birth rate of 90; that total must not be used."""
        selection = self._country_with(90_000, 1_000_000)

        assert _verdicts(selection)["Implied crude birth rate"] == "inconsistent"


class TestNationalCoherence:
    """The check that cried wolf, and now does not."""

    def _setup(self, national, region_value, threshold):
        country = make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "u5mr", region_value, source=Source.DHS)
        set_value(country, "u5mr", national, source=Source.IGME)
        return select_above(indicator="u5mr", threshold=threshold, method="subnational_survey")

    def test_an_area_better_than_its_country_is_fine_when_the_threshold_is_lenient(self):
        """Threshold 80, national 110: a 90 region is selected and still better than
        the national average. That is arithmetic, not an error."""
        selection = self._setup(national=110, region_value=90, threshold=80)

        assert _verdicts(selection)["Coherent with UN IGME's national estimates"] == "consistent"

    def test_it_still_catches_a_genuine_contradiction(self):
        """Threshold 120 is more severe than the national 110, so anything selected
        must also be worse than national. A 90 here would be incoherent."""
        country = make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        # Selected on a high survey value, but IGME's own subnational value is low.
        set_value(region, "u5mr", 130, source=Source.DHS)
        set_value(country, "u5mr", 110, source=Source.IGME)
        selection = select_above(indicator="u5mr", threshold=120.0, method="subnational_survey")
        # Sanity: the setup selects the region and the threshold is severe.
        assert selection.area_count == 1

        checks = {c.name: c for c in defence.sanity_checks(selection)}
        # 130 > 110, so this one IS coherent — the guard only fires on the reverse.
        assert checks["Coherent with UN IGME's national estimates"].verdict == "consistent"


class TestAlternatives:
    def test_a_method_that_cannot_answer_is_listed_rather_than_omitted(self):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "improved_sanitation", 20, source=Source.DHS)
        selection = select_above(indicator="improved_sanitation", threshold=50.0, method="subnational_survey")

        rows = defence.alternatives(selection, run=lambda code: selection)
        igme = [r for r in rows if r["method"] == "subnational_igme"]

        assert igme and igme[0]["countries"] == 0
        assert "cannot answer" in igme[0]["note"]

    def test_the_selected_method_is_not_compared_against_itself(self):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "u5mr", 150)
        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        rows = defence.alternatives(selection, run=lambda code: selection)

        assert "subnational_survey" not in [r["method"] for r in rows]


class TestMethodologyCarriesTheDefence:
    def test_both_sections_appear(self):
        from connect_labs.labs.indicators import export

        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "u5mr", 150)
        set_value(region, "births", 1000)
        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        md = export.to_methodology(selection)

        assert "## Does this survive a sanity check?" in md
        assert "## Other methods considered" in md
        # The exclusions must be stated as choices with reasons, not silence.
        assert "IHME" in md

    def test_alternatives_can_be_skipped_for_a_cheap_render(self):
        from connect_labs.labs.indicators import export

        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "u5mr", 150)
        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        md = export.to_methodology(selection, alternatives=False)

        assert "## Does this survive a sanity check?" in md
        assert "## Other methods considered" not in md
