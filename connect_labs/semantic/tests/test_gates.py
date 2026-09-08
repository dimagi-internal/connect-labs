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


def test_credibility_has_exactly_one_source():
    """The tables must come from deployment.yml, not a literal in this module.

    They lived in three places at once: `var MORTALITY_CREDIBLE` in the render,
    literals in gates.py, and `settings:` in registry/kmc/deployment.yml — which
    the compiler cannot do without, since an llo-scoped suppression rule will not
    compile without them. Three copies of one human judgement, and when they
    disagree every engine still confidently returns a number.
    """
    import connect_labs.semantic.gates as g

    assert not hasattr(g, "MORTALITY_CREDIBLE"), "credibility must be read, not redeclared here"
    assert not hasattr(g, "COMPLETION_CREDIBLE"), "credibility must be read, not redeclared here"


def test_the_two_readings_of_the_table_agree():
    """gates.py asks 'is X NOT false'; the compiler asks 'is X true'.

    Those are a deny-list and an allow-list, and they agree ONLY when every LLO is
    listed explicitly. deployment.yml does that deliberately — an LLO omitted from
    `completion_recording_credible` would read credible here and suppressed there,
    which is the exact shape of bug a shared table is supposed to prevent.
    """
    from connect_labs.semantic.gates import credible_for
    from connect_labs.semantic.runtime import load_deployment

    llo_map, settings = load_deployment()
    every_llo = sorted(set(llo_map.values()))

    for setting, indicator in (
        ("mortality_recording_credible", "C14"),
        ("completion_recording_credible", "C18"),
    ):
        table = settings[setting]
        missing = [llo for llo in every_llo if llo not in table]
        assert not missing, f"{setting} does not state a verdict for {missing}"

        allow_list = {llo for llo, ok in table.items() if ok}
        deny_list = {llo for llo in every_llo if credible_for(indicator, llo)}
        assert allow_list == deny_list, f"{setting}: compiler sees {allow_list}, gates sees {deny_list}"


def test_programme_scope_is_never_credibility_gated():
    """Pooling every LLO is the one place the gate must not fire."""
    from connect_labs.semantic.gates import credible_for

    assert credible_for("C14", None) is True
    assert credible_for("C18", None) is True


def _render_app_asks() -> dict[str, dict[str, bool]]:
    """Parse APP_ASKS out of the render JS.

    The map is DUPLICATED — once here in Python, once as a JS literal in
    kmc_programme_metrics_render.js — and the render's copy is the one that
    actually decides whether a cell reads "not in this app". Drift between them is
    silent and one-sided: the server can say a field is available while the browser
    prints n/a over a real number.
    """
    import json
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "workflow" / "templates" / "kmc_programme_metrics_render.js"
    ).read_text()
    start = src.index("var APP_ASKS = {")
    end = src.index("\n  };\n", start)
    literal = src[start + len("var APP_ASKS = ") : end + len("\n  }")]
    literal = re.sub(r"(\w+):", r'"\1":', literal)  # bare keys -> JSON keys
    literal = re.sub(r",(\s*[}\]])", r"\1", literal)  # trailing commas
    return json.loads(literal)


def test_app_asks_matches_the_render_copy():
    """The two copies must agree, field by field."""
    from connect_labs.semantic.gates import APP_ASKS

    js = _render_app_asks()
    assert set(js) == set(APP_ASKS), "opportunity sets differ between gates.py and the render"
    for opp in sorted(APP_ASKS):
        assert js[opp] == APP_ASKS[opp], f"opp {opp} disagrees: render={js[opp]} gates={APP_ASKS[opp]}"


def test_c16_is_not_gated_notinapp_where_the_interval_is_derivable():
    """C16's input widened; the gate has to have widened with it.

    C16/C17 stopped reading the app's pre-computed `child_age_at_reg_discharge_date`
    and now derive `days_to_enrolment` from (reg_date - hospital_discharge_date),
    falling back to the pre-computed value. `APP_ASKS["days_discharge_to_reg"]` kept
    answering the OLD question, so four opportunities that only ever had the DATES
    read False — and EHA and GHI, the two LLOs that fix was written for, rendered
    "not in this app" over a real 72.40% (202/279) and 94.50% (361/382).

    Values are measured, not asserted: per-opportunity C16 denominators read live on
    2026-09-08 against a warm cache, agreeing across workflows 5476 and 5456.
    """
    from connect_labs.semantic.gates import any_asks

    # opp -> C16 denominator observed live
    OBSERVED = {
        10013: 28,
        10014: 651,
        10015: 954,
        10016: 279,
        10017: 382,
        10018: 139,
        10019: 1805,
        10020: 0,
        10021: 0,
        10022: 0,
        10042: 342,
    }
    for opp, denominator in OBSERVED.items():
        asks = any_asks("days_discharge_to_reg", [opp])
        if denominator > 0:
            assert asks, f"opp {opp} produced a C16 denominator of {denominator} but the gate says 'not in this app'"
        else:
            assert not asks, f"opp {opp} produced no C16 denominator; the gate should say 'not in this app'"


def test_c16_gate_holds_at_llo_scope():
    """The contradiction was visible at LLO scope, which is what the dashboard shows."""
    from connect_labs.semantic.gates import any_asks

    LLO_OPPS = {
        "BERI": [10042],
        "EHA": [10016],
        "GHI": [10017, 10020],
        "Kikapu": [10013],
        "NAMA": [10014, 10018, 10022],
        "PIPN": [10015, 10019, 10021],
    }
    for llo, opps in LLO_OPPS.items():
        assert any_asks("days_discharge_to_reg", opps), f"{llo} reports a C16 value but would render 'not in this app'"
