"""What the network page costs to render.

Written because it was measured at 4.7s on production with 240 organisations,
and every filter click pays it again. The causes were structural rather than a
single slow query, so they are pinned structurally: a query-count assertion
catches a reintroduced N+1, which a timing assertion never would without being
flaky.

All data invented.
"""
import datetime as dt

import pytest
from django.urls import reverse

from connect_labs.marketplace import queries
from connect_labs.marketplace.testing import make_partner
from connect_labs.pulse.models import PulseEvent, PulseOpportunity
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x")


def _org(i, round_):
    org = make_partner(
        f"Organisation {i:03d}",
        f"O{i:03d}",
        countries=["Nigeria" if i % 2 else "Kenya"],
        lat=9.0 + i / 100,
        lon=7.0 + i / 100,
        country_iso3="NGA",
        location_precision="city",
    )
    SolicitationResponse.objects.create(
        solicitation=round_,
        llo_entity=org,
        source_row=i + 1,
        org_name=org.name,
        match_state="name",
    )
    return org


@pytest.fixture
def many(db):
    """Enough organisations that a per-row query is visible in the count."""
    round_ = Solicitation.objects.create(
        slug="chc-2025", title="CHC", status="closed", sa_access_state="ok", delivery_type="chc"
    )
    other = Solicitation.objects.create(
        slug="rutf", title="RUTF", status="closed", sa_access_state="ok", delivery_type="nutrition"
    )
    orgs = [_org(i, round_) for i in range(25)]
    for i, org in enumerate(orgs[:10]):
        SolicitationResponse.objects.create(
            solicitation=other, llo_entity=org, source_row=100 + i, org_name=org.name, match_state="name"
        )
    PulseOpportunity.objects.create(
        opportunity_id=1, name="Live", org_slug="organisation-000", country="NG", lifetime_visit_count=10
    )
    PulseEvent.objects.create(
        connect_visit_id=1,
        opportunity_id=1,
        program_id=1,
        org_slug="organisation-000",
        worker_hash="w",
        field_ts=dt.datetime(2025, 6, 1, tzinfo=dt.timezone.utc),
        sync_ts=dt.datetime(2025, 6, 1, tzinfo=dt.timezone.utc),
        lat=9.0,
        lon=7.0,
        country="NG",
        status="approved",
    )
    return orgs


@pytest.mark.django_db
class TestTheRowLoopDoesNotQueryPerRow:
    def test_rendering_more_organisations_does_not_cost_more_queries(
        self, client, user, many, django_assert_num_queries
    ):
        """The regression that mattered: rounds were fetched per organisation,
        so 240 rows meant 240 round-trips. Query count must not track row count.
        """
        client.force_login(user)
        # Measure the page at two different row counts.
        with_25 = _count_queries(client)
        for i in range(25, 45):
            _org(i, Solicitation.objects.get(slug="chc-2025"))
        queries.invalidate()
        with_45 = _count_queries(client)

        assert with_45 <= with_25 + 2, (
            f"query count grew with row count ({with_25} -> {with_45}); "
            "something in the row loop is querying per organisation"
        )

    def test_every_listed_organisation_still_carries_its_programmes(self, client, user, many):
        """Batching must not cost the feature it was batching: the programme
        chips come off the same prefetch the query count depends on."""
        client.force_login(user)
        listed = client.get(reverse("marketplace:network")).context["listed"]
        first = next(row for row in listed if row["org"].name == "Organisation 000")
        assert {p["label"] for p in first["applied"]} == {"Child Health Campaign", "Nutrition"}

    def test_a_programme_appears_once_however_many_rounds_carried_it(self, client, user, many):
        """Three KMC rounds are one KMC chip. Repeating the tag per round would
        make a prolific applicant look like it works on more than it does."""
        org = make_partner("Busy Organisation", "BO", countries=["Kenya"])
        for i in range(3):
            round_ = Solicitation.objects.create(
                slug=f"kmc-{i}", title=f"KMC {i}", status="closed", delivery_type="kmc"
            )
            SolicitationResponse.objects.create(
                solicitation=round_, llo_entity=org, source_row=1, org_name=org.name, match_state="name"
            )
        client.force_login(user)
        listed = client.get(reverse("marketplace:network")).context["listed"]
        busy = next(row for row in listed if row["org"].name == "Busy Organisation")
        assert [p["label"] for p in busy["applied"]] == ["Kangaroo Mother Care"]


def _count_queries(client, params=None):
    """How many queries one network render costs.

    Count BEFORE resetting: `CaptureQueriesContext.__len__` reads back out of
    `connection.queries`, so clearing that list first makes every measurement
    zero and every comparison between two of them vacuously true.
    """
    from django.db import connection, reset_queries
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as ctx:
        client.get(reverse("marketplace:network"), params or {})
    count = len(ctx)
    reset_queries()
    return count


@pytest.mark.django_db
class TestTheSpineIsReadOnce:
    def test_delivery_is_not_recomputed_for_every_facet_dimension(self, many):
        """Four calls to delivering_names() per render meant four aggregations
        over PulseEvent, which is millions of rows in production."""
        queries.invalidate()
        calls = []

        from connect_labs.pulse import network_api

        original = network_api.first_service_by_partner
        network_api.first_service_by_partner = lambda: (calls.append(1) or original())
        try:
            for _ in range(4):
                queries.delivering_names()
        finally:
            network_api.first_service_by_partner = original

        assert len(calls) == 1, f"the pulse spine was aggregated {len(calls)} times for one render"

    def test_invalidate_lets_a_new_partner_be_seen(self, many):
        """A cache nothing can clear is a bug generator: the importer clears it,
        and so does make_partner."""
        queries.delivering_names()
        queries.invalidate()
        assert queries.delivering_names() is not None


@pytest.mark.django_db
class TestTheRegistryIsFetchedOnce:
    """The rail asks the same population four questions — the list plus one per
    facet dimension, each with its own filter dropped. Asking the database each
    time was four scans of the registry to draw one page.
    """

    def test_one_render_reads_the_organisations_table_once(self, client, user, many):
        client.force_login(user)
        queries.invalidate()

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            client.get(reverse("marketplace:network"))

        # The page's own population — the annotated fetch that carries each
        # organisation's profile. (`partner_names` reads the same table for a
        # different question, name resolution, and has its own cache.)
        scans = [
            q
            for q in ctx.captured_queries
            if 'FROM "labs_labsorg"' in q["sql"] and "marketplace_orgprofile" in q["sql"]
        ]
        assert len(scans) == 1, f"the registry was scanned {len(scans)} times for one render"

    def test_the_filtered_page_costs_no_more_than_the_unfiltered_one(self, client, user, many):
        """Filtering in memory means a filter click is the same page, not a
        second set of queries — which is what made clicking one feel slow."""
        client.force_login(user)
        _count_queries(client)  # warm the caches, as a second click would find them
        plain = _count_queries(client)
        filtered = _count_queries(client, {"country": "Nigeria", "applied": "chc"})

        assert plain > 0, "measured nothing"
        assert filtered <= plain, f"filtering cost {filtered} queries against {plain} unfiltered"
