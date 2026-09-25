"""Every supply point carries a latitude and longitude, and says how it got one.

The rule under test (stock/services/placement.py): a point with no coordinates
of its own is placed at its managing organisation's office -- Pulse's
`OrgProfile` coordinates -- then at its parent, then at its country's centre.
A recorded coordinate always wins and is never overwritten, and a stand-in
echoed back unchanged is not promoted to one.

Organisation names and coordinates here are placeholders (public repo).
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgProfile
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services.placement import place_all

pytestmark = pytest.mark.django_db

PROGRAMME = 10971
HQ = (11.5, 8.25)


def _access():
    return SupplyDataAccess(access_token="placeholder", program_id=PROGRAMME, caller=SYSTEM)


def _org(slug="a-partner", name="A Placeholder Partner", country="", hq=HQ, precision="city"):
    org = LabsOrg.objects.create(slug=slug, name=name, country=country)
    if hq is not None:
        OrgProfile.objects.create(
            org=org, lat=hq[0], lon=hq[1], location_precision=precision, location_label="Placeholder Town"
        )
    return org


def _upsert(**data):
    payload = {"slug": "a-store", "name": "A Store", "kind": "central_store", "source": "we_recorded", **data}
    return call_operation("supply_point_upsert", _access(), {"data": payload})


def test_a_point_with_no_location_stands_in_at_its_organisations_head_office():
    """MUTATED: `place` removed from the upsert -- latitude came back None."""
    point = _upsert(managed_by_org_id=_org().pk)

    assert (point["latitude"], point["longitude"]) == HQ
    assert point["location_source"] == "org_hq"
    assert point["location_precision"] == "city"
    assert point["location_label"] == "A Placeholder Partner office (Placeholder Town)"


def test_a_duplicate_organisation_row_finds_the_directory_head_office_by_exact_name():
    """The OES demo seeded partners beside their directory rows; matching is exact-only.

    MUTATED: the exact-name fallback removed from `_profile_of` -- red.
    """
    _org(slug="the-directory-row", name="A Placeholder Partner")
    duplicate = LabsOrg.objects.create(slug="a-seeded-row", name="a placeholder partner ")
    near_miss = LabsOrg.objects.create(slug="another-row", name="A Placeholder Partners Ltd")

    assert (_upsert(managed_by_org_id=duplicate.pk)["latitude"]) == HQ[0]
    assert _upsert(slug="another-store", managed_by_org_id=near_miss.pk)["latitude"] is None


def test_a_recorded_location_wins_and_is_never_overwritten():
    """MUTATED: `place` refreshing regardless of source -- the store moved to the HQ."""
    org = _org()
    point = _upsert(managed_by_org_id=org.pk, latitude=9.1, longitude=7.4)
    assert (point["latitude"], point["longitude"], point["location_source"]) == (9.1, 7.4, "recorded")

    # A later write that says nothing about location, and a head office that moved.
    OrgProfile.objects.filter(org=org).update(lat=1.0, lon=1.0)
    again = _upsert(managed_by_org_id=org.pk, name="A Renamed Store")

    assert (again["latitude"], again["longitude"], again["location_source"]) == (9.1, 7.4, "recorded")


def test_a_stand_in_echoed_back_by_an_edit_form_is_not_promoted_to_recorded():
    """MUTATED: `_same` check removed -- the stand-in became `recorded`."""
    first = _upsert(managed_by_org_id=_org().pk)
    again = _upsert(
        managed_by_org_id=first["managed_by_org_id"], latitude=first["latitude"], longitude=first["longitude"]
    )

    assert again["location_source"] == "org_hq"


def test_a_stand_in_follows_its_organisations_head_office():
    org = _org()
    _upsert(managed_by_org_id=org.pk)
    OrgProfile.objects.filter(org=org).update(lat=2.0, lon=3.0)

    again = _upsert(managed_by_org_id=org.pk)

    assert (again["latitude"], again["longitude"]) == (2.0, 3.0)


def test_a_field_worker_with_no_organisation_stands_in_at_its_store():
    store = _upsert(managed_by_org_id=_org().pk)
    worker = _upsert(
        slug="a-worker",
        name="A Worker",
        kind="user_held",
        opportunity_id=PROGRAMME,
        connect_username="a-worker",
        parent_supply_point_id=store["id"],
    )

    assert (worker["latitude"], worker["longitude"]) == HQ
    assert worker["location_source"] == "parent"
    assert worker["location_precision"] == "city"


def test_with_no_head_office_the_countrys_centre_is_the_last_resort():
    org = _org(country="IN", hq=None)

    point = _upsert(managed_by_org_id=org.pk)

    assert point["location_source"] == "country"
    assert point["location_precision"] == "country"
    assert point["latitude"] is not None


def test_with_nothing_to_go_on_a_point_stays_unplaced_rather_than_invented():
    point = _upsert()
    assert point["latitude"] is None and point["location_source"] == ""


def test_the_backfill_keeps_existing_coordinates_as_recorded_and_places_children_after_parents():
    org = _org()
    store = SupplyPoint.objects.create(
        program_id=PROGRAMME,
        slug="a-store",
        name="A Store",
        kind="central_store",
        managed_by_org=org,
        source="we_recorded",
    )
    SupplyPoint.objects.create(
        program_id=PROGRAMME,
        slug="a-worker",
        name="A Worker",
        kind="user_held",
        connect_username="a-worker",
        parent=store,
        source="we_recorded",
    )
    SupplyPoint.objects.create(
        program_id=PROGRAMME,
        slug="a-surveyed-store",
        name="A Surveyed Store",
        kind="facility",
        managed_by_org=org,
        latitude=9.9,
        longitude=8.8,
        source="we_recorded",
    )

    tally = place_all(PROGRAMME)

    got = {
        p.slug: (p.latitude, p.longitude, p.location_source) for p in SupplyPoint.objects.filter(program_id=PROGRAMME)
    }
    assert got["a-store"] == (*HQ, "org_hq")
    assert got["a-worker"] == (*HQ, "parent")
    assert got["a-surveyed-store"] == (9.9, 8.8, "recorded")
    assert tally == {"changed": 3, "unplaced": 0, "total": 3}
    assert place_all(PROGRAMME)["changed"] == 0
