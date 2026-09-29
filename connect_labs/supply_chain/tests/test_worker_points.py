"""A worker's own supply point: found by username, then by Connect user id,
and made the first time the worker submits a visit.

THIS REPOSITORY IS PUBLIC. Every username and id here is invented.
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services.workers import WorkerIndex, worker_slug

pytestmark = pytest.mark.django_db

PROGRAM = 10514
OPP = 10514
UUID_A = "6c1f1d0e-0000-4000-8000-00000000000a"
UUID_B = "6c1f1d0e-0000-4000-8000-00000000000b"


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded"
    )


def _worker(username, uuid=""):
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        slug=worker_slug(OPP, username),
        name=username,
        kind="user_held",
        connect_username=username,
        connect_user_uuid=uuid,
        source="connect_visit",
    )


def test_loading_the_index_is_one_query(da, store, django_assert_num_queries):
    _worker("worker-acacia")
    _worker("worker-baobab")
    with django_assert_num_queries(1):
        WorkerIndex(da, OPP)


def test_a_new_worker_gets_a_point_under_the_resupply_store(da, store):
    index = WorkerIndex(da, OPP)
    point = index.ensure("worker-acacia", UUID_A, parent=store)

    assert (point.kind, point.parent_id, point.opportunity_id, point.source) == (
        "user_held",
        store.pk,
        OPP,
        "connect_visit",
    )
    assert (point.slug, point.connect_username, point.connect_user_uuid) == (
        "user-10514-worker-acacia",
        "worker-acacia",
        UUID_A,
    )
    assert index.created == [point.pk]


def test_the_same_worker_is_found_again_without_a_second_point(da, store):
    index = WorkerIndex(da, OPP)
    first = index.ensure("worker-acacia", UUID_A, parent=store)
    again = WorkerIndex(da, OPP).ensure("worker-acacia", UUID_A, parent=store)
    assert again.pk == first.pk
    assert SupplyPoint.objects.filter(kind="user_held").count() == 1


def test_a_renamed_worker_is_found_by_uuid(da, store):
    known = _worker("worker-acacia", UUID_A)
    found = WorkerIndex(da, OPP).ensure("worker-acacia-2", UUID_A, parent=store)
    assert found.pk == known.pk
    assert SupplyPoint.objects.filter(kind="user_held").count() == 1


def test_a_matching_username_wins_over_a_uuid_that_names_someone_else(da, store):
    acacia = _worker("worker-acacia", UUID_A)
    _worker("worker-baobab", UUID_B)
    assert WorkerIndex(da, OPP).find("worker-acacia", UUID_B).pk == acacia.pk


def test_a_known_worker_gains_the_uuid_it_lacked(da, store):
    known = _worker("worker-acacia")
    WorkerIndex(da, OPP).ensure("worker-acacia", UUID_A, parent=store)
    known.refresh_from_db()
    assert known.connect_user_uuid == UUID_A


def test_a_visit_naming_nobody_resolves_to_nothing(da, store):
    assert WorkerIndex(da, OPP).ensure("", "", parent=store) is None
    assert not SupplyPoint.objects.filter(kind="user_held").exists()


def test_a_uuid_alone_finds_but_never_creates(da, store):
    assert WorkerIndex(da, OPP).ensure("", UUID_A, parent=store) is None
    known = _worker("worker-acacia", UUID_A)
    assert WorkerIndex(da, OPP).ensure("", UUID_A, parent=store).pk == known.pk


def test_another_opportunitys_worker_is_not_matched(da, store):
    SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP + 1,
        slug="elsewhere",
        name="worker-acacia",
        kind="user_held",
        connect_username="worker-acacia",
        source="we_recorded",
    )
    assert WorkerIndex(da, OPP).find("worker-acacia", "") is None


def test_supply_point_upsert_carries_the_uuid(da):
    point = call_operation(
        "supply_point_upsert",
        da,
        {
            "data": {
                "slug": "user-10514-worker-cassia",
                "name": "worker-cassia",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "worker-cassia",
                "connect_user_uuid": UUID_B,
                "source": "we_recorded",
            }
        },
    )
    assert point["connect_user_uuid"] == UUID_B
