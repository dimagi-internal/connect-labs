"""The seam from a scenario's places to the baseline's question."""

import pytest

from connect_labs.effectiveness import people
from connect_labs.effectiveness.scenario import Scenario

pytestmark = pytest.mark.django_db


def test_everywhere_asks_the_widest_indicator_with_no_threshold():
    q, everywhere = people.question_for(Scenario())
    assert everywhere and q.indicator == people.EVERYWHERE_INDICATOR and q.threshold == 0.0


def test_a_chosen_indicator_and_threshold_are_the_questions():
    q, everywhere = people.question_for(Scenario.from_params({"indicator": "u5mr", "threshold": "90", "iso": "ner"}))
    assert not everywhere and (q.indicator, q.threshold, q.iso_codes) == ("u5mr", 90.0, ("NER",))


def test_an_unknown_indicator_falls_back_as_every_baseline_endpoint_does():
    q, _ = people.question_for(Scenario.from_params({"indicator": "nope"}))
    assert q.indicator == "u5mr"


def test_a_method_in_the_link_reaches_the_baseline():
    q, _ = people.question_for(Scenario.from_params({"indicator": "u5mr", "method": "national_igme"}))
    assert q.method == "national_igme"


def test_the_indicator_choices_lead_with_everywhere():
    choices = people.indicator_choices()
    assert choices[0] == {"key": "all", "label": "Everywhere with data", "threshold": None}
    assert {c["key"] for c in choices[1:]} <= set(people.TARGET_INDICATORS)
