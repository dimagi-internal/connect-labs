"""The registry answers the question it exists for.

"Who can supply RUTF to north-east Nigeria today" needs three facts the app
already collects at EOI — regions served, capacity and lead time — and used to
discard. Every assertion here is that question, asked of the registry.
"""
from datetime import date, timedelta

import pytest

from connect_labs.supply.models import EOISubmission, Qualification
from connect_labs.supply.services import eoi_actions

from . import factories as f

pytestmark = pytest.mark.django_db


def _qualified(org, category="rutf", *, commitments=None, country=None):
    if country:
        org.country = country
        org.save(update_fields=["country"])
    sub = f.EOISubmissionFactory(
        org=org,
        categories=[category],
        status=EOISubmission.Status.QUALIFIED,
        commitments=commitments or {},
    )
    return Qualification.objects.create(
        org=org,
        category=category,
        source_submission=sub,
        granted_at=date.today(),
        expires_at=date.today() + timedelta(days=400),
        status=Qualification.Status.ACTIVE,
    )


def test_the_commitments_captured_at_eoi_reach_the_registry():
    from connect_labs.supply.serializers import qualification_dict

    org = f.SupplierOrgFactory(legal_name="Savanna", country="NG")
    qual = _qualified(
        org,
        commitments={"rutf": {"capacity": "20,000 cartons per month", "regions": ["NG", "BF"], "lead_time_days": 21}},
    )

    row = qualification_dict(qual)
    assert row["capacity"] == "20,000 cartons per month"
    assert row["regions_served"] == ["NG", "BF"]
    assert row["lead_time_days"] == 21


def test_regions_survive_the_web_form_s_comma_string():
    """The API contract says a list; the web form posts "NG, BF"."""
    org = f.SupplierOrgFactory(country="NG")
    qual = _qualified(org, commitments={"rutf": {"regions": "NG, BF"}})
    assert eoi_actions.served_regions(qual) == ["NG", "BF"]


def test_a_supplier_is_found_by_where_it_can_DELIVER_not_where_it_is_registered():
    """The filter answered a different question from the one the screen asks.

    A Kano plant that committed to serving Burkina Faso vanished from a Burkina
    search because its head office is in Nigeria — which is exactly the supplier
    a Burkina search exists to find.
    """
    kano = f.SupplierOrgFactory(legal_name="Savanna Nutrients Ltd", country="NG")
    _qualified(kano, commitments={"rutf": {"regions": ["NG", "BF"]}})

    found = eoi_actions.live_qualifications(category="rutf", country="BF")
    assert [q.org.legal_name for q in found] == ["Savanna Nutrients Ltd"]


def test_a_supplier_that_named_no_regions_still_answers_for_its_own_country():
    """Silence is not an exclusion.

    A supplier who committed to no regions has said nothing about reach, and
    dropping them from their OWN country's search would lose a real supplier to
    a blank field.
    """
    org = f.SupplierOrgFactory(legal_name="Quiet Foods", country="ET")
    _qualified(org, commitments={})

    assert [q.org.legal_name for q in eoi_actions.live_qualifications(country="ET")] == ["Quiet Foods"]
    assert eoi_actions.live_qualifications(country="NG") == []


def test_a_supplier_serving_elsewhere_is_absent_from_its_own_country_when_it_said_so():
    """A stated region list is a claim, and the filter honours it in both directions."""
    org = f.SupplierOrgFactory(legal_name="Export Only Ltd", country="ET")
    _qualified(org, commitments={"rutf": {"regions": ["SD"]}})

    assert [q.org.legal_name for q in eoi_actions.live_qualifications(country="SD")] == ["Export Only Ltd"]
    assert eoi_actions.live_qualifications(country="ET") == []


def test_the_seeded_world_puts_suppliers_where_they_actually_are():
    """A supplier serves its own country, whatever else it reaches.

    Every seeded commitment was written with a hardcoded lookup, so all fourteen
    suppliers claimed Nigeria and Sudan whatever continent they were on — an
    Ethiopian plant served neither Ethiopia nor anywhere near it. The field was
    collected and never displayed, so nothing caught it until the registry began
    surfacing regions and a Burkina Faso search came back empty with a Burkinabé
    plant sitting in the registry.
    """
    from django.core.management import call_command

    from connect_labs.supply.models import Qualification

    call_command("seed_supply_demo")

    checked = 0
    for qual in Qualification.objects.select_related("org", "source_submission"):
        regions = eoi_actions.served_regions(qual)
        if not regions:
            continue
        checked += 1
        assert (
            qual.org.country in regions
        ), f"{qual.org.legal_name} ({qual.org.country}) serves {regions} — not its own country"
    assert checked >= 10, "the seeded world must carry commitments to check"


def test_every_seeded_country_is_reachable_through_the_registry():
    """A country with a plant in it must not return an empty search."""
    from django.core.management import call_command

    from connect_labs.supply.models import SupplierOrg

    call_command("seed_supply_demo")
    countries = {o.country for o in SupplierOrg.objects.all() if o.country}
    for country in sorted(countries):
        found = eoi_actions.live_qualifications(country=country)
        assert found, f"no supplier answers a {country} search, yet {country} has organisations in it"
