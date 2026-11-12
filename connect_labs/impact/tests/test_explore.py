"""The cost-effectiveness explorer: the engine layer, and the page and API on top of it.

The numbers are checked against the live Sheets' own figures (Connect CHC model, Nov 2026): 6.0954 at
the current inputs and 23.9769 for scenario 1. If a refresh of the vendored specs moves them, that is the
point of the test: a model change should be seen.
"""

import pytest
from django.urls import reverse

from connect_labs.impact import engine, explore, valuation


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

    def test_the_api_returns_the_run_and_the_tornado(self, client_in):
        got = client_in.get(reverse("cea:models_run"), {"model": "chc", "i_cost": "2.5"}).json()
        assert got["run"]["overrides"] == {"cost": 2.5}
        assert got["run"]["headline"]["multiple"] == pytest.approx(
            explore.run("chc", {"cost": 2.5})["headline"]["multiple"]
        )
        assert got["tornado"]

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
