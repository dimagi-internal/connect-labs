"""The gates decide whether a number may be shown at all.

BEHAVIOUR ONLY. Every fact these gates read is now registry data — editable
without a deploy — so this file passes facts IN as fixtures and never asserts what
the KMC registry happens to contain. A test over registry values would fail CI on a
correct edit, and, worse, would be reading the on-disk SEED while a workflow bound
to a record computes from something else entirely: green here, different in
production. Registry values are checked where they can actually be checked — the
`semantic_registry_validate` coherence rules, and the dry-run against real data
before a candidate registry is bound.

What remains here is the engine: given these facts, does the gate answer correctly.
Plus a few invariants about repo CODE, which is not dynamic.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic.gates import any_asks, credible_for, input_state

MORTALITY = {"mortality_recording_credible": {"PIPN": True, "EHA": True, "GHI": False, "NAMA": False}}
COMPLETION = {"completion_recording_credible": {"PIPN": True, "GHI": False}}
# `referred` is stored as `referral_visits`; without the alias the gate looks up a
# column that is not there and fails open.
ASKS_AS = {"referred": "referral_visits", "self_referral_count": "self_referral_visits"}


def measure(indicator: str, inputs: list[str]) -> dict:
    """A measure as `measure_catalog` serves it — `inputs` is registry data now."""
    return {"id": indicator.lower(), "indicator": indicator, "inputs": inputs}


def facts(app_asks=None) -> dict:
    return {"llo_map": {}, "settings": {}, "app_asks": app_asks or {}, "asks_as": ASKS_AS}


def state(indicator, inputs, row, opps=None, app_asks=None) -> str:
    return input_state(measure(indicator, inputs), row, opps, deployment=facts(app_asks))


def asks(field, opps, app_asks=None) -> bool:
    return any_asks(field, opps, app_asks=app_asks or {}, asks_as=ASKS_AS)


class TestInputAvailability:
    def test_ok_when_the_input_was_recorded(self):
        assert state("C20", ["ever_danger_sign"], {"anyrec_ever_danger_sign": 1}) == "ok"

    def test_unrecorded_when_the_scope_never_recorded_it(self):
        """A worker who logged no danger signs has not achieved a 0% rate."""
        assert state("C20", ["ever_danger_sign"], {"anyrec_ever_danger_sign": 0}) == "unrecorded"

    def test_an_ungated_indicator_is_always_ok(self):
        assert state("C01", [], {}) == "ok"

    def test_fails_open_when_the_gate_column_is_absent(self):
        """A missing gate means our wiring is wrong, not that nothing was collected.

        Blanking a real indicator on our own error is the worse failure -- the
        render makes the same choice in anyRecorded().
        """
        assert state("C20", ["ever_danger_sign"], {}) == "ok"

    def test_every_declared_input_must_be_present(self):
        row = {"anyrec_birth_weight_g": 1, "anyrec_enrollment_weight_g": 0}
        assert state("C28", ["birth_weight_g", "enrollment_weight_g"], row) == "unrecorded"

    def test_inputs_come_from_the_measure_not_a_side_table(self):
        """`IND_INPUTS` was keyed by indicator id, so an indicator and its inputs
        could be edited apart. They are one record now."""
        assert state("C20", [], {"anyrec_ever_danger_sign": 0}) == "ok"


class TestCredibility:
    @pytest.mark.parametrize("llo,expected", [("PIPN", True), ("EHA", True), ("GHI", False), ("NAMA", False)])
    def test_mortality_is_an_allow_list(self, llo, expected):
        assert credible_for("C14", llo, settings=MORTALITY) is expected

    def test_programme_scope_is_never_gated(self):
        """Pooling is the point: the programme figure includes credible recorders."""
        assert credible_for("C14", None, settings=MORTALITY) is True
        assert credible_for("C18", None, settings=COMPLETION) is True

    def test_completion_gate_is_deny_listed_not_allow_listed(self):
        """The two readings are deliberately opposite, which is why a registry must
        state a verdict for every LLO — see the validator rule that enforces it."""
        assert credible_for("C18", "GHI", settings=COMPLETION) is False
        assert credible_for("C18", "PIPN", settings=COMPLETION) is True
        assert credible_for("C18", "NOT-LISTED", settings=COMPLETION) is True

    def test_ungated_indicators_pass(self):
        assert credible_for("C09", "GHI", settings=MORTALITY) is True

    def test_absent_settings_do_not_silently_publish_a_gated_figure(self):
        """An empty table must not read as "everyone is credible" for C14."""
        assert credible_for("C14", "PIPN", settings={}) is False


class TestAppAsks:
    """'not in app' and 'unrecorded' are different facts about the programme."""

    def test_notinapp_when_no_opportunity_in_scope_asks(self):
        app_asks = {"1": {"days_discharge_to_reg": False}}
        row = {"anyrec_days_discharge_to_reg": 0}
        assert state("C16", ["days_discharge_to_reg"], row, [1], app_asks) == "notinapp"

    def test_unrecorded_when_the_app_asks_but_nothing_was_recorded(self):
        app_asks = {"1": {"days_discharge_to_reg": True}}
        got = state("C16", ["days_discharge_to_reg"], {"anyrec_days_discharge_to_reg": 0}, [1], app_asks)
        assert got == "unrecorded"

    def test_any_opportunity_asking_is_enough(self):
        app_asks = {"1": {"x": False}, "2": {"x": True}}
        assert asks("x", [1, 2], app_asks) is True
        assert asks("x", [1], app_asks) is False

    def test_derived_names_map_to_their_pipeline_column(self):
        app_asks = {"1": {"referral_visits": False}}
        assert asks("referred", [1], app_asks) is False

    def test_unknown_opportunity_fails_open(self):
        assert asks("x", [999999], {"1": {"x": False}}) is True

    def test_unknown_field_on_a_known_opportunity_fails_open(self):
        assert asks("y", [1], {"1": {"x": False}}) is True

    def test_no_scope_fails_open(self):
        assert asks("x", None, {"1": {"x": False}}) is True

    def test_no_declared_facts_gate_nothing(self):
        """A registry that says nothing about app coverage must not blank anything."""
        assert asks("x", [1, 2, 3], {}) is True

    def test_yaml_integer_keys_still_match(self):
        """YAML reads `10020:` as an int while the gate keys on str, so a map moved
        out of Python would match nothing and every gate would fail open."""
        from connect_labs.semantic.runtime import normalise_deployment_facts

        normalised = normalise_deployment_facts({"app_asks": {10020: {"x": False}}})
        assert set(normalised["app_asks"]) == {"10020"}
        assert asks("x", [10020], normalised["app_asks"]) is False


class TestNothingIsRedeclaredInCode:
    """Invariants about repo CODE, which is not dynamic and so is fair to pin.

    The availability and credibility tables lived in three places at once: a JS
    literal in the render, literals in this module, and the registry's deployment
    document — which the compiler cannot do without. Three copies of one human
    judgement, and when they disagree every engine still confidently returns a
    number. The render's copy was the one that decided what a user saw.
    """

    def test_gates_declares_no_tables(self):
        import connect_labs.semantic.gates as g

        for name in ("APP_ASKS", "ASKS_AS", "IND_INPUTS", "MORTALITY_CREDIBLE", "COMPLETION_CREDIBLE"):
            assert not hasattr(g, name), f"{name} must live in the registry, not in gates.py"

    def test_the_render_keeps_no_copy(self):
        """The inverse of the test this replaces, which asserted the render's copy
        AGREED with Python field by field — a drift detector for a duplication that
        should not exist at all."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2] / "workflow" / "templates" / "kmc_programme_metrics_render.js"
        ).read_text()
        for literal in ("var APP_ASKS = {", "var IND_INPUTS = {", "var MORTALITY_CREDIBLE = {"):
            assert literal not in src, f"the render redeclares {literal.split()[1]} instead of reading it"
