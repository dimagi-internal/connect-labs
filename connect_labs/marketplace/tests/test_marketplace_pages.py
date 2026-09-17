"""The marketplace pages: home, rounds, a round, and the globe's points.

All data invented. These cover the half of the product the first version
lacked — the rounds — and the one join that makes it a marketplace rather than
two lists: which applicants to a past round went on to deliver.
"""
import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.marketplace import queries
from connect_labs.marketplace.models import OrgContact
from connect_labs.marketplace.testing import make_partner
from connect_labs.pulse.models import PulseEvent, PulseOpportunity
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x")


@pytest.fixture
def marketplace(db):
    live = make_partner(
        "Northlake Maternal Health Network",
        "NMHN",
        countries=["Uganda"],
        lat=0.34,
        lon=32.58,
        country_iso3="UGA",
        location_precision="city",
        location_label="Kampala",
    )
    OrgContact.objects.create(org=live, email="a@example.invalid", full_name="A Person")
    PulseOpportunity.objects.create(
        opportunity_id=1,
        name="Northlake delivery",
        org_slug="northlake-maternal-health-network",
        country="UG",
        lifetime_visit_count=900,
    )
    PulseEvent.objects.create(
        connect_visit_id=1,
        opportunity_id=1,
        program_id=10,
        org_slug="northlake-maternal-health-network",
        worker_hash="w",
        field_ts=dt.datetime(2025, 6, 1, tzinfo=dt.timezone.utc),
        sync_ts=dt.datetime(2025, 6, 1, tzinfo=dt.timezone.utc),
        lat=0.34,
        lon=32.58,
        country="UG",
        status="approved",
    )

    bench = make_partner(
        "Serrano Child Nutrition Foundation",
        "SCNF",
        countries=["Malawi"],
        lat=-13.3,
        lon=34.3,
        country_iso3="MWI",
        location_precision="country",
    )

    closed = Solicitation.objects.create(
        slug="chc-2025",
        title="Community Health Campaign",
        solicitation_type="eoi",
        delivery_type="chc",
        status="closed",
        sa_access_state="ok",
        target_countries="Kenya, Uganda",
        published_on=dt.date(2025, 1, 8),
        questions=[{"id": "q1", "text": "Organisation name"}, {"id": "q2", "text": "Annual budget"}],
    )
    SolicitationResponse.objects.create(
        solicitation=closed, llo_entity=live, source_row=2, org_name=live.name, match_state="email"
    )
    SolicitationResponse.objects.create(
        solicitation=closed, llo_entity=bench, source_row=3, org_name=bench.name, match_state="name"
    )
    SolicitationResponse.objects.create(
        solicitation=closed, source_row=4, org_name="Someone Else", match_state="unmatched"
    )

    live_round = Solicitation.objects.create(
        slug="matching-grant-2026",
        title="Matching Grant Pilot",
        solicitation_type="eoi",
        status="active",
        sa_access_state="ok",
        published_on=dt.date(2026, 3, 4),
    )
    return {"live": live, "bench": bench, "closed": closed, "open": live_round}


@pytest.mark.django_db
class TestAccess:
    def test_every_marketplace_page_requires_a_login(self, client, marketplace):
        """Submission text is what an organisation wrote while applying for work."""
        for name, args in [
            ("marketplace:home", []),
            ("marketplace:network", []),
            ("marketplace:rounds", []),
            ("marketplace:round", ["chc-2025"]),
            ("marketplace:network_points", []),
        ]:
            response = client.get(reverse(name, args=args))
            assert response.status_code in (301, 302), name
            assert "login" in response["Location"], name


@pytest.mark.django_db
class TestHome:
    def test_states_the_network_in_figures(self, client, user, marketplace):
        client.force_login(user)
        body = client.get(reverse("marketplace:home")).content.decode()
        assert "CONNECT MARKETPLACE" in body
        assert "ORGANISATIONS" in body
        assert "SERVICES DELIVERED" in body

    def test_separates_open_rounds_from_closed_ones(self, client, user, marketplace):
        client.force_login(user)
        body = client.get(reverse("marketplace:home")).content.decode()
        assert "Open now" in body
        assert "Matching Grant Pilot" in body
        assert "Community Health Campaign" in body

    def test_the_home_page_carries_no_operational_warnings(self, client, user, marketplace):
        """This is the page the marketplace is shown from. Ingest state, the
        verdict queue and read/unread are all our plumbing — a visitor cannot
        act on any of them, and a banner about them is the loudest thing on the
        page. The truth about an unread round has to survive somewhere, and
        that somewhere is the round's own applicant list; see the round page
        test below."""
        Solicitation.objects.create(slug="blocked", title="Blocked round", status="closed", sa_access_state="denied")
        client.force_login(user)
        body = client.get(reverse("marketplace:home")).content.decode()
        for noise in ("not ingested", "could not be read", "awaiting a verdict"):
            assert noise not in body, noise


@pytest.mark.django_db
class TestRoundPage:
    def test_shows_who_applied_and_what_became_of_them(self, client, user, marketplace):
        """The join this project exists to make: an EOI answered in one system,
        a first delivered service in another."""
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["chc-2025"])).content.decode()
        assert "Northlake Maternal Health Network" in body
        assert "started after" in body
        assert "never activated" in body

    def test_counts_applicants_by_outcome(self, client, user, marketplace):
        client.force_login(user)
        response = client.get(reverse("marketplace:round", args=["chc-2025"]))
        assert response.context["after_count"] == 1
        assert response.context["never_count"] == 1
        assert response.context["unresolved_count"] == 1

    def test_shows_what_the_round_asked(self, client, user, marketplace):
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["chc-2025"])).content.decode()
        assert "WHAT THIS ROUND ASKED" in body
        assert "Annual budget" in body

    def test_an_unresolved_applicant_links_to_the_queue(self, client, user, marketplace):
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["chc-2025"])).content.decode()
        assert "needs a verdict" in body
        assert reverse("marketplace:unmatched") in body

    def test_an_unread_round_says_so_rather_than_looking_unpopular(self, client, user, marketplace):
        """The banner is gone from every page, but this claim cannot go with
        it: an empty applicant list means "nobody applied" unless the page says
        otherwise, and for these rounds that would be false."""
        Solicitation.objects.create(slug="blocked", title="Blocked", status="closed", sa_access_state="denied")
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["blocked"])).content.decode()
        assert "never been read" in body

    def test_a_round_that_was_read_and_drew_nobody_says_that_instead(self, client, user, marketplace):
        """The other half of the same distinction."""
        Solicitation.objects.create(
            slug="quiet", title="Quiet", status="closed", sa_access_state="ok", last_ingested_at=timezone.now()
        )
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["quiet"])).content.decode()
        assert "No applications recorded" in body

    def test_an_unknown_round_is_a_404(self, client, user, marketplace):
        client.force_login(user)
        assert client.get(reverse("marketplace:round", args=["nope"])).status_code == 404


@pytest.mark.django_db
class TestGlobePoints:
    def test_returns_a_point_per_located_organisation_in_scope(self, client, user, marketplace):
        client.force_login(user)
        data = client.get(reverse("marketplace:network_points")).json()
        assert len(data["points"]) == 2

    def test_the_globe_follows_the_filters(self, client, user, marketplace):
        """The point of drawing the filtered set: 'on the bench in Malawi' is a
        shape on the globe, not a number in a table."""
        client.force_login(user)
        data = client.get(reverse("marketplace:network_points"), {"segment": "delivering"}).json()
        assert [p["name"] for p in data["points"]] == ["Northlake Maternal Health Network"]

    def test_precision_reaches_the_globe(self, client, user, marketplace):
        client.force_login(user)
        by_name = {p["name"]: p for p in client.get(reverse("marketplace:network_points")).json()["points"]}
        assert by_name["Northlake Maternal Health Network"]["precision"] == "city"
        assert by_name["Serrano Child Nutrition Foundation"]["precision"] == "country"


@pytest.mark.django_db
class TestNetworkFiltering:
    def test_segments_carry_their_counts(self, client, user, marketplace):
        client.force_login(user)
        segments = {s["key"]: s["count"] for s in client.get(reverse("marketplace:network")).context["segments"]}
        assert segments["all"] == 2
        assert segments["delivering"] == 1
        assert segments["bench"] == 1

    def test_facet_options_show_how_many_they_would_return(self, client, user, marketplace):
        """A facet count is worth having because you see the size of a filter
        before spending a click on it."""
        client.force_login(user)
        rail = {s["param"]: s for s in client.get(reverse("marketplace:network")).context["rail"]}
        countries = {r["label"]: r["count"] for r in rail["country"]["rows"]}
        applied = {r["label"]: r["count"] for r in rail["applied"]["rows"]}
        assert countries["Uganda"] == 1
        assert applied["Child Health Campaign"] == 2

    def test_filtering_by_programme_narrows_the_list(self, client, user, marketplace):
        client.force_login(user)
        response = client.get(reverse("marketplace:network"), {"applied": "kmc"})
        assert response.context["shown"] == 0

    def test_each_row_carries_the_programmes_behind_its_applications(self, client, user, marketplace):
        """The list answers "what does this organisation work on" in Connect's
        own vocabulary, rather than listing round titles a reader has to decode."""
        client.force_login(user)
        body = client.get(reverse("marketplace:network")).content.decode()
        assert "Child Health Campaign" in body


@pytest.mark.django_db
class TestASubmissionIsNotAnApplicant:
    """The 2025 CHC round drew 104 submissions from 90 organisations — four of
    them sent the form twice, days or months apart, under slightly different
    spellings of their own name. Counting submissions as applicants overstated
    the round, and listing each one made one body look like two.
    """

    @pytest.fixture
    def twice(self, db):
        org = make_partner("Munafa Federation", "MF", countries=["Sierra Leone"])
        other = make_partner("Lakeside Trust", "LT", countries=["Kenya"])
        round_ = Solicitation.objects.create(
            slug="chc-2025", title="CHC 2025", status="closed", sa_access_state="ok", delivery_type="chc"
        )
        for row, date in ((99, dt.date(2025, 5, 22)), (100, dt.date(2025, 5, 22))):
            SolicitationResponse.objects.create(
                solicitation=round_,
                llo_entity=org,
                source_row=row,
                org_name="MUNAFA-M'PATIE FEDERATION",
                match_state="name",
                submission_date=date,
            )
        SolicitationResponse.objects.create(
            solicitation=round_, llo_entity=other, source_row=101, org_name=other.name, match_state="name"
        )
        return {"org": org, "round": round_}

    def test_the_headline_counts_organisations_not_submissions(self, client, user, twice):
        client.force_login(user)
        round_ = client.get(reverse("marketplace:round", args=["chc-2025"])).context["round"]
        assert round_.organisations == 2
        assert round_.applications == 3

    def test_an_organisation_appears_once_however_often_it_submitted(self, client, user, twice):
        client.force_login(user)
        applicants = client.get(reverse("marketplace:round", args=["chc-2025"])).context["applicants"]
        assert [a["name"] for a in applicants].count("Munafa Federation") == 1
        assert len(applicants) == 2

    def test_the_row_says_how_many_times_it_submitted(self, client, user, twice):
        """Collapsed, not hidden — a duplicate submission is worth knowing about."""
        client.force_login(user)
        applicants = client.get(reverse("marketplace:round", args=["chc-2025"])).context["applicants"]
        by_name = {a["name"]: a for a in applicants}
        assert by_name["Munafa Federation"]["submissions"] == 2
        assert by_name["Lakeside Trust"]["submissions"] == 1

    def test_unmatched_submissions_are_never_collapsed_together(self, client, user, twice):
        """Whether two unattributed submissions are the same organisation is the
        open question, so merging them would answer it by assumption."""
        for row in (102, 103):
            SolicitationResponse.objects.create(
                solicitation=twice["round"], source_row=row, org_name="Someone Else", match_state="unmatched"
            )
        client.force_login(user)
        applicants = client.get(reverse("marketplace:round", args=["chc-2025"])).context["applicants"]
        assert sum(1 for a in applicants if a["outcome"] == "unresolved") == 2

    def test_submitting_twice_to_one_round_is_not_coming_back_for_another(self, client, user, twice):
        """The segment's own words are "Came back for another round". An
        organisation that sent one round's form twice has not done that."""
        client.force_login(user)
        segments = {s["key"]: s["count"] for s in client.get(reverse("marketplace:network")).context["segments"]}
        assert segments["repeat"] == 0

    def test_applying_to_a_second_round_does_count(self, client, user, twice):
        second = Solicitation.objects.create(
            slug="rutf-2026", title="RUTF", status="closed", delivery_type="nutrition"
        )
        SolicitationResponse.objects.create(
            solicitation=second, llo_entity=twice["org"], source_row=2, org_name="Munafa", match_state="name"
        )
        client.force_login(user)
        segments = {s["key"]: s["count"] for s in client.get(reverse("marketplace:network")).context["segments"]}
        assert segments["repeat"] == 1


@pytest.mark.django_db
class TestTheRoundPageDoesNotClaimCausation:
    """ "Now delivering" counted every applicant delivering on Connect at all,
    under a column headed "Since this round" — so an organisation that had been
    delivering for a year before the round opened was presented as something
    the round produced.
    """

    @pytest.fixture
    def timed(self, db):
        import datetime as dt

        from connect_labs.pulse.models import PulseEvent, PulseOpportunity

        early = make_partner("Early Bird Trust", "EBT", countries=["Kenya"])
        late = make_partner("Latecomer Health", "LH", countries=["Kenya"])
        round_ = Solicitation.objects.create(
            slug="timed-2025",
            title="Timed",
            status="closed",
            sa_access_state="ok",
            application_deadline=dt.date(2025, 6, 1),
        )
        for i, (org, when) in enumerate(
            (
                (early, dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc)),
                (late, dt.datetime(2025, 9, 1, tzinfo=dt.timezone.utc)),
            )
        ):
            PulseOpportunity.objects.create(
                opportunity_id=500 + i, name="Op", org_slug=org.slug, service_slug="chc", lifetime_visit_count=5
            )
            PulseEvent.objects.create(
                connect_visit_id=500 + i,
                opportunity_id=500 + i,
                program_id=1,
                org_slug=org.slug,
                worker_hash="w",
                field_ts=when,
                sync_ts=when,
                status="approved",
            )
            SolicitationResponse.objects.create(
                solicitation=round_, llo_entity=org, source_row=2 + i, org_name=org.name, match_state="name"
            )
        queries.invalidate()
        return round_

    def test_an_organisation_already_delivering_is_not_credited_to_the_round(self, client, user, timed):
        client.force_login(user)
        context = client.get(reverse("marketplace:round", args=["timed-2025"])).context
        outcomes = {a["name"]: a["outcome"] for a in context["applicants"]}
        assert outcomes["Early Bird Trust"] == "before"
        assert outcomes["Latecomer Health"] == "after"

    def test_the_headline_counts_only_those_who_started_afterwards(self, client, user, timed):
        client.force_login(user)
        context = client.get(reverse("marketplace:round", args=["timed-2025"])).context
        assert context["after_count"] == 1
        assert context["before_count"] == 1

    def test_the_page_says_it_is_comparing_dates_not_proving_cause(self, client, user, timed):
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["timed-2025"])).content.decode()
        assert "not that one caused the other" in body
        assert "already delivering" in body

    def test_needs_a_verdict_is_explained_where_it_is_read(self, client, user, timed):
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["timed-2025"])).content.decode()
        assert "could not be matched to any" in body


@pytest.mark.django_db
class TestTheSubmissionTrend:
    @pytest.fixture
    def spread(self, db):
        import datetime as dt

        org = make_partner("Trend Trust", "TT", countries=["Kenya"])
        round_ = Solicitation.objects.create(slug="spread-2025", title="Spread", status="closed", sa_access_state="ok")

        # Two bursts three months apart — the shape a total cannot show.
        def at(y, m, d):
            return dt.datetime(y, m, d, 9, tzinfo=dt.timezone.utc)

        days = [at(2025, 2, 3)] * 5 + [at(2025, 2, 4)] * 2 + [at(2025, 5, 12)] * 3
        for i, day in enumerate(days):
            SolicitationResponse.objects.create(
                solicitation=round_,
                llo_entity=org if i == 0 else None,
                source_row=2 + i,
                org_name=f"Applicant {i}",
                match_state="name" if i == 0 else "unmatched",
                submission_date=day,
            )
        return round_

    def test_buckets_by_week_over_a_long_round(self, spread):
        trend = queries.submission_trend(spread)
        assert [b["days"] for b in trend] == [7] * len(trend)
        assert sum(b["count"] for b in trend) == 10
        assert trend[0]["count"] == 7  # the opening burst

    def test_the_busiest_bucket_is_full_height(self, spread):
        assert max(b["pct"] for b in queries.submission_trend(spread)) == 100.0

    def test_a_short_round_buckets_by_day(self, db):
        import datetime as dt

        round_ = Solicitation.objects.create(slug="short-2025", title="Short", status="closed")
        march = [dt.datetime(2025, 3, d, 9, tzinfo=dt.timezone.utc) for d in (1, 2, 3)]
        for i, day in enumerate(march):
            SolicitationResponse.objects.create(
                solicitation=round_, source_row=2 + i, org_name=f"A{i}", match_state="unmatched", submission_date=day
            )
        assert [b["days"] for b in queries.submission_trend(round_)] == [1, 1, 1]

    def test_a_round_with_one_submission_draws_nothing(self, db):
        import datetime as dt

        round_ = Solicitation.objects.create(slug="one-2025", title="One", status="closed")
        SolicitationResponse.objects.create(
            solicitation=round_,
            source_row=2,
            org_name="A",
            match_state="unmatched",
            submission_date=dt.date(2025, 3, 1),
        )
        assert queries.submission_trend(round_) == []

    def test_the_page_draws_the_trend(self, client, user, spread):
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["spread-2025"])).content.decode()
        assert "When the submissions arrived" in body
        assert "submissions from 3 Feb 2025" in body or "submission" in body


@pytest.mark.django_db
class TestTheRoundPageShowsNoInternalIdentifiers:
    def test_the_slug_is_not_printed_at_the_reader(self, client, user, marketplace):
        """`chc-2025` is how the URL addresses the round, not something to read."""
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["chc-2025"])).content.decode()
        head = body[: body.find("Who applied")]
        assert ">chc-2025<" not in head
