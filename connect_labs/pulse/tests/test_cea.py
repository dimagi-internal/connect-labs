"""Every opportunity ranked against Connect's CEA models. All data invented."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.pulse import cea, costs
from connect_labs.pulse.models import PulseCostEntry, PulseEvent, PulseOpportunity, PulseWork

D = Decimal
_seq = [0]


def _opp(oid, service, country="NG", **extra):
    return PulseOpportunity.objects.create(
        opportunity_id=oid,
        name=f"Opp {oid}",
        org_slug=f"org-{oid}",
        service_slug=service,
        country=country,
        currency="USD",
        usd_rate=D("1"),
        end_date=dt.date(2026, 1, 1),
        **extra,
    )


def _work(oid, usd, *, unit=1, status="approved", org=0, visits=1):
    _seq[0] += 1
    PulseWork.objects.create(
        work_key=f"cea-w{_seq[0]}",
        opportunity_id=oid,
        worker_hash="w",
        payment_unit_id=unit,
        status=status,
        approved_count=visits if status == "approved" else 0,
        created_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
        usd_to_worker=D(str(usd)),
        usd_to_org=D(str(org)),
    )


@pytest.fixture(autouse=True)
def _fresh_costs():
    costs.invalidate()
    yield
    costs.invalidate()


class TestValue:
    """The arithmetic, against the vendored model numbers themselves."""

    def test_at_the_models_own_price_the_multiple_is_the_models(self):
        ng = cea.models()["countries"]["NG"]["chc"]
        out = cea.value("chc", "NG", total_usd=ng["unit_cost"] * 1000, reached=1000)
        assert out["status"] == cea.STATUS_MODELLED
        assert out["x"] == pytest.approx(ng["x"])

    def test_twice_the_price_halves_the_multiple(self):
        ke = cea.models()["countries"]["KE"]["kc"]
        out = cea.value("kmc", "KE", total_usd=ke["unit_cost"] * 2 * 50, reached=50)
        assert out["x"] == pytest.approx(ke["x"] / 2)
        assert out["model"] == "kc"

    def test_deaths_and_cost_per_death(self):
        mw = cea.models()["countries"]["MW"]["mbw"]
        out = cea.value("mbw", "mw", total_usd=20_000, reached=1_000)
        assert out["deaths_averted"] == pytest.approx(1_000 * mw["deaths_per_100k"] / 100_000)
        assert out["cost_per_death"] == pytest.approx(20_000 / out["deaths_averted"])

    def test_bar(self):
        m = cea.models()
        ng = m["countries"]["NG"]["chc"]
        # Priced so the multiple lands exactly on the bar.
        price = ng["x"] * ng["unit_cost"] / m["bar_x"]
        assert cea.value("chc", "NG", price * 10, 10)["clears_bar"] is True
        assert cea.value("chc", "NG", price * 1.01 * 10, 10)["clears_bar"] is False

    def test_a_delivery_type_with_no_model_says_so_and_borrows_nothing(self):
        out = cea.value("chlorine", "NG", 5_000, 100)
        assert out["status"] == cea.STATUS_NO_MODEL
        assert out["x"] is None and out["model_x"] is None
        assert out["cost_per_person"] == 50

    def test_a_country_outside_the_models_says_so(self):
        out = cea.value("kmc", "IN", 5_000, 100)
        assert out["status"] == cea.STATUS_NO_COUNTRY
        assert out["x"] is None

    def test_nothing_delivered(self):
        out = cea.value("chc", "NG", 0, 0)
        assert out["status"] == cea.STATUS_NOTHING_DELIVERED
        assert out["model_x"] is not None and out["x"] is None


@pytest.mark.django_db
class TestRanking:
    def test_people_reached_is_the_most_used_payment_unit(self):
        # Connect keeps one completed work per (worker, beneficiary, payment unit) and stacks
        # repeat visits on it (commcare-connect form_receiver/processor.py, get_or_create on
        # those three), so works on a unit count people, and visits only raise approved_count.
        _opp(1, "kmc")
        for _ in range(10):
            _work(1, 3, unit=7)  # enrolment: every baby
        for _ in range(7):
            _work(1, 3, unit=8, visits=3)  # follow-up: fewer babies, three visits each
        _work(1, 0, unit=7, status="rejected")
        row = cea.ranking()["rows"][0]
        assert row["reached"] == 10
        assert row["total_usd"] == pytest.approx(51)
        assert row["cost_per_person"] == pytest.approx(5.1)

    def test_total_cost_includes_fixed_costs(self):
        _opp(2, "chc")
        for _ in range(100):
            _work(2, 1, org=0.5)
        PulseCostEntry.objects.create(opportunity_id=2, usd=D("50"), kind="fixed", reason="startup")
        row = cea.ranking()["rows"][0]
        assert row["total_usd"] == pytest.approx(200)
        assert row["fixed_usd"] == pytest.approx(50)
        assert row["cost_per_person"] == pytest.approx(2)

    def test_order_ranks_modelled_first_then_the_rest_by_spend(self):
        _opp(10, "chc", country="NG")
        _opp(11, "chc", country="UG")
        _opp(12, "chlorine")
        _opp(13, "kmc", country="IN")
        for oid, n in ((10, 100), (11, 100), (12, 400), (13, 50)):
            for _ in range(n):
                _work(oid, 2)
        out = cea.ranking()
        ids = [r["id"] for r in out["rows"]]
        # Nigeria's CHC multiple is far above Uganda's at the same price.
        assert ids[:2] == [10, 11]
        assert [r["rank"] for r in out["rows"]] == [1, 2, None, None]
        assert {r["id"]: r["status_text"] for r in out["rows"]}[12] == "No model"
        assert out["totals"]["modelled"] == 2
        assert out["totals"]["spend"] == pytest.approx(1300)
        modelled = [r for r in out["rows"] if r["rank"]]
        assert out["totals"]["portfolio_x"] == pytest.approx(
            sum(r["x"] * r["total_usd"] for r in modelled) / sum(r["total_usd"] for r in modelled)
        )

    def test_test_opportunities_never_appear(self):
        _opp(20, "chc", is_test=True)
        _work(20, 2)
        assert cea.ranking()["rows"] == []

    def test_location_is_the_centre_of_approved_visits(self):
        _opp(30, "chc")
        _work(30, 2)
        for i, (lat, lon, status) in enumerate(((10, 8, "approved"), (12, 10, "approved"), (40, 40, "rejected"))):
            PulseEvent.objects.create(
                connect_visit_id=9000 + i,
                opportunity_id=30,
                field_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
                sync_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
                lat=lat,
                lon=lon,
                status=status,
            )
        row = cea.ranking()["rows"][0]
        assert (row["lat"], row["lon"]) == (11, 9)


@pytest.mark.django_db
class TestPage:
    def test_requires_a_session(self, client):
        resp = client.get(reverse("pulse:cea"))
        assert resp.status_code == 302

    def test_renders_json_and_csv(self, client, django_user_model):
        user = django_user_model.objects.create(username="cea-viewer")
        client.force_login(user)
        _opp(40, "chc")
        _work(40, 2)
        page = client.get(reverse("pulse:cea"))
        assert page.status_code == 200
        assert b"Cost-effectiveness of every opportunity" in page.content
        data = client.get(reverse("pulse:cea") + "?format=json").json()
        assert data["rows"][0]["id"] == 40
        csv = client.get(reverse("pulse:cea") + "?format=csv").content.decode()
        assert csv.splitlines()[0].startswith("rank,id,name")
        assert "Opp 40" in csv
