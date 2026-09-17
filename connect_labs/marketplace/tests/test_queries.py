"""What the marketplace screens ask for. All data invented."""
import datetime as dt

import pytest

from connect_labs.marketplace import queries
from connect_labs.marketplace.models import OrgContact
from connect_labs.marketplace.testing import make_partner
from connect_labs.pulse.models import PulseEvent, PulseOpportunity
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


@pytest.fixture
def network(db):
    """Two organisations: one delivering with a contact, one on the bench."""
    live = make_partner(
        "Northlake Maternal Health Network",
        "NMHN",
        countries=["Uganda"],
        flws_managed=120,
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
        service_slug="kmc",
        lifetime_visit_count=900,
    )
    PulseEvent.objects.create(
        connect_visit_id=1,
        opportunity_id=1,
        program_id=10,
        org_slug="northlake-maternal-health-network",
        worker_hash="w1",
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
        flws_managed=15,
        lat=-13.3,
        lon=34.3,
        country_iso3="MWI",
        location_precision="country",
    )

    round_ = Solicitation.objects.create(
        slug="chc-2025", title="CHC", status="closed", sa_access_state="ok", delivery_type="chc"
    )
    SolicitationResponse.objects.create(
        solicitation=round_, llo_entity=live, source_row=2, org_name=live.name, match_state="email"
    )
    SolicitationResponse.objects.create(
        solicitation=round_, llo_entity=bench, source_row=3, org_name=bench.name, match_state="name"
    )
    SolicitationResponse.objects.create(
        solicitation=round_, source_row=4, org_name="Someone Else", match_state="unmatched"
    )
    return {"live": live, "bench": bench, "round": round_}


@pytest.mark.django_db
class TestSegments:
    def test_bench_and_delivering_partition_the_network(self, network):
        rows = list(queries.all_rows_with_rounds())
        delivering = queries.delivering_names()
        counts = queries.segment_counts(rows, delivering)
        assert counts["delivering"] + counts["bench"] == counts["all"] == 2

    def test_no_contact_counts_only_the_unreachable(self, network):
        counts = queries.segment_counts(queries.all_rows_with_rounds(), queries.delivering_names())
        assert counts["nocontact"] == 1

    def test_a_count_never_promises_more_than_the_list_shows(self, network):
        """Counted over the same rows the list renders, so the two cannot drift."""
        rows = list(queries.all_rows_with_rounds())
        delivering = queries.delivering_names()
        counts = queries.segment_counts(rows, delivering)
        for key, _, _ in queries.SEGMENTS:
            shown = [r for r in rows if queries.in_segment(r, key, delivering)]
            assert len(shown) == counts[key], key


@pytest.mark.django_db
class TestFacets:
    def test_counts_every_country_in_scope(self, network):
        facets = queries.facet_counts(queries.all_rows_with_rounds(), queries.delivering_names())
        assert {f["value"] for f in facets["countries"]} == {"Uganda", "Malawi"}

    def test_delivered_and_applied_are_counted_separately(self, network):
        """The two programme facets answer different questions. One organisation
        has delivered KMC; both applied to a CHC round; nobody has delivered CHC.
        Collapsing them would claim two CHC deliverers that do not exist.
        """
        facets = queries.facet_counts(queries.all_rows_with_rounds(), queries.delivering_names())
        assert facets["delivered"] == [{"value": "kmc", "label": "Kangaroo Mother Care", "count": 1}]
        assert facets["applied"] == [{"value": "chc", "label": "Child Health Campaign", "count": 2}]

    def test_an_untagged_round_contributes_no_programme(self, network):
        """A round nobody has tagged yet must not become a blank facet row."""
        Solicitation.objects.filter(slug="chc-2025").update(delivery_type="")
        facets = queries.facet_counts(queries.all_rows_with_rounds(), queries.delivering_names())
        assert facets["applied"] == []

    def test_counts_organisations_per_round_not_applications(self, network):
        """Three applications, two organisations — the facet answers the second."""
        facets = queries.facet_counts(queries.all_rows_with_rounds(), queries.delivering_names())
        assert facets["rounds"][0]["count"] == 2

    def test_facets_narrow_with_the_rows_given(self, network):
        rows = [r for r in queries.all_rows_with_rounds() if r.name.startswith("Serrano")]
        facets = queries.facet_counts(rows, queries.delivering_names())
        assert {f["value"] for f in facets["countries"]} == {"Malawi"}


@pytest.mark.django_db
class TestRounds:
    def test_open_and_closed_are_separated(self, network):
        Solicitation.objects.create(slug="live-round", title="Live", status="active")
        assert [r.slug for r in queries.open_rounds()] == ["live-round"]
        assert [r.slug for r in queries.closed_rounds()] == ["chc-2025"]

    def test_a_round_counts_applications_and_distinct_organisations(self, network):
        got = queries.rounds_with_counts().get(slug="chc-2025")
        assert got.applications == 3
        assert got.organisations == 2
        assert got.unresolved == 1

    def test_applicants_carry_what_became_of_them(self, network):
        delivering = queries.delivering_names()
        outcomes = {a["name"]: a["outcome"] for a in queries.round_applicants(network["round"], delivering)}
        assert outcomes["Northlake Maternal Health Network"] == "delivering"
        assert outcomes["Serrano Child Nutrition Foundation"] == "never"
        assert outcomes["Someone Else"] == "unresolved"

    def test_an_unreadable_round_is_listed_apart(self, network):
        Solicitation.objects.create(slug="blocked", title="Blocked", sa_access_state="denied")
        assert [r.slug for r in queries.unreadable_rounds()] == ["blocked"]


@pytest.mark.django_db
class TestMapPoints:
    def test_every_located_organisation_becomes_a_point(self, network):
        points = queries.map_points(queries.all_rows_with_rounds(), queries.delivering_names())
        assert len(points) == 2

    def test_precision_travels_with_the_point(self, network):
        """A town matched in an address is a pin; a country is a whole country.
        A map that hides the difference draws a rooftop from a country name."""
        by_name = {
            p["name"]: p for p in queries.map_points(queries.all_rows_with_rounds(), queries.delivering_names())
        }
        assert by_name["Northlake Maternal Health Network"]["precision"] == "city"
        assert by_name["Serrano Child Nutrition Foundation"]["precision"] == "country"

    def test_an_unlocated_organisation_is_omitted_not_placed_at_zero(self, network):
        nowhere = make_partner("Unplaced Trust", "UT")
        rows = [r for r in queries.all_rows_with_rounds() if r.pk == nowhere.pk]
        assert queries.map_points(rows, set()) == []

    def test_points_carry_the_delivering_flag_the_globe_colours_by(self, network):
        by_name = {
            p["name"]: p for p in queries.map_points(queries.all_rows_with_rounds(), queries.delivering_names())
        }
        assert by_name["Northlake Maternal Health Network"]["delivering"] is True
        assert by_name["Serrano Child Nutrition Foundation"]["delivering"] is False


@pytest.mark.django_db
class TestTotals:
    def test_the_hero_figures_come_from_the_same_places_the_rest_of_labs_uses(self, network):
        totals = queries.network_totals()
        assert totals["organisations"] == 2
        assert totals["countries"] == 2
        assert totals["services"] == 900
        assert totals["rounds"] == 1
        assert totals["applications"] == 3
        assert totals["delivering"] == 1
