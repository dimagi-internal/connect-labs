"""DHS loader tests that need no network."""

from __future__ import annotations

from connect_labs.labs.indicators.sources.dhs import _latest_survey_per_country


def rec(code, year, label, value=100):
    return {
        "DHS_CountryCode": code,
        "SurveyYear": year,
        "CharacteristicLabel": label,
        "Value": value,
    }


class TestLatestSurveyPerCountry:
    def test_only_the_most_recent_survey_survives(self):
        got = _latest_survey_per_country([rec("NG", 2013, "Kano"), rec("NG", 2024, "Kano"), rec("NG", 2024, "Lagos")])
        assert set(got) == {("NG", "Kano"), ("NG", "Lagos")}
        assert got[("NG", "Kano")]["SurveyYear"] == 2024

    def test_labels_from_an_older_taxonomy_are_dropped_entirely(self):
        # Kenya's pre-2010 provinces must not ride alongside its 2022 counties;
        # keeping both would double-count the same population.
        got = _latest_survey_per_country(
            [rec("KE", 2008, "Coast"), rec("KE", 2022, "Mombasa"), rec("KE", 2022, "Kilifi")]
        )
        assert ("KE", "Coast") not in got
        assert len(got) == 2

    def test_each_country_keeps_its_own_latest_year(self):
        got = _latest_survey_per_country([rec("NG", 2024, "Kano"), rec("ML", 2023, "Segou"), rec("ML", 2018, "Segou")])
        assert got[("NG", "Kano")]["SurveyYear"] == 2024
        assert got[("ML", "Segou")]["SurveyYear"] == 2023

    def test_empty_input(self):
        assert _latest_survey_per_country([]) == {}
