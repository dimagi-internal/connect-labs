"""The program page — and that it agrees with Pulse to the dollar.

All data invented.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.marketplace import queries
from connect_labs.marketplace.testing import make_partner
from connect_labs.pulse.models import PulseOpportunity, PulseProgram, PulseWork
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse

_seq = [0]


def _work(opp_id, service, worker, org=0, *, program_id=None, org_slug="org", status="approved"):
    _seq[0] += 1
    PulseWork.objects.create(
        work_key=f"k{_seq[0]}",
        opportunity_id=opp_id,
        program_id=program_id,
        org_slug=org_slug,
        worker_hash="w",
        status=status,
        service_slug=service,
        created_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
        usd_to_worker=Decimal(str(worker)),
        usd_to_org=Decimal(str(org)),
    )


def _opp(
    opp_id,
    service,
    *,
    visits=0,
    budget=None,
    live=True,
    program_id=None,
    org_slug="org",
    currency="USD",
    is_test=False,
):
    return PulseOpportunity.objects.create(
        opportunity_id=opp_id,
        name=f"Opp {opp_id}",
        org_slug=org_slug,
        service_slug=service,
        program_id=program_id,
        lifetime_visit_count=visits,
        total_budget=budget,
        currency=currency,
        usd_rate=Decimal("1") if currency == "USD" else None,
        is_active=live,
        end_date=None if live else dt.date(2025, 1, 1),
        is_test=is_test,
    )


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x")


@pytest.fixture
def market(db):
    """A realistic mix: an established program, one with a queue, one with
    demand and no supply, and a test program and ACE that must not leak."""
    PulseProgram.objects.create(program_id=1, name="CHC Nigeria", delivery_type="chc")
    PulseProgram.objects.create(program_id=2, name="Founders Pledge Test Program", delivery_type="chc", is_test=True)

    make_partner("Lakeside Health", "LH", countries=["Nigeria"])
    _opp(1, "chc", visits=9_000, budget=100_000, program_id=1, org_slug="lakeside-health")
    _work(1, "chc", 30_000, 10_000, program_id=1, org_slug="lakeside-health")
    # Ingest flags every opportunity under a test program.
    _opp(2, "chc", visits=40, budget=312_500, program_id=2, is_test=True)
    _work(2, "chc", 20, 5, program_id=2)
    # An ACE demo run carrying a real delivery type: must not make Nutrition
    # look delivered.
    _opp(5, "nutrition", visits=6, budget=5_000, org_slug="ai-demo-space", is_test=True)
    _work(5, "nutrition", 8, org_slug="ai-demo-space")

    _opp(3, "kmc", visits=500, budget=50_000, org_slug="lakeside-health")
    _work(3, "kmc", 8_000, 2_000, org_slug="lakeside-health")

    _opp(4, "ace", visits=19, budget=68_000)
    _work(4, "ace", 143)

    nutrition = Solicitation.objects.create(slug="rutf-2026", title="RUTF", status="closed", delivery_type="nutrition")
    for i in range(3):
        org = make_partner(f"Applicant {i}", f"A{i}", countries=["Nigeria"])
        SolicitationResponse.objects.create(
            solicitation=nutrition, llo_entity=org, source_row=2 + i, org_name=org.name, match_state="name"
        )
    queries.invalidate()


def _cards():
    return {c["slug"]: c for c in queries.program_cards()}


@pytest.mark.django_db
class TestItTiesOutWithPulse:
    """The requirement the page is built around: this page and the Pulse wall
    must not quote different numbers for the same program. Asserted against
    Pulse's real endpoint, not against a copy of its logic.
    """

    def test_paid_out_matches_pulse_money_by_service(self, client, user, market):
        client.force_login(user)
        pulse = client.get(reverse("pulse:api_summary")).json()
        by_service = {row["service"]: row["usd_total"] for row in pulse["money"]["by_service"]}
        for slug, card in _cards().items():
            if slug in by_service:
                assert card["spent"] == int(by_service[slug]), slug

    def test_services_delivered_match_pulses_service_menu(self, client, user, market):
        client.force_login(user)
        pulse = client.get(reverse("pulse:api_summary")).json()
        menu = {row["slug"]: row["visits"] for row in pulse["services"]}
        for slug, card in _cards().items():
            if card["services"]:
                assert card["services"] == menu[slug], slug

    def test_test_work_is_in_neither_pulse_nor_the_card(self, client, user, market):
        """Both leave scaffolding out, so they agree on the real figure."""
        client.force_login(user)
        pulse = client.get(reverse("pulse:api_summary")).json()
        by_service = {row["service"]: row["usd_total"] for row in pulse["money"]["by_service"]}
        assert by_service["chc"] == 40_000
        assert _cards()["chc"]["spent"] == 40_000

    def test_ace_demo_runs_do_not_count_as_delivery(self, market):
        nutrition = _cards()["nutrition"]
        assert nutrition["services"] == 0
        assert nutrition["spent"] == 0

    def test_a_test_programs_budget_is_not_still_funded(self, market):
        """Its $312,500 is a placeholder. Counted, one test row would dwarf the
        real program's remaining budget."""
        assert _cards()["chc"]["remaining"] == 100_000 - 40_000


@pytest.mark.django_db
class TestTheCards:
    def test_ace_has_no_card(self, market):
        assert "ace" not in _cards()

    def test_demand_with_no_supply_is_its_own_section(self, market):
        nutrition = _cards()["nutrition"]
        assert nutrition["state"] == "waiting"
        assert nutrition["applied"] == 3
        assert nutrition["delivering"] == 0

    def test_the_note_is_derived_from_the_figures(self, market):
        """Generated, never written: a hand-written line is true the day it is
        written and quietly false after."""
        assert _cards()["nutrition"]["note"].startswith("3 organisations have applied")

    def test_the_bar_is_paid_against_paid_plus_still_funded(self, market):
        chc = _cards()["chc"]
        assert chc["spent_pct"] == round(40_000 * 100 / (40_000 + 60_000), 1)

    def test_every_card_has_a_colour_of_its_own(self, market):
        hues = [c["hue"] for c in queries.program_cards()]
        assert len(hues) == len(set(hues))


@pytest.mark.django_db
class TestThePage:
    def test_renders_the_sections_and_the_totals(self, client, user, market):
        client.force_login(user)
        response = client.get(reverse("marketplace:programs"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Asked for, not yet delivered" in body
        assert "Child Health Campaign" in body
        assert "ACE" not in body.split("<body")[1].split("Pulse wall")[0]

    def test_still_funded_is_always_worded_as_an_upper_bound(self, client, user, market):
        """It is headroom on live budgets, not money committed. The page must
        never present it as the second."""
        client.force_login(user)
        body = client.get(reverse("marketplace:programs")).content.decode()
        assert "up to" in body.lower()
        assert "committed" not in body.lower()

    def test_requires_login(self, client, market):
        assert client.get(reverse("marketplace:programs")).status_code == 302

    def test_the_home_page_leads_to_it(self, client, user, market):
        client.force_login(user)
        assert reverse("marketplace:programs") in client.get(reverse("marketplace:home")).content.decode()


@pytest.mark.django_db
def test_the_old_programmes_address_redirects(client, user):
    client.force_login(user)
    response = client.get("/labs/marketplace/programmes/")
    assert response.status_code == 301
    assert response["Location"] == reverse("marketplace:programs")


def test_nutrition_is_named_for_what_is_delivered():
    from connect_labs.marketplace import programs

    assert programs.label("nutrition") == "Ready-to-Use Therapeutic Food (RUTF)"
