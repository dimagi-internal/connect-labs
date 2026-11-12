"""The cost-effectiveness explorer: the engine layer, and the page and API on top of it.

The numbers are checked against the live Sheets' own figures (Connect CHC model, Nov 2026): 6.0954 at
the current inputs and 23.9769 for scenario 1. If a refresh of the vendored specs moves them, that is the
point of the test: a model change should be seen.
"""

import pytest
from django.urls import reverse

from connect_labs.impact import engine, explore, groups, valuation


class TestEngineLayer:
    def test_it_reproduces_the_live_sheets_headline(self):
        run = explore.run("chc")
        assert run["headline"]["multiple"] == pytest.approx(6.095357753901075)
        assert run["headline"]["price_per_person"] == pytest.approx(1.84)
        assert run["headline"]["clears_bar"] is True and run["headline"]["bar"] == valuation.bar()
        assert any(r["key"] == "full_x" for r in run["chain"])

    def test_every_model_runs_at_its_own_inputs(self):
        for m in engine.MODELS:
            assert explore.run(m)["headline"]["multiple"] > 0

    def test_an_override_changes_the_answer_and_is_echoed(self):
        base = explore.run("chc")["headline"]["multiple"]
        run = explore.run("chc", {"cost": 2.5})
        assert run["overrides"] == {"cost": 2.5}
        assert run["headline"]["multiple"] < base

    def test_switching_the_extra_components_off_lowers_a_package_multiple(self):
        """Which components count for CHC is a model input: vitamin A and deworming each have a switch."""
        both = explore.run("chc")["headline"]["multiple"]
        ors_only = explore.run("chc", {"va_on": 0, "dw_on": 0})["headline"]["multiple"]
        assert ors_only < both

    def test_a_model_name_is_never_a_path(self):
        """The spec file is opened from the model name, so only the known models may be asked for."""
        for bad in ("../../etc/passwd", "chc/../kc", "", "CHC"):
            with pytest.raises(KeyError):
                engine.spec(bad)
            with pytest.raises(explore.ExploreError):
                explore.run(bad)

    def test_the_pages_script_never_parses_model_text_as_html(self):
        """Labels and sources come from the spec; the page builds nodes with textContent, so none is parsed as HTML."""
        from pathlib import Path

        page = Path(__file__).resolve().parents[2] / "templates" / "impact" / "models.html"
        assert "innerHTML" not in page.read_text(encoding="utf-8")

    def test_unknown_inputs_and_non_numbers_are_refused_by_name(self):
        with pytest.raises(explore.ExploreError, match="nope"):
            explore.run("chc", {"nope": 1})
        with pytest.raises(explore.ExploreError, match="ors"):
            explore.run("chc", {"ors": "lots"})
        with pytest.raises(explore.ExploreError, match="Unknown model"):
            explore.run("zzz")

    def test_the_models_own_scenarios_reproduce_the_sheet(self):
        got = explore.scenarios("chc")
        assert got[0]["multiple"] == pytest.approx(23.976866689655893)
        assert len(got) == len(engine.spec("chc")["scenarios"])

    def test_tornado_orders_by_swing_and_keeps_the_base_answer(self):
        rows = explore.tornado("chc")
        base = explore.run("chc")["headline"]["multiple"]
        assert rows and all(r["base"] == pytest.approx(base) for r in rows)
        assert [r["swing"] for r in rows] == sorted((r["swing"] for r in rows), reverse=True)
        # Only inputs the spec gives a low and a high case for.
        with_cases = {i["key"] for i in explore.inputs("chc") if i["low"] is not None and i["high"] is not None}
        assert {r["key"] for r in rows} == with_cases
        # A lower cost (the high case) buys more than a higher one.
        cost = next(r for r in rows if r["key"] == "cost")
        assert cost["multiple_at_high"] > cost["multiple_at_low"]

    def test_a_grid_agrees_with_single_runs_and_refuses_a_repeated_input(self):
        g = explore.grid("chc", "cost", [1.44, 2.5], "ors", [0.2, 0.6])
        assert g["multiples"][1][0] == pytest.approx(
            explore.run("chc", {"cost": 1.44, "ors": 0.6})["headline"]["multiple"]
        )
        with pytest.raises(explore.ExploreError):
            explore.grid("chc", "cost", [1], "cost", [1])

    def test_places_carry_the_inputs_that_reproduce_them(self):
        got = explore.places("chc")
        assert len(got) == 45 and got[0]["x"] >= got[-1]["x"]
        p = got[0]
        assert explore.run("chc", p["inputs"])["headline"]["multiple"] == pytest.approx(p["x"])

    def test_the_form_is_driven_by_the_specs_own_metadata(self):
        ins = {i["key"]: i for i in explore.inputs("chc")}
        assert ins["ors"]["block"] == "location" and ins["ors"]["editable"] is True
        assert ins["funging"]["editable"] is False and ins["va_on"]["unit"] == "switch"
        assert ins["cost"]["low"] == 2.5 and ins["cost"]["high"] == 1.44


@pytest.mark.django_db
class TestPageAndApi:
    @pytest.fixture
    def client_in(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="cem", password="x"))
        return client

    def test_the_page_renders_the_model_its_tabs_and_its_data(self, client_in):
        resp = client_in.get(reverse("cea:models"))
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "Cost-effectiveness models" in body and 'data-testid="cea-tabs"' in body
        assert 'id="explore-data"' in body and "Connect Child Health Campaign" in body

    def test_a_shared_link_restores_the_inputs(self, client_in):
        body = client_in.get(reverse("cea:models"), {"model": "mbw", "i_cost": "12"}).content.decode()
        assert '"model": "mbw"' in body and '"cost": 12.0' in body

    def test_a_bad_link_still_renders_with_the_reason(self, client_in):
        body = client_in.get(reverse("cea:models"), {"model": "chc", "i_nope": "1"}).content.decode()
        assert "nope" in body

    def test_the_api_returns_the_run_and_the_sensitivity(self, client_in):
        got = client_in.get(reverse("cea:models_run"), {"model": "chc", "i_cost": "2.5"}).json()
        assert got["run"]["overrides"] == {"cost": 2.5}
        assert got["run"]["headline"]["multiple"] == pytest.approx(
            explore.run("chc", {"cost": 2.5})["headline"]["multiple"]
        )
        assert got["sensitivity"]["rows"] and got["sensitivity"]["combined"]

    def test_the_api_returns_a_grid_on_request(self, client_in):
        got = client_in.get(
            reverse("cea:models_run"),
            {"model": "chc", "grid_x": "cost", "grid_xs": "1.44,2.5", "grid_y": "ors", "grid_ys": "0.2,0.6"},
        ).json()
        assert len(got["grid"]["multiples"]) == 2 and len(got["grid"]["multiples"][0]) == 2

    def test_the_api_refuses_bad_input_with_a_400_and_a_reason(self, client_in):
        resp = client_in.get(reverse("cea:models_run"), {"model": "chc", "i_ors": "lots"})
        assert resp.status_code == 400 and "ors" in resp.json()["error"]

    def test_both_need_a_login(self, client):
        assert client.get(reverse("cea:models")).status_code == 302
        assert client.get(reverse("cea:models_run")).status_code == 302


class TestSheetsCheck:
    """The drift check, on rows built from the spec itself (no Google), with one cell moved."""

    @staticmethod
    def _rows(model):
        """The Sheet's Model tab as the spec says it computes it: one row per spec row, in order."""
        from connect_labs.impact import engine

        spec = engine.spec(model)
        columns = list(spec["results"])
        return [
            [r["label"]] + [spec["results"][c].get(r["key"]) for c in columns]
            for r in spec["rows"]
            if not r.get("text")
        ]

    def test_an_identical_sheet_is_in_sync(self):
        from connect_labs.impact import sheets_check

        for m in engine.MODELS:
            got = sheets_check.compare(m, self._rows(m))
            assert got["in_sync"] and got["cells_compared"] > 100 and not got["differing"], got

    def test_one_moved_cell_is_named(self):
        from connect_labs.impact import sheets_check

        rows = self._rows("chc")
        heads = [r for r in rows if r[0].startswith("Cost-effectiveness: multiple of GiveWell")]
        assert len(heads) >= 2, "the headline and its working row share a label: both must be checked"
        head = heads[0]
        head[1] = head[1] + 1  # the headline's "current inputs" multiple, edited
        got = sheets_check.compare("chc", rows)
        assert got["in_sync"] is False
        assert [d["row"] for d in got["differing"]] == [head[0]]
        assert got["differing"][0]["live"] == pytest.approx(got["differing"][0]["vendored"] + 1)

    def test_a_row_deleted_from_the_sheet_is_reported(self):
        from connect_labs.impact import sheets_check

        rows = [r for r in self._rows("kc") if not r[0].startswith("Cost per")]
        assert sheets_check.compare("kc", rows)["rows_missing_in_live"]


class TestDeliveryIntoTheExplorer:
    """An opportunity valued where it was delivered gives the same multiple in the explorer."""

    @staticmethod
    def _delivery(model):
        from connect_labs.impact import engine, result

        class V:  # a resolved place input, as baseline.Value is read by result.evaluate
            def __init__(self, value):
                self.value = value

            def as_dict(self):
                return {"value": self.value}

        # A place that differs from the model's defaults on every location input it has.
        inputs = {k: V(engine.defaults(model)[k] * 0.8) for k in engine.location_keys(model)}
        areas = [{"name": "Somewhere", "level": "state or province", "share": 1.0, "inputs": inputs}]
        return result, areas, {k: v.value for k, v in inputs.items()}

    @pytest.mark.parametrize("model", ["chc", "mbw", "kc"])
    def test_the_explorer_at_a_deliverys_inputs_gives_the_deliverys_multiple(self, model):
        result, areas, place = self._delivery(model)
        out = result.evaluate(model, "NGA", areas, reached=1200, total_usd=3600.0, bar_x=valuation.bar())
        overrides = explore.delivery_overrides(model, place, out["cost_per_person"])
        run = explore.run(model, overrides)
        assert run["headline"]["price_per_person"] == pytest.approx(out["cost_per_person"])
        assert run["headline"]["multiple"] == pytest.approx(out["x"], rel=1e-9)
        assert run["headline"]["deaths_per_100k"] * 1200 / 100_000 == pytest.approx(out["deaths_averted"], rel=1e-9)

    def test_reprice_is_the_one_formula(self):
        from connect_labs.impact import result

        base = result.reprice(20.0, 2.0, 50.0, 1000, 4000.0, 6.0)
        assert base["x"] == pytest.approx(20.0 * 2.0 / 4.0) and base["cost_per_person"] == 4.0
        assert base["deaths_averted"] == pytest.approx(0.5) and base["cost_per_death"] == pytest.approx(8000.0)
        assert base["clears_bar"] is True
        # The what-if knobs: half the effect halves the multiple and the deaths; extra cost lowers the multiple.
        half = result.reprice(20.0, 2.0, 50.0, 1000, 4000.0, 6.0, effect=0.5)
        assert half["x"] == pytest.approx(base["x"] / 2) and half["deaths_averted"] == pytest.approx(0.25)
        assert result.reprice(20.0, 2.0, 50.0, 1000, 4000.0, 6.0, extra_per_person=4.0)["x"] == pytest.approx(5.0)
        # At the model's own price the multiple is the model's.
        assert result.reprice(20.0, 2.0, 50.0, 1000, 4000.0, 6.0, at_model_price=True)["x"] == pytest.approx(20.0)
        assert result.reprice(20.0, 2.0, 50.0, 0, 0.0, 6.0) is None


class TestSensitivity:
    def test_every_ranged_input_is_included_by_default(self):
        out = explore.sensitivity("chc")
        assert out["rows"] and all(r["included"] for r in out["rows"])
        assert {r["key"] for r in out["rows"]} == set(explore.default_included("chc"))
        assert out["base"] == pytest.approx(explore.run("chc")["headline"]["multiple"])

    def test_the_whole_set_spreads_wider_than_any_one_input(self):
        out = explore.sensitivity("chc")
        biggest = max(r["swing"] for r in out["rows"])
        assert out["combined"]["best"] - out["combined"]["worst"] > biggest
        assert out["combined"]["worst"] < out["base"] < out["combined"]["best"]

    def test_switching_an_input_off_removes_it_and_narrows_the_spread(self):
        full = explore.sensitivity("chc")
        keys = [r["key"] for r in full["rows"] if r["key"] != "cost"]
        part = explore.sensitivity("chc", include=keys)
        assert not next(r for r in part["rows"] if r["key"] == "cost")["included"]
        assert (
            part["combined"]["best"] - part["combined"]["worst"] < full["combined"]["best"] - full["combined"]["worst"]
        )

    def test_nothing_included_has_no_combined_spread(self):
        out = explore.sensitivity("chc", include=[])
        assert out["combined"] is None and out["groups"] == {} and not any(r["included"] for r in out["rows"])

    def test_one_input_alone_matches_the_one_at_a_time_figures(self):
        out = explore.sensitivity("chc", include=["cost"])
        row = next(r for r in out["rows"] if r["key"] == "cost")
        one = next(r for r in explore.tornado("chc") if r["key"] == "cost")
        assert row["multiple_at_low"] == pytest.approx(one["multiple_at_low"])
        assert {out["combined"]["worst"], out["combined"]["best"]} == {row["multiple_at_low"], row["multiple_at_high"]}

    def test_groups_cover_the_inputs_that_are_in(self):
        out = explore.sensitivity("chc")
        assert set(out["groups"]) <= set(groups.ORDER) and "cost" in out["groups"] and "baseline" in out["groups"]

    def test_unknown_keys_are_ignored(self):
        assert explore.sensitivity("chc", include=["cost", "nonsense"])["combined"]["n"] == 1


class TestGroups:
    @pytest.mark.parametrize("model", engine.MODELS)
    def test_every_listed_key_is_an_input_of_the_model_and_listed_once(self, model):
        keys = {i["key"] for i in engine.spec(model)["inputs"]}
        listed = [k for ks in groups.GROUPS[model].values() for k in ks]
        assert set(listed) <= keys, sorted(set(listed) - keys)
        assert len(listed) == len(set(listed)), "an input is in two groups"

    @pytest.mark.parametrize("model", engine.MODELS)
    def test_the_four_key_inputs_are_all_present(self, model):
        assert all(groups.GROUPS[model][g] for g in groups.KEY_GROUPS)

    def test_the_headline_inputs_sit_where_a_reader_expects(self):
        assert groups.group_of("chc", "deaths") == "baseline"
        assert groups.group_of("chc", "ors") == "counterfactual"
        assert groups.group_of("chc", "va_effect") == "effectiveness"
        assert groups.group_of("kc", "cost") == "cost"
        assert groups.group_of("chc", "uv") == "other"
