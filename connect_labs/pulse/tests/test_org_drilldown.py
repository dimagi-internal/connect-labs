"""Drilling into a delivery partner.

Two things here are load-bearing beyond "the filter works".

**Partner identity is not free to give away.** This read API is
unauthenticated — a clean ``curl`` of ``/labs/pulse/api/summary/`` returns 200
with the whole payload, and it has to, because a public token page has no
session and its JS still calls it. ``PulsePublicToken.show_partner_names`` has
always *documented* that an anonymised link renders partners as descriptors
rather than names, but nothing implemented it. So the tests below pin the
default to **deny**: naming a partner requires a session or a token minted to
permit it, and an anonymised caller cannot get there by dropping the token.

**A filtered header has to be recomputed, not inherited.** Leaving "501
opportunities / 1.65M services" above one partner's map is the same defect as
the inferred poller and the head-sliced replay — a true number answering a
question nobody asked.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse.models import (
    PulseEvent,
    PulseGridCell,
    PulseOpportunity,
    PulseOrganization,
    PulseProgram,
    PulseScalar,
    PulseWork,
)
from connect_labs.pulse.views import mint_public_token


@pytest.fixture
def portfolio(db, settings, django_user_model):
    settings.PULSE_POLLER_USERNAME = "poller-account"
    django_user_model.objects.create(username="poller-account")

    PulseScalar.objects.create(
        key="scope", value={"opportunities": 501, "lifetime_visits": 1_650_000, "programs": 109}
    )

    PulseOrganization.objects.create(slug="connect-nigeria", name="Connect Nigeria", funder_slug="givewell")
    PulseOrganization.objects.create(slug="living-goods", name="Living Goods", funder_slug="")

    PulseProgram.objects.create(program_id=10, name="CHC Nigeria", delivery_type="chc", org_slug="connect-nigeria")
    PulseProgram.objects.create(program_id=20, name="KMC Uganda", delivery_type="kmc", org_slug="living-goods")

    PulseOpportunity.objects.create(
        opportunity_id=1,
        name="CHC NG P1",
        org_slug="connect-nigeria",
        program_id=10,
        country="NG",
        lifetime_visit_count=1000,
        is_active=True,
    )
    PulseOpportunity.objects.create(
        opportunity_id=2,
        name="CHC NG P2",
        org_slug="connect-nigeria",
        program_id=10,
        country="NG",
        lifetime_visit_count=500,
        is_active=True,
    )
    PulseOpportunity.objects.create(
        opportunity_id=3,
        name="KMC UG",
        org_slug="living-goods",
        program_id=20,
        country="UG",
        lifetime_visit_count=300,
        is_active=True,
    )

    now = timezone.now()
    for i, (opp, org, prog) in enumerate(
        [(1, "connect-nigeria", 10), (2, "connect-nigeria", 10), (3, "living-goods", 20)], start=1
    ):
        PulseEvent.objects.create(
            connect_visit_id=i,
            opportunity_id=opp,
            program_id=prog,
            org_slug=org,
            field_ts=now - timedelta(hours=i),
            sync_ts=now,
            lat=11.0,
            lon=7.6,
            country="NG" if org == "connect-nigeria" else "UG",
            status="approved",
            service_slug="chc" if org == "connect-nigeria" else "kmc",
        )
        PulseWork.objects.create(
            work_key=f"{i:0>64}",
            opportunity_id=opp,
            program_id=prog,
            org_slug=org,
            status="approved",
            created_ts=now - timedelta(days=i),
            service_slug="chc" if org == "connect-nigeria" else "kmc",
            country="NG" if org == "connect-nigeria" else "UG",
            usd_to_worker="1.00",
            usd_to_org="0.50",
        )
    return None


@pytest.fixture
def viewer(client, django_user_model):
    """A logged-in labs user.

    Most of these tests are about scoping, not entitlement, and partner identity
    now requires a session or a permitting token — so the anonymous test client
    correctly sees no partners at all. Scoping tests therefore log in; the
    fail-closed behaviour has its own class below.
    """
    client.force_login(django_user_model.objects.create(username="viewer"))
    return client


def summary(client, **params):
    return client.get(reverse("pulse:api_summary"), params).json()


@pytest.mark.django_db
class TestOrgScoping:
    def test_org_filter_narrows_every_spine(self, viewer, portfolio):
        data = summary(viewer, org="connect-nigeria")
        assert data["org"]["name"] == "Connect Nigeria"
        assert data["stored"]["events"] == 2
        assert data["money"]["works"] == 2

    def test_a_filtered_header_is_recomputed_not_inherited(self, viewer, portfolio):
        """The stored scalar counts the whole estate. Leaving it above one
        partner's map is a true number answering a question nobody asked."""
        assert summary(viewer)["scope"]["opportunities"] == 501
        scoped = summary(viewer, org="connect-nigeria")["scope"]
        assert scoped["opportunities"] == 2
        assert scoped["lifetime_visits"] == 1500
        assert scoped["orgs"] == 1

    def test_a_partner_running_several_programmes_reports_all_of_them(self, viewer, portfolio):
        """`programs: 1` is only true under a programme filter. A partner filter
        has to count what is actually in scope."""
        PulseOpportunity.objects.create(
            opportunity_id=4,
            name="ECD NG",
            org_slug="connect-nigeria",
            program_id=30,
            country="NG",
            lifetime_visit_count=10,
        )
        assert summary(viewer, org="connect-nigeria")["scope"]["programs"] == 2

    def test_org_and_programme_compose_rather_than_override(self, viewer, portfolio):
        """Selecting both must mean the intersection. If one silently cleared the
        other the two controls would disagree about what is on screen."""
        both = summary(viewer, org="connect-nigeria", program="20")
        assert both["stored"]["events"] == 0
        assert both["org"]["slug"] == "connect-nigeria"
        assert both["program"]["id"] == 20

    def test_an_unknown_org_is_ignored_not_an_error(self, viewer, portfolio):
        """A stale link should degrade to the unfiltered display, not a 500."""
        data = summary(viewer, org="no-such-partner")
        assert data["org"] is None
        assert data["scope"]["opportunities"] == 501


@pytest.mark.django_db
class TestOrgMenu:
    def test_lists_connects_own_partner_names_never_a_slug(self, viewer, portfolio):
        names = [o["name"] for o in summary(viewer)["orgs"]]
        assert "Connect Nigeria" in names
        assert "connect-nigeria" not in names

    def test_carries_the_funder_connect_already_publishes(self, viewer, portfolio):
        row = next(o for o in summary(viewer)["orgs"] if o["slug"] == "connect-nigeria")
        assert row["funder"] == "givewell"

    def test_orders_currently_delivering_partners_first(self, viewer, portfolio):
        PulseOrganization.objects.create(slug="dormant-org", name="Dormant Org")
        PulseOpportunity.objects.create(
            opportunity_id=9,
            name="Old work",
            org_slug="dormant-org",
            program_id=10,
            lifetime_visit_count=999_999,
        )
        slugs = [o["slug"] for o in summary(viewer)["orgs"]]
        # Enormous lifetime volume must not outrank a partner delivering now,
        # or the top of the menu resolves to a blank map.
        assert slugs.index("connect-nigeria") < slugs.index("dormant-org")

    def test_a_partner_with_no_delivery_is_not_offered(self, viewer, portfolio):
        PulseOrganization.objects.create(slug="empty-org", name="Empty Org")
        assert "empty-org" not in [o["slug"] for o in summary(viewer)["orgs"]]

    def test_a_partner_whose_only_work_is_a_test_programme_is_not_offered(self, viewer, portfolio):
        PulseOrganization.objects.create(slug="sandbox-org", name="Sandbox Org")
        PulseProgram.objects.create(
            program_id=99,
            name="[TEST 02] Sandbox",
            delivery_type="chc",
            org_slug="sandbox-org",
            is_test=True,
        )
        PulseOpportunity.objects.create(
            opportunity_id=10,
            name="Sandbox opp",
            org_slug="sandbox-org",
            program_id=99,
            lifetime_visit_count=9035,
        )
        assert "sandbox-org" not in [o["slug"] for o in summary(viewer)["orgs"]]


@pytest.mark.django_db
class TestPartnersConnectWillNotName:
    """Most delivery partners have no name available, and must still work.

    ``opp_org_program_list`` scopes its ``organizations`` list to the orgs the
    poller is a *member* of, while returning every opportunity under a programme
    those orgs *manage* — delivered by other partners entirely. Measured on labs
    prod: 74 partners deliver, 10 are named, and the other 64 carry **92.2% of
    all services**. No export endpoint will give up those names.

    So the slug stands in, flagged, and the drill-down still reaches them.
    Listing only the named ten would have looked complete while omitting almost
    all the delivery.
    """

    @pytest.fixture
    def unnamed_partner(self, portfolio):
        # Delivers real work; has no PulseOrganization row, exactly like the 64.
        PulseOpportunity.objects.create(
            opportunity_id=50,
            name="Big delivery",
            org_slug="janna-health-foundation",
            program_id=10,
            country="NG",
            lifetime_visit_count=65_777,
            is_active=True,
        )
        PulseWork.objects.create(
            work_key="b" * 64,
            opportunity_id=50,
            program_id=10,
            org_slug="janna-health-foundation",
            status="approved",
            created_ts=timezone.now(),
            service_slug="chc",
            country="NG",
            usd_to_worker="4.00",
            usd_to_org="2.00",
        )
        return "janna-health-foundation"

    def test_an_unnamed_partner_is_still_offered(self, viewer, unnamed_partner):
        row = next(o for o in summary(viewer)["orgs"] if o["slug"] == unnamed_partner)
        assert row["visits"] == 65_777

    def test_the_master_list_supplies_the_name_connect_withholds(self, viewer, unnamed_partner):
        """Connect never names this partner, but the master Organizations list
        does — so the display shows the partner, with the Connect workspace it
        came from still carried alongside."""
        row = next(o for o in summary(viewer)["orgs"] if o["slug"] == unnamed_partner)
        assert row["name"] == "janna-health-foundation"  # the Connect workspace
        assert row["partner"] == "Janna Health Foundation"  # the real partner
        assert row["named"] is True

    def test_a_partner_in_neither_source_still_shows_its_slug_flagged(self, viewer, portfolio):
        """The residue has to stay visible rather than vanish: unmatched is a
        reason to show an identifier, not a reason to drop
        the partner."""
        PulseOpportunity.objects.create(
            opportunity_id=52,
            name="Unknown partner work",
            org_slug="ehealth-africa-connect-interviews",
            program_id=10,
            lifetime_visit_count=2_943,
        )
        row = next(o for o in summary(viewer)["orgs"] if o["slug"] == "ehealth-africa-connect-interviews")
        assert row["partner"] == ""
        assert row["named"] is False
        assert row["name"] == "ehealth-africa-connect-interviews"

    def test_the_slug_is_never_prettified_into_a_guess(self, viewer, portfolio):
        """Title-casing reads plausibly and is wrong where it matters: the real
        names behind these slugs are "C-WINS DGw" and "EHA Clinics REACH", which
        mechanical de-slugification renders as "C Wins Dgw" and "Eha Clinics
        Reach". A visible identifier cannot be mistaken for a considered name."""
        PulseOpportunity.objects.create(
            opportunity_id=51,
            name="Acronym partner",
            org_slug="eha-clinics-reach",
            program_id=10,
            lifetime_visit_count=91_071,
        )
        row = next(o for o in summary(viewer)["orgs"] if o["slug"] == "eha-clinics-reach")
        assert row["name"] == "eha-clinics-reach"
        assert "Eha" not in row["name"]

    def test_a_named_partner_is_flagged_as_named(self, viewer, portfolio):
        row = next(o for o in summary(viewer)["orgs"] if o["slug"] == "connect-nigeria")
        assert row["named"] is True
        assert row["name"] == "Connect Nigeria"

    def test_selecting_an_unnamed_partner_actually_filters(self, viewer, unnamed_partner):
        """The bug this guards: resolving the filter only against
        PulseOrganization returned None for 64 of 74 partners, so the filter was
        silently ignored and the whole portfolio stayed on screen under that
        partner's name — a filter that appears to work and does not."""
        data = summary(viewer, org=unnamed_partner)
        assert data["org"] is not None
        assert data["org"]["named"] is False
        assert data["scope"]["opportunities"] == 1
        assert data["scope"]["lifetime_visits"] == 65_777
        assert data["money"]["total_paid"] == pytest.approx(6.0)

    def test_a_slug_that_delivers_nothing_is_still_refused(self, viewer, portfolio):
        """Accepting any string would make the filter a way to probe for slugs."""
        assert summary(viewer, org="not-a-partner-at-all")["org"] is None


@pytest.mark.django_db
class TestPartnerNamesFailClosed:
    """The API is unauthenticated, so the default has to be deny.

    Confirmed against the deployed service: an unauthenticated GET of
    /labs/pulse/api/summary/ returns 200 with the full payload. Anything the
    payload contains is therefore world-readable, which is why entitlement is
    decided server-side and defaults to withholding.
    """

    def test_an_anonymous_caller_gets_no_partner_menu(self, client, portfolio):
        assert summary(client)["orgs"] == [] or True  # anonymous client has no session
        # Explicit: no session, no token.
        assert summary(client)["orgs"] == []

    def test_an_anonymous_caller_cannot_scope_to_a_named_partner(self, client, portfolio):
        """Dropping the token must not be a way in. Withholding the *name* from
        a response shaped like "this named partner's performance" protects
        nothing, so the filter itself is refused."""
        data = summary(client, org="connect-nigeria")
        assert data["org"] is None
        assert data["scope"]["opportunities"] == 501

    def test_a_logged_in_labs_user_may_name_partners(self, client, portfolio, django_user_model):
        user = django_user_model.objects.create(username="viewer")
        client.force_login(user)
        assert [o["name"] for o in summary(client)["orgs"]]
        assert summary(client, org="connect-nigeria")["org"]["name"] == "Connect Nigeria"

    def test_a_token_minted_to_show_partners_may_name_them(self, client, portfolio):
        token = mint_public_token(None, label="funder link", show_partner_names=True)
        assert summary(client, token=token.token)["orgs"]
        assert summary(client, org="connect-nigeria", token=token.token)["org"] is not None

    def test_an_anonymised_token_may_not(self, client, portfolio):
        token = mint_public_token(None, label="anon link", show_partner_names=False)
        data = summary(client, org="connect-nigeria", token=token.token)
        assert data["orgs"] == []
        assert data["org"] is None

    def test_a_revoked_token_may_not(self, client, portfolio):
        token = mint_public_token(None, label="killed", show_partner_names=True)
        token.revoked = True
        token.save()
        assert summary(client, token=token.token)["orgs"] == []

    def test_no_partner_name_appears_anywhere_in_an_anonymous_payload(self, client, portfolio):
        """A field-by-field check misses a name leaking through a breakdown
        label or an opportunity name, so scan the whole body."""
        body = client.get(reverse("pulse:api_summary")).content.decode()
        assert "Connect Nigeria" not in body
        assert "Living Goods" not in body


@pytest.mark.django_db
class TestWeeklySeries:
    def test_comes_from_works_so_it_outlives_event_retention(self, client, portfolio):
        """Events are capped at PULSE_EVENT_RETENTION_DAYS (30), so a series
        drawn from rollups is a one-month window whatever the axis says. Works
        carry full history."""
        old = timezone.now() - timedelta(weeks=12)
        PulseWork.objects.create(
            work_key="a" * 64,
            opportunity_id=1,
            program_id=10,
            org_slug="connect-nigeria",
            status="approved",
            created_ts=old,
            service_slug="chc",
            country="NG",
            usd_to_worker="2.00",
            usd_to_org="1.00",
        )
        weeks = summary(client)["weekly"]
        assert len(weeks) >= 2
        assert min(w["t"] for w in weeks) <= int(old.timestamp()) + 86400 * 7

    def test_carries_both_money_streams_per_week(self, client, portfolio):
        weeks = summary(client)["weekly"]
        assert sum(w["usd"] for w in weeks) == pytest.approx(3.0)
        assert sum(w["usd_org"] for w in weeks) == pytest.approx(1.5)
        assert sum(w["usd_total"] for w in weeks) == pytest.approx(4.5)

    def test_narrows_with_the_org_filter(self, viewer, portfolio):
        weeks = summary(viewer, org="living-goods")["weekly"]
        assert sum(w["works"] for w in weeks) == 1

    def test_the_trailing_partial_week_is_flagged_not_dropped(self, client, portfolio):
        """Dropping it makes a live trend look stopped; leaving it unmarked makes
        every trend look like it fell off a cliff."""
        weeks = summary(client)["weekly"]
        assert weeks[-1]["partial"] is True
        assert any(w["partial"] is False for w in weeks) or len(weeks) == 1


@pytest.mark.django_db
class TestOrgGridNarrowing:
    """Selecting a partner must narrow the accumulated geography too.

    The programme filter shipped without this and a Nigeria-only programme lit
    up Cameroon and DR Congo beside a header reading "COUNTRIES 1". Cells carry
    no org, but orgs own programmes and cells carry programme.
    """

    def _cell(self, lat_q, lon_q, program_id, service="chc"):
        return PulseGridCell.objects.create(
            lat_q=lat_q, lon_q=lon_q, service_slug=service, program_id=program_id, n=10, country="NG"
        )

    def test_cells_narrow_to_the_partners_programmes(self, viewer, portfolio):
        self._cell(1100, 760, 10)
        self._cell(-100, 3000, 20, service="kmc")

        cells = viewer.get(reverse("pulse:api_grid"), {"org": "connect-nigeria"}).json()
        assert len(cells["cells"]) == 1

    def test_an_opportunity_with_no_programme_makes_the_match_inexact(self, viewer, portfolio):
        """Its folded cells carry a null programme and cannot be attributed back
        to a partner, so the response says the geography is partial rather than
        quietly under-drawing it."""
        self._cell(1100, 760, 10)
        assert viewer.get(reverse("pulse:api_grid"), {"org": "connect-nigeria"}).json()["exact"] is True

        PulseOpportunity.objects.create(
            opportunity_id=77,
            name="Unprogrammed",
            org_slug="connect-nigeria",
            program_id=None,
            country="NG",
            lifetime_visit_count=5,
        )
        assert viewer.get(reverse("pulse:api_grid"), {"org": "connect-nigeria"}).json()["exact"] is False
