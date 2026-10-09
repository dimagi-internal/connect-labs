"""Per-state EMOD inputs: the rain-season functions and the registry read."""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators.emod import states
from connect_labs.labs.indicators.models import IndicatorValue
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value


def _rain(peak_month: int, spread: float = 1.0) -> list[float]:
    """A smooth seasonal vector peaking at ``peak_month`` (1-12)."""
    base = [200.0, 150.0, 100.0, 50.0, 20.0, 5.0, 1.0]  # distance 0..6 months from the peak
    out = []
    for m in range(1, 13):
        d = min((m - peak_month) % 12, (peak_month - m) % 12)
        out.append(base[d] * spread)
    return out


class TestOnset:
    def test_onset_tracks_rain(self):
        assert states.onset_month(_rain(5)) <= 4
        assert states.onset_month(_rain(9)) >= 6

    def test_onset_wraps_the_year(self):
        assert states.onset_month(_rain(12)) in (10, 11, 12, 1)

    def test_flat_rain_is_still_a_month(self):
        assert 1 <= states.onset_month([10.0] * 12) <= 12


class TestHabitat:
    def test_habitat_lag_and_floor(self):
        rain = [0.0] * 12
        rain[4] = 300.0  # May only
        times, values = states.habitat_curve(rain)
        assert times[:3] == [0, 15, 46] and times[-1] == 365 and len(times) == len(values) == 14
        assert all(a < b for a, b in zip(times, times[1:]))
        assert values.index(max(values)) == 6  # June (index 6 after the day-0 knot): one month after the May rain
        assert max(values) == 1.0
        assert min(values) == 0.1  # dry months floored, not zero
        assert values[5] == 0.1  # May itself carries April's (zero) rain

    def test_curve_is_periodic(self):
        times, values = states.habitat_curve(_rain(9))
        assert times[0] == 0 and times[-1] == 365
        assert all(a < b for a, b in zip(times, times[1:]))
        assert values[0] == values[-1]
        dec, jan = values[-2], values[1]
        assert min(dec, jan) <= values[0] <= max(dec, jan)

    def test_all_dry_year_is_a_flat_floor(self):
        _, values = states.habitat_curve([0.0] * 12)
        assert set(values) == {0.1}

    def test_wrong_length_is_refused(self):
        with pytest.raises(ValueError):
            states.habitat_curve([1.0] * 11)

    def test_drops_into_a_worker_setting(self):
        import importlib.util
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[4] / "tools" / "pmc_emod" / "worker" / "run_scenarios.py"
        spec = importlib.util.spec_from_file_location("run_scenarios_under_test", path)
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except ImportError:
            pytest.skip("EMOD worker imports are not installed here")
        times, values = states.habitat_curve(_rain(8))
        setting = {
            "name": "x",
            "larval_capacity": 6e7,
            "habitat_times": times,
            "habitat_values": values,
            "pop": 100,
            "case_mgmt": 0.5,
            "net_coverage": 0.5,
        }
        req = {"setting": setting, "schedules": [{"code": "none", "rounds": []}], "seeds": [0], "years": 1}
        try:
            mod.validate_request(req)
        except Exception as e:  # a request-shape complaint unrelated to the habitat is not ours
            assert "habitat" not in str(e), e


def _state(name, bid, x, *, prev=40.0, inc=250.0, pop=500_000, rain=None, rain_extra=True):
    b = make_boundary("NGA", 1, name, bid, x=x)
    if prev is not None:
        set_value(b, "malaria_prevalence", prev, year=2021)
    if inc is not None:
        set_value(b, "malaria_incidence", inc)
    if pop is not None:
        set_value(b, "pop_u5", pop)
    if rain is not None:
        row = set_value(b, "rain_peak_month", float(rain.index(max(rain)) + 1))
        if rain_extra:
            IndicatorValue.objects.filter(pk=row.pk).update(extra={"monthly_mm": rain, "window": "2015-2024"})
    return b


@pytest.mark.django_db
class TestStateInputs:
    def test_complete_state_is_written_with_derived_fields(self):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        _state("Ondo", "NGA-1-28", 2, prev=44.8, inc=283.1, pop=600_000, rain=_rain(9))
        out, skipped = states.state_inputs(None)
        assert skipped == []
        (ondo,) = out
        assert ondo["name"] == "Ondo"
        assert ondo["pfpr_target"] == pytest.approx(0.448)
        assert ondo["incidence_per_1000"] == 283.1
        assert ondo["pop_u5"] == 600_000
        assert len(ondo["rain_monthly"]) == 12
        assert ondo["onset_month"] >= 6
        assert 0 < ondo["rain_wettest_quarter"] <= 100

    def test_missing_rain_is_skipped_with_reason(self):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        _state("Ondo", "NGA-1-28", 2, rain=_rain(9))
        _state("Kano", "NGA-1-20", 4, rain=None)
        _state("Lagos", "NGA-1-25", 6, rain=_rain(6), rain_extra=False)
        out, skipped = states.state_inputs(None)
        assert [s["name"] for s in out] == ["Ondo"]
        reasons = {s["name"]: s["reason"] for s in skipped}
        assert "rain_peak_month" in reasons["Kano"]
        assert "monthly_mm" in reasons["Lagos"]

    def test_inherited_rain_is_skipped(self):
        country = make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        row = set_value(country, "rain_peak_month", 9.0)
        IndicatorValue.objects.filter(pk=row.pk).update(extra={"monthly_mm": _rain(9)})
        _state("Ondo", "NGA-1-28", 2, rain=None)
        out, skipped = states.state_inputs(None)
        assert out == []
        assert "inherited" in skipped[0]["reason"]

    def test_names_filter_and_unknown_name(self):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        _state("Ondo", "NGA-1-28", 2, rain=_rain(9))
        _state("Kano", "NGA-1-20", 4, rain=_rain(8))
        out, skipped = states.state_inputs(["ondo", "Atlantis"])
        assert [s["name"] for s in out] == ["Ondo"]
        assert skipped == [{"name": "Atlantis", "reason": "no such state boundary"}]


@pytest.mark.django_db
def test_command_writes_the_inputs_file(tmp_path):
    import json

    from django.core.management import call_command

    make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
    _state("Ondo", "NGA-1-28", 2, rain=_rain(9))
    _state("Kano", "NGA-1-20", 4, rain=None)
    out = tmp_path / "inputs.json"
    call_command("export_pmc_state_inputs", out=str(out))
    doc = json.loads(out.read_text())
    assert "generated" not in doc
    assert [s["name"] for s in doc["states"]] == ["Ondo"]
    assert doc["skipped"][0]["name"] == "Kano"
