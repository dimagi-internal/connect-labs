"""The partner window and the worker window.

Two things these must never do.

**Blend two people's records.** Workers are addressed by the truncated hash the
rest of the UI shows, so the lookup is a prefix match. A prefix that matches
more than one worker is refused rather than answered with whichever row came
first — that would silently merge two workers' delivery and quality figures.

**Leak a named partner's commercial record.** This is the same data the partner
menu is gated on, so it fails closed the same way: no session and no permitting
token means 403, not a thinner payload.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse.models import (
    PulseEvent,
    PulseOpportunity,
    PulseOrganization,
    PulseProgram,
    PulseScalar,
    PulseWork,
)
from connect_labs.pulse.views import mint_public_token


@pytest.fixture
def partner(db, settings, django_user_model):
    settings.PULSE_POLLER_USERNAME = "poller-account"
    django_user_model.objects.create(username="poller-account")
    PulseScalar.objects.create(key="scope", value={"opportunities": 501, "lifetime_visits": 1_650_000})

    PulseOrganization.objects.create(slug="janna-health", name="Janna Health", funder_slug="givewell")
    PulseProgram.objects.create(program_id=10, name="CHC", delivery_type="chc", org_slug="janna-health")
    PulseOpportunity.objects.create(
        opportunity_id=1,
        name="CHC NG",
        org_slug="janna-health",
        program_id=10,
        country="NG",
        # Ingest always sets a service_slug (Connect's delivery_type, falling
        # back to a name regex), so a fixture without one is not a mirror of
        # what production stores.
        service_slug="chc",
        lifetime_visit_count=500,
        is_active=True,
    )

    now = timezone.now()
    # Two workers with deliberately different records.
    for i, (h, n_works, n_approved) in enumerate([("aaaa1111" + "0" * 8, 4, 3), ("bbbb2222" + "0" * 8, 2, 2)]):
        for k in range(n_works):
            PulseWork.objects.create(
                work_key=f"{h[:8]}{k:0>56}",
                opportunity_id=1,
                program_id=10,
                org_slug="janna-health",
                worker_hash=h,
                status="approved" if k < n_approved else "rejected",
                created_ts=now - timedelta(days=k),
                service_slug="chc",
                country="NG",
                usd_to_worker="1.00",
                usd_to_org="0.50",
            )
        PulseEvent.objects.create(
            connect_visit_id=100 + i,
            opportunity_id=1,
            program_id=10,
            org_slug="janna-health",
            worker_hash=h,
            field_ts=now - timedelta(hours=i + 1),
            sync_ts=now,
            lat=11.0,
            lon=7.6,
            country="NG",
            status="approved",
            flagged=(i == 0),
            flag_type="duration" if i == 0 else "",
            service_slug="chc",
        )
    return "janna-health"


@pytest.fixture
def viewer(client, django_user_model):
    client.force_login(django_user_model.objects.create(username="viewer"))
    return client


@pytest.mark.django_db
class TestPartnerWindow:
    def test_returns_the_partner_and_its_worker_roster(self, viewer, partner):
        data = viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()
        assert data["partner"]["slug"] == "janna-health"
        assert data["worker_count"] == 2
        assert {w["worker"] for w in data["workers"]} == {"aaaa11", "bbbb22"}

    def test_worker_rows_carry_volume_quality_and_money(self, viewer, partner):
        data = viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()
        row = next(w for w in data["workers"] if w["worker"] == "aaaa11")
        assert row["works"] == 4
        assert row["approved"] == 3
        assert row["approval_rate"] == pytest.approx(0.75)
        assert row["usd"] == pytest.approx(4.0)

    def test_never_returns_a_whole_worker_identifier_in_the_display_field(self, viewer, partner):
        """The UI shows six characters; the roster's display field matches, so a
        screenshot of this window cannot carry a full identifier."""
        data = viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()
        assert all(len(w["worker"]) == 6 for w in data["workers"])

    def test_money_totals_cover_both_streams(self, viewer, partner):
        m = viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()["money"]
        assert m["to_workers"] == pytest.approx(6.0)
        assert m["to_orgs"] == pytest.approx(3.0)
        assert m["total_paid"] == pytest.approx(9.0)

    def test_carries_a_weekly_series_for_the_graphs(self, viewer, partner):
        assert viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()["weekly"]

    def test_an_unknown_partner_is_404_not_an_empty_window(self, viewer, partner):
        assert viewer.get(reverse("pulse:api_partner"), {"org": "nope"}).status_code == 404

    def test_declares_when_the_roster_is_truncated(self, viewer, partner):
        """Silent truncation of a roster reads as "that is everyone"."""
        assert viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()["workers_truncated"] is False


@pytest.mark.django_db
class TestWorkerWindow:
    def test_returns_one_workers_record(self, viewer, partner):
        data = viewer.get(reverse("pulse:api_worker"), {"w": "aaaa11", "org": partner}).json()
        assert data["totals"]["works"] == 4
        assert data["totals"]["approved"] == 3
        assert data["totals"]["approval_rate"] == pytest.approx(0.75)

    def test_scopes_to_that_worker_alone(self, viewer, partner):
        """The other worker's four-vs-two record must not bleed in."""
        data = viewer.get(reverse("pulse:api_worker"), {"w": "bbbb22", "org": partner}).json()
        assert data["totals"]["works"] == 2
        assert data["totals"]["usd"] == pytest.approx(2.0)

    def test_an_ambiguous_prefix_is_refused_rather_than_guessed(self, viewer, partner):
        """A prefix matching two workers must not silently answer with one of
        them — that would merge two people's delivery records behind one name."""
        for i, suffix in enumerate(("cccc", "dddd")):
            PulseEvent.objects.create(
                connect_visit_id=900 + i,
                opportunity_id=1,
                program_id=10,
                org_slug="janna-health",
                worker_hash="shared0" + suffix,
                field_ts=timezone.now(),
                sync_ts=timezone.now(),
                country="NG",
                status="approved",
            )
        res = viewer.get(reverse("pulse:api_worker"), {"w": "shared0", "org": partner})
        assert res.status_code == 409
        assert res.json()["candidates"] == 2

    def test_an_unknown_worker_is_404(self, viewer, partner):
        assert viewer.get(reverse("pulse:api_worker"), {"w": "zzzzzz", "org": partner}).status_code == 404

    def test_a_missing_worker_argument_is_a_400(self, viewer, partner):
        assert viewer.get(reverse("pulse:api_worker")).status_code == 400

    def test_carries_recent_activity_for_the_mini_map(self, viewer, partner):
        data = viewer.get(reverse("pulse:api_worker"), {"w": "aaaa11", "org": partner}).json()
        assert isinstance(data["recent"], list)

    def test_no_beneficiary_identity_anywhere_in_the_payload(self, viewer, partner):
        body = viewer.get(reverse("pulse:api_worker"), {"w": "aaaa11", "org": partner}).content.decode()
        for forbidden in ("entity_name", "entity_id", "phone", "form_json"):
            assert forbidden not in body


@pytest.mark.django_db
class TestDrilldownFailsClosed:
    """Same gate as the partner menu: this is a named partner's commercial and
    quality record, so it is refused outright rather than thinned."""

    def test_anonymous_cannot_open_a_partner_window(self, client, partner):
        assert client.get(reverse("pulse:api_partner"), {"org": partner}).status_code == 403

    def test_anonymous_cannot_open_a_worker_window(self, client, partner):
        assert client.get(reverse("pulse:api_worker"), {"w": "aaaa11"}).status_code == 403

    def test_an_anonymised_token_cannot_either(self, client, partner):
        token = mint_public_token(None, label="anon", show_partner_names=False)
        res = client.get(reverse("pulse:api_partner"), {"org": partner, "token": token.token})
        assert res.status_code == 403

    def test_a_permitting_token_can(self, client, partner):
        token = mint_public_token(None, label="funder", show_partner_names=True)
        res = client.get(reverse("pulse:api_partner"), {"org": partner, "token": token.token})
        assert res.status_code == 200
        assert res.json()["worker_count"] == 2


@pytest.mark.django_db
class TestOpportunityBreakdown:
    """A partner is not one thing.

    On real data partners run between one and ninety-one opportunities, and the
    window rendered both identically — answering "how is this partner doing"
    without ever saying "at what".
    """

    @pytest.fixture
    def second_opp(self, partner):
        now = timezone.now()
        PulseOpportunity.objects.create(
            opportunity_id=2,
            name="KMC UG - P2",
            org_slug="janna-health",
            program_id=10,
            country="UG",
            service_slug="kmc",
            lifetime_visit_count=90,
            is_active=False,
        )
        PulseWork.objects.create(
            work_key="c" * 64,
            opportunity_id=2,
            program_id=10,
            org_slug="janna-health",
            worker_hash="cccc3333" + "0" * 8,
            status="approved",
            created_ts=now,
            service_slug="kmc",
            country="UG",
            usd_to_worker="3.00",
            usd_to_org="1.50",
        )
        return partner

    def test_a_single_opportunity_partner_still_lists_it(self, viewer, partner):
        rows = viewer.get(reverse("pulse:api_partner"), {"org": partner}).json()["opportunities"]
        assert len(rows) == 1
        assert rows[0]["name"] == "CHC NG"

    def test_each_opportunity_carries_its_own_figures(self, viewer, second_opp):
        rows = {
            o["id"]: o for o in viewer.get(reverse("pulse:api_partner"), {"org": second_opp}).json()["opportunities"]
        }
        assert rows[1]["works"] == 6 and rows[1]["service_name"] == "Child Health Campaign"
        assert rows[2]["works"] == 1 and rows[2]["service_name"] == "Kangaroo Mother Care"
        assert rows[2]["usd_total"] == pytest.approx(4.5)
        assert rows[2]["active"] is False

    def test_opportunity_names_are_connects_own_verbatim(self, viewer, second_opp):
        """Shortening one in labs would invent a name for something Connect and
        the partner both already call something."""
        names = {
            o["name"] for o in viewer.get(reverse("pulse:api_partner"), {"org": second_opp}).json()["opportunities"]
        }
        assert names == {"CHC NG", "KMC UG - P2"}

    def test_selecting_one_narrows_the_whole_window(self, viewer, second_opp):
        """The roster, chart and KPIs are server-side aggregates, so selecting
        an engagement has to re-scope them — a client-side filter would leave
        the partner's totals sitting above one opportunity's workers."""
        data = viewer.get(reverse("pulse:api_partner"), {"org": second_opp, "opportunity": 2}).json()
        assert data["money"]["works"] == 1
        assert data["money"]["total_paid"] == pytest.approx(4.5)
        assert {w["worker"] for w in data["workers"]} == {"cccc33"}

    def test_an_unknown_opportunity_is_ignored_not_an_error(self, viewer, second_opp):
        data = viewer.get(reverse("pulse:api_partner"), {"org": second_opp, "opportunity": 99999}).json()
        assert data["money"]["works"] == 7

    def test_currently_delivering_opportunities_come_first(self, viewer, second_opp):
        """Same ordering rule as the partner and programme menus, so "recent"
        means one thing across the app."""
        rows = viewer.get(reverse("pulse:api_partner"), {"org": second_opp}).json()["opportunities"]
        assert rows[0]["last_ts"] is not None


@pytest.mark.django_db
class TestSelectingAnOpportunityIsNotAOneWayDoor:
    """Selecting an engagement must not remove the way out of it.

    The figures narrow; the list of what you can narrow to does not. Scoping
    the engagement list as well collapsed the grid to the single card you had
    just picked — so there was no way back to the partner and no way across to
    any of its other opportunities.
    """

    @pytest.fixture
    def two_opps(self, partner):
        PulseOpportunity.objects.create(
            opportunity_id=2,
            name="KMC UG - P2",
            org_slug="janna-health",
            program_id=10,
            country="UG",
            service_slug="kmc",
            lifetime_visit_count=90,
        )
        PulseWork.objects.create(
            work_key="d" * 64,
            opportunity_id=2,
            program_id=10,
            org_slug="janna-health",
            worker_hash="dddd4444" + "0" * 8,
            status="approved",
            created_ts=timezone.now(),
            service_slug="kmc",
            country="UG",
            usd_to_worker="3.00",
            usd_to_org="1.50",
        )
        return partner

    def test_the_full_list_survives_selecting_one(self, viewer, two_opps):
        data = viewer.get(reverse("pulse:api_partner"), {"org": two_opps, "opportunity": 2}).json()
        assert len(data["opportunities"]) == 2, "the other engagement must stay reachable"

    def test_the_figures_still_narrow(self, viewer, two_opps):
        data = viewer.get(reverse("pulse:api_partner"), {"org": two_opps, "opportunity": 2}).json()
        assert data["money"]["works"] == 1
        assert {w["worker"] for w in data["workers"]} == {"dddd44"}

    def test_the_response_names_which_one_is_selected(self, viewer, two_opps):
        """The card cannot show a pressed state without being told which."""
        data = viewer.get(reverse("pulse:api_partner"), {"org": two_opps, "opportunity": 2}).json()
        assert data["selected_opportunity"] == 2
        assert viewer.get(reverse("pulse:api_partner"), {"org": two_opps}).json()["selected_opportunity"] is None
