"""The PMC schedule explorer: the model data, the costing, the page and the agent tool."""

from __future__ import annotations

import pytest
from canopy_sdk.keys import generate_private_key, private_pem
from django.urls import reverse

from connect_labs.labs import canopy
from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import pmc
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import targeting

DEFAULTS = pmc.costs_or_default()


def _rows():
    return {r["code"]: r for r in pmc.schedule_rows(DEFAULTS)}


class TestTheSweep:
    def test_the_proposal_price_is_about_a_dollar_a_dose(self):
        # $0.80 a visit + 20% fee, over the 95% of visits that give a dose.
        assert pmc.cost_per_dose(**DEFAULTS) == pytest.approx(1.0105, abs=1e-4)

    @pytest.mark.parametrize(
        "code, reported",
        [
            # The six-seed run's own cost per case averted at $1.01 a dose.
            ("connect_monthly_in_season_3_24", 5.9),
            ("connect_quarterly_3_24", 12.3),
            ("connect_bimonthly_3_24", 14.6),
            ("connect_quarterly_12_24", 19.3),
        ],
    )
    def test_recomputed_costs_match_what_the_model_run_reported(self, code, reported):
        assert _rows()[code]["cost_per_case_averted"] == pytest.approx(reported, abs=0.2)

    def test_an_effect_inside_its_own_noise_is_not_costed(self):
        epi = _rows()["epi_linked"]
        assert epi["too_noisy"] is True
        assert epi["cost_per_case_averted"] is None

    def test_the_comparison_point_is_not_called_noisy(self):
        assert _rows()["none"]["too_noisy"] is False
        assert _rows()["none"]["cost_per_case_averted"] is None

    def test_monthly_through_the_season_is_the_best_value(self):
        assert pmc.best_schedule(pmc.schedule_rows(DEFAULTS))["code"] == "connect_monthly_in_season_3_24"

    def test_the_answer_moves_with_the_price(self):
        dearer = {r["code"]: r for r in pmc.schedule_rows({**DEFAULTS, "cost_per_visit": 1.6})}
        base = _rows()["connect_quarterly_3_24"]["cost_per_case_averted"]
        assert dearer["connect_quarterly_3_24"]["cost_per_case_averted"] == pytest.approx(2 * base, abs=0.05)

    @pytest.mark.parametrize(
        "bad",
        [
            {"dose_rate": 0},
            {"dose_rate": 1.5},
            {"cost_per_visit": -1},
            {"cost_per_visit": float("nan")},
            {"platform_fee": float("inf")},
        ],
    )
    def test_impossible_prices_are_refused(self, bad):
        with pytest.raises(ValueError):
            pmc.cost_per_dose(**{**DEFAULTS, **bad})

    @pytest.mark.django_db  # the summary reads Nigeria's incidence from the registry
    def test_every_answer_says_it_is_uncalibrated(self):
        out = pmc.summary(DEFAULTS)
        assert out["setting"]["calibrated"] is False
        assert any("illustrative" in c for c in out["caveats"])

    @pytest.mark.parametrize("n, want", [(332_465, 330_000), (1_976_741, 2_000_000), (65_709, 66_000), (0, 0)])
    def test_projections_are_rounded_to_what_an_uncalibrated_model_can_claim(self, n, want):
        assert pmc.approx(n) == want

    @pytest.mark.parametrize(
        "prev, rain, fit",
        [
            (44.8, 41.5, "near"),  # Ondo
            (35.6, 39.8, "near"),  # Ogun
            (20.0, 42.0, "prevalence_differs"),
            # Kano: southern-level prevalence, Sahel seasonality. SMC country.
            (54.0, 77.2, "more_seasonal"),
            (40.3, 79.5, "more_seasonal"),  # Sokoto
            # Kebbi: Sahel seasonality AND far higher prevalence -- the seasonality is the reason shown.
            (75.6, 72.1, "more_seasonal"),
            (40.0, 25.0, "less_seasonal"),
            (None, 41.0, "unknown"),
            (44.0, None, "unknown"),
        ],
    )
    def test_fit_needs_both_burden_and_seasonality_to_match(self, prev, rain, fit):
        assert pmc.fit_for(prev, rain) == fit


@pytest.fixture
def nigeria(db):
    make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
    ondo = make_boundary("NGA", 1, "Ondo", "NGA-1-28", x=2)
    kano = make_boundary("NGA", 1, "Kano", "NGA-1-20", x=4)
    set_value(ondo, "malaria_prevalence", 44.8, year=2021)
    set_value(ondo, "pop_u5", 600_000)
    set_value(ondo, "rain_wettest_quarter", 41.5)
    set_value(ondo, "malaria_incidence", 283.1)
    set_value(kano, "malaria_prevalence", 40.0, year=2021)
    set_value(kano, "rain_wettest_quarter", 77.2)
    set_value(kano, "pop_u5", 3_000_000)
    return ondo, kano


@pytest.mark.django_db
class TestStates:
    def test_states_rank_by_burden_and_project_the_chosen_schedule(self, nigeria):
        rows = pmc.state_rows("connect_monthly_in_season_3_24", DEFAULTS)

        assert [r["name"] for r in rows] == ["Ondo", "Kano"]
        ondo = rows[0]
        assert ondo["fit"] == "near"
        assert ondo["children_3_24m"] == 210_000  # 21/60 of 600,000 under-5s
        sched = _rows()["connect_monthly_in_season_3_24"]
        # Reconciles from the row's own columns: children x MAP incidence x EMOD effect.
        assert ondo["projection"]["cases_averted_per_year"] == pmc.approx(
            210_000 * 283.1 / 1000 * sched["averted_pct"] / 100
        )
        # Near-model prevalence, Sahel seasonality: not the modelled setting.
        assert rows[1]["fit"] == "more_seasonal"
        # A 15% state projected with a 44% setting's incidence would invent cases.
        assert rows[1]["projection"] is None

    def test_states_are_ranked_by_their_own_burden(self, nigeria):
        """Same model, different malaria: the higher-incidence state averts more cases
        per dose, so it ranks as more cost-effective."""
        cr = make_boundary("NGA", 1, "Cross River", "NGA-1-9", x=6)
        set_value(cr, "malaria_prevalence", 40.6, year=2021)
        set_value(cr, "rain_wettest_quarter", 42.4)
        set_value(cr, "malaria_incidence", 340.0)
        set_value(cr, "pop_u5", 600_000)

        rows = {r["name"]: r for r in pmc.state_rows("connect_monthly_in_season_3_24", DEFAULTS)}

        assert rows["Cross River"]["rank"] == 1 and rows["Ondo"]["rank"] == 2
        assert (
            rows["Cross River"]["projection"]["cost_per_case_averted"]
            < rows["Ondo"]["projection"]["cost_per_case_averted"]
        )
        assert rows["Kano"]["rank"] is None  # Sahel-seasonal: never ranked

    def test_the_schedule_comparison_is_costed_at_national_incidence(self, nigeria):
        ng = AdminBoundary.objects.get(iso_code="NGA", admin_level=0)
        set_value(ng, "malaria_incidence", 272.3)
        out = pmc.summary(DEFAULTS)
        monthly = {r["code"]: r for r in out["schedules"]}["connect_monthly_in_season_3_24"]
        assert out["reference_incidence"]["value"] == 272.3
        assert monthly["basis"] == "map_incidence"
        # ~4.86 doses/child/yr at $1.01, over 0.2723 x 31.1% cases averted per child.
        assert monthly["cost_per_case_averted"] == pytest.approx(58.0, abs=1.0)
        # The uncalibrated setting's own figure is kept, for reference only.
        assert monthly["model_setting"]["cost_per_case_averted"] == pytest.approx(5.9, abs=0.2)

    def test_an_unknown_schedule_is_refused(self, nigeria):
        with pytest.raises(ValueError):
            pmc.state_rows("weekly", DEFAULTS)


@pytest.mark.django_db
class TestThePage:
    @pytest.fixture
    def client_in(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="pm", password="x"))
        return client

    def test_the_page_renders(self, client_in, nigeria):
        body = client_in.get(reverse("targeting:pmc")).content.decode()
        assert "Which PMC schedule, and where?" in body
        assert "Illustrative." in body
        assert "indicators/pmc/pmc.js" in body
        # Two answers, in the order a programme is designed: how, then where.
        assert body.index(">How<") < body.index(">Where<")

    def test_the_data_endpoint_defaults_to_the_best_schedule(self, client_in, nigeria):
        got = client_in.get(reverse("targeting:pmc_data")).json()
        assert got["schedule"] == "connect_monthly_in_season_3_24"
        assert [s["name"] for s in got["states"]] == ["Ondo", "Kano"]

    def test_the_data_endpoint_takes_the_visitors_prices(self, client_in, nigeria):
        got = client_in.get(reverse("targeting:pmc_data"), {"cost_per_visit": 1.6, "dose_rate": 2}).json()
        # A nonsense dose rate falls back to the proposal's own.
        assert got["costs"]["dose_rate"] == DEFAULTS["dose_rate"]
        assert got["costs"]["cost_per_dose"] == pytest.approx(2.021, abs=1e-3)

    def test_a_non_number_price_falls_back_rather_than_breaking_the_json(self, client_in, nigeria):
        got = client_in.get(reverse("targeting:pmc_data"), {"cost_per_visit": "nan", "platform_fee": "inf"}).json()
        assert got["costs"]["cost_per_visit"] == DEFAULTS["cost_per_visit"]
        assert got["costs"]["platform_fee"] == DEFAULTS["platform_fee"]

    def test_the_agent_panel_declares_the_pmc_resource(self, client, django_user_model, settings, nigeria):
        base = "https://labs.example.org"
        settings.LABS_PUBLIC_URL = base
        settings.CANOPY_BASE_URL = f"{base}/canopy"
        settings.CANOPY_APP_NAME = "connect-labs"
        settings.CANOPY_AGENT_SLUG = "ace"
        settings.CANOPY_SIGNING_KEY = private_pem(generate_private_key("EdDSA"))
        settings.CANOPY_CLIENT_ID = f"{base}/canopy/oauth/client.json"
        client.force_login(django_user_model.objects.create_user(username="pm2", password="x"))

        body = client.get(reverse("targeting:pmc"), {"schedule": "connect_quarterly_3_24"}).content.decode()

        assert "labs-targeting://pmc" in body
        assert "targeting_pmc_schedules" in body
        assert "connect_quarterly_3_24" in body

    def test_the_page_may_use_the_targeting_tools(self):
        assert canopy.PAGE_SCOPES["targeting:pmc"] == ("targeting:read",)
        assert "targeting_pmc_schedules" in canopy.SCOPE_TOOLS["targeting:read"]


@pytest.mark.django_db
class TestTheAgentTool:
    def test_it_returns_schedules_states_and_caveats(self, nigeria):
        got = targeting.targeting_pmc_schedules(None)
        assert got["best_schedule"] == "connect_monthly_in_season_3_24"
        assert got["schedule"] == got["best_schedule"]
        assert {s["name"] for s in got["states"]} == {"Ondo", "Kano"}
        assert got["caveats"]

    def test_it_narrows_to_named_states_and_says_which_it_could_not_find(self, nigeria):
        got = targeting.targeting_pmc_schedules(None, states=["ondo", "Atlantis"])
        assert [s["name"] for s in got["states"]] == ["Ondo"]
        assert got["states_not_found"] == ["atlantis"]

    def test_it_hands_back_the_explorer_opened_on_those_states(self, nigeria):
        """The targeting -> model hand-off: the agent passes the selected states and
        gives the visitor a link to the explorer showing exactly them."""
        got = targeting.targeting_pmc_schedules(None, states=["Ondo", "Kano"])
        path = got["explorer_path"]
        assert path.startswith("/labs/targeting/pmc/?")
        assert "states=Ondo%2CKano" in path or "states=Kano%2COndo" in path
        # Defaults are left out so the link stays readable in a chat panel.
        assert "schedule=" not in path and "cost_per_visit=" not in path
        dearer = targeting.targeting_pmc_schedules(None, states=["Ondo"], cost_per_visit=1.2)
        assert "cost_per_visit=1.2" in dearer["explorer_path"]

    def test_it_refuses_an_unknown_schedule(self, nigeria):
        with pytest.raises(MCPToolError):
            targeting.targeting_pmc_schedules(None, schedule="weekly")

    def test_it_refuses_an_impossible_price(self, nigeria):
        with pytest.raises(MCPToolError):
            targeting.targeting_pmc_schedules(None, dose_rate=0)
