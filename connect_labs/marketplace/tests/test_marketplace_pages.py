"""The marketplace pages: home, rounds, a round, and the globe's points.

All data invented. These cover the half of the product the first version
lacked — the rounds — and the one join that makes it a marketplace rather than
two lists: which applicants to a past round went on to deliver.
"""
import datetime as dt

import pytest
from django.urls import reverse

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

    def test_a_round_that_was_never_read_is_marked_not_ingested(self, client, user, marketplace):
        """'Nobody applied' and 'we could not open the sheet' must not look alike."""
        Solicitation.objects.create(slug="blocked", title="Blocked round", status="closed", sa_access_state="denied")
        client.force_login(user)
        body = client.get(reverse("marketplace:home")).content.decode()
        assert "not ingested" in body
        assert "could not be read" in body


@pytest.mark.django_db
class TestRoundPage:
    def test_shows_who_applied_and_what_became_of_them(self, client, user, marketplace):
        """The join this project exists to make: an EOI answered in one system,
        a first delivered service in another."""
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["chc-2025"])).content.decode()
        assert "Northlake Maternal Health Network" in body
        assert "delivering" in body
        assert "never activated" in body

    def test_counts_applicants_by_outcome(self, client, user, marketplace):
        client.force_login(user)
        response = client.get(reverse("marketplace:round", args=["chc-2025"]))
        assert response.context["delivering_count"] == 1
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
        Solicitation.objects.create(slug="blocked", title="Blocked", status="closed", sa_access_state="denied")
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["blocked"])).content.decode()
        assert "has not been ingested" in body

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
