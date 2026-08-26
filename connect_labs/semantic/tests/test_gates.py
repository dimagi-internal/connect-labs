"""The gates decide whether a number may be shown at all."""

from __future__ import annotations

import pytest

from connect_labs.semantic.gates import credible_for, input_state


class TestInputAvailability:
    def test_ok_when_the_input_was_recorded(self):
        assert input_state("C20", {"anyrec_ever_danger_sign": 1}) == "ok"

    def test_unrecorded_when_the_scope_never_recorded_it(self):
        """A worker who logged no danger signs has not achieved a 0% rate."""
        assert input_state("C20", {"anyrec_ever_danger_sign": 0}) == "unrecorded"

    def test_an_ungated_indicator_is_always_ok(self):
        assert input_state("C01", {}) == "ok"

    def test_fails_open_when_the_gate_column_is_absent(self):
        """A missing gate means our wiring is wrong, not that nothing was collected.

        Blanking a real indicator on our own error is the worse failure -- the
        render makes the same choice in anyRecorded().
        """
        assert input_state("C20", {}) == "ok"

    def test_every_declared_input_must_be_present(self):
        row = {"anyrec_birth_weight_g": 1, "anyrec_enrollment_weight_g": 0}
        assert input_state("C28", row) == "unrecorded"


class TestCredibility:
    @pytest.mark.parametrize("llo,expected", [("PIPN", True), ("EHA", True), ("GHI", False), ("NAMA", False)])
    def test_mortality_only_for_credible_recorders(self, llo, expected):
        assert credible_for("C14", llo) is expected

    def test_programme_scope_is_never_gated(self):
        """Pooling is the point: the programme figure includes credible recorders."""
        assert credible_for("C14", None) is True

    def test_completion_gate_is_deny_listed_not_allow_listed(self):
        assert credible_for("C18", "GHI") is False
        assert credible_for("C18", "PIPN") is True

    def test_ungated_indicators_pass(self):
        assert credible_for("C09", "GHI") is True


class TestAppAsks:
    """'not in app' and 'unrecorded' are different facts about the programme."""

    def test_notinapp_when_no_opportunity_in_scope_asks(self):
        from connect_labs.semantic.gates import input_state

        # 10021's app does not ask days_discharge_to_reg, which C16/C17 need.
        assert input_state("C16", {"anyrec_days_discharge_to_reg": 0}, [10021]) == "notinapp"

    def test_unrecorded_when_the_app_asks_but_nothing_was_recorded(self):
        from connect_labs.semantic.gates import input_state

        # 10013's app does ask it, so an empty scope is 'unrecorded', not 'notinapp'.
        assert input_state("C16", {"anyrec_days_discharge_to_reg": 0}, [10013]) == "unrecorded"

    def test_any_opportunity_asking_is_enough(self):
        from connect_labs.semantic.gates import any_asks

        assert any_asks("days_discharge_to_reg", [10021, 10013]) is True
        assert any_asks("days_discharge_to_reg", [10021, 10022]) is False

    def test_derived_names_map_to_their_pipeline_column(self):
        from connect_labs.semantic.gates import any_asks

        # self_referral_count is stored as self_referral_visits in APP_ASKS.
        assert any_asks("self_referral_count", [10021]) is False
        assert any_asks("self_referral_count", [10013]) is True

    def test_unknown_opportunity_fails_open(self):
        from connect_labs.semantic.gates import any_asks

        assert any_asks("days_discharge_to_reg", [999999]) is True

    def test_no_scope_fails_open(self):
        from connect_labs.semantic.gates import any_asks

        assert any_asks("days_discharge_to_reg", None) is True

    def test_fourteen_of_twentytwo_opportunities_have_a_gap(self):
        """Guards the generalisation that produced the wrong reason on 369 cells."""
        from connect_labs.semantic.gates import APP_ASKS

        with_gap = [o for o, m in APP_ASKS.items() if any(v is False for v in m.values())]
        assert len(with_gap) == 14, f"expected 14 opportunities with a gap, got {len(with_gap)}"
