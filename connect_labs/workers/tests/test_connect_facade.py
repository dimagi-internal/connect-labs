"""The Connect facade: every export gap worked around, and said so in Coverage."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

from connect_labs.workers.connect_facade import (
    ACCESSIBLE_OPPORTUNITIES,
    EVENTS,
    METHOD_KEYS,
    Capabilities,
    ConnectWorkerSource,
    ExportFacade,
    Result,
    RoutedSource,
    entities_key,
    get_source,
    shapes,
)
from connect_labs.workers.connect_facade.base import Coverage
from connect_labs.workers.tests.fake_connect import FakeConnect, opp_path

ALICE = "alice_pid"
BOB = "bob_pid"


def _user(username, **kw):
    row = {
        "username": username,
        "name": username.title(),
        "phone": "+100000",
        "user_invite_status": "accepted",
        "suspended": "False",
        "suspension_reason": "",
        "invited_date": "2026-10-01T10:00:00Z",
        "date_learn_started": None,
        "completed_learn_date": None,
        "last_active": None,
        "date_claimed": None,
        "payment_accrued": 0,
        "claim_limits": [],
    }
    row.update(kw)
    return row


def _connect(**extra):
    lists = {
        opp_path(1, "user_data"): [_user(ALICE), _user(BOB)],
        opp_path(2, "user_data"): [_user(BOB)],
        opp_path(1, "completed_module"): [
            {"username": ALICE, "module": 7, "date": "2026-10-02T10:00:00Z", "duration": "0:10:00"},
            {"username": BOB, "module": 7, "date": "2026-10-03T10:00:00Z", "duration": "0:12:00"},
        ],
        opp_path(1, "payment"): [
            {"username": ALICE, "created_at": "2026-10-05T10:00:00Z", "amount": 500, "payment_unit": 3},
            {"username": BOB, "created_at": "2026-10-05T11:00:00Z", "amount": 400, "payment_unit": 3},
        ],
        opp_path(1, "task_types"): [
            {"id": 11, "task_type_id": "tt-uuid", "name": "Coach", "slug": "c", "mode": "ocs"}
        ],
        opp_path(1, "assigned_tasks"): [
            {
                "id": 5,
                "assigned_task_id": "at-uuid",
                "task_type": 11,
                "task_type_name": "Coach",
                "username": ALICE,
                "status": "assigned",
                "due_date": "2026-10-20",
                "date_created": "2026-10-10T10:00:00Z",
            }
        ],
        opp_path(1, "completed_works"): [
            {
                "username": ALICE,
                "payment_unit_id": 3,
                "entity_id": "Jane Doe +2547",
                "entity_name": "Jane Doe",
                "status": "approved",
                "date_created": "2026-10-04T10:00:00Z",
            }
        ],
    }
    lists.update(extra.pop("lists", {}))
    objects = {
        "/export/opp_org_program_list/": {
            "organizations": [{"id": 1, "slug": "org-a", "name": "Org A"}],
            "programs": [{"id": 9, "name": "Prog", "organization": "org-a"}],
            "opportunities": [
                {"id": 1, "name": "Opp One", "program": 9, "organization": "org-a", "is_active": True},
                {"id": 2, "name": "Opp Two", "program": 9, "organization": "org-a", "is_active": False},
            ],
        },
        "/export/opportunity/1/": {
            "id": 1,
            "name": "Opp One",
            "learn_app": {"learn_modules": [{"id": 7, "slug": "intro", "name": "Intro", "time_estimate": 5}]},
        },
        "/export/opportunity/30/": {"id": 30, "name": "Supervised", "organization": "org-b", "is_active": True},
    }
    objects.update(extra.pop("objects", {}))
    return FakeConnect(lists=lists, objects=objects, **extra)


def test_every_method_the_asks_name_is_on_the_interface():
    for name in (
        "accessible_opportunities",
        "opportunity_workers",
        "worker_opportunities",
        "entities",
        "events",
        "notifications",
        "learn_modules",
    ):
        assert callable(getattr(ConnectWorkerSource, name))
    assert entities_key("payment") in METHOD_KEYS


def test_listed_and_probed_opportunities_say_which_is_which():
    fake = _connect(accessible={30})
    found = ExportFacade(fake.client()).accessible_opportunities(probe_ids=[1, 30, 31])
    by_id = {o.id: o for o in found.opportunities}
    assert by_id[1].via == "listed" and by_id[1].program_id == 9
    assert by_id[30].via == "probed" and by_id[30].name == "Supervised"
    assert 31 not in by_id
    # The probe only asks about ids the list did not give.
    assert fake.count("opportunity_access") == 1
    assert found.coverage.complete is False and "supervising" in found.coverage.notes[0]


def test_a_failed_probe_keeps_the_listing_and_says_so():
    fake = _connect(missing={"/export/opportunity_access/"})
    found = ExportFacade(fake.client()).accessible_opportunities(probe_ids=[30])
    assert {o.id for o in found.opportunities} == {1, 2}
    assert any("failed" in n for n in found.coverage.notes)


def test_no_per_user_filter_pulls_once_and_filters_in_labs():
    fake = _connect()
    facade = ExportFacade(fake.client())
    alice = facade.entities(1, "payment", username=ALICE)
    bob = facade.entities(1, "payment", username=BOB)
    assert [r["username"] for r in alice.rows] == [ALICE]
    assert [r["username"] for r in bob.rows] == [BOB]
    assert fake.count("/payment/") == 1  # memoised: one download serves every worker
    assert any("filtered in labs" in n for n in alice.coverage.notes)


def test_completed_module_uses_connects_own_username_filter():
    fake = _connect()
    rows = ExportFacade(fake.client()).entities(1, "completed_module", username=ALICE).rows
    assert [r["username"] for r in rows] == [ALICE]
    assert ("GET", opp_path(1, "completed_module"), {"username": ALICE, "page_size": "2500"}) in fake.calls


def test_rows_without_ids_get_stable_keys_that_survive_a_status_change():
    a = shapes.payment({"username": ALICE, "created_at": "2026-10-05", "amount": 5, "payment_unit": 3}, 1)
    b = shapes.payment(
        {"username": ALICE, "created_at": "2026-10-05", "amount": 5, "payment_unit": 3, "confirmed": "True"}, 1
    )
    assert a["key"] == b["key"] and a["key"].startswith("s:")
    assert b["confirmed"] is True


def test_work_units_never_carry_the_beneficiary():
    fake = _connect()
    (row,) = ExportFacade(fake.client()).entities(1, "completed_works", username=ALICE).rows
    assert "entity_id" not in row and "entity_name" not in row
    assert "Jane" not in str(row)


def test_since_is_emulated_on_a_full_pull():
    fake = _connect()
    since = datetime(2026, 10, 5, 10, 30, tzinfo=dt_timezone.utc)
    rows = ExportFacade(fake.client()).entities(1, "payment", since=since).rows
    assert [r["username"] for r in rows] == [BOB]


def test_assigned_tasks_get_their_mode_from_task_types():
    fake = _connect()
    (task,) = ExportFacade(fake.client()).entities(1, "assigned_tasks").rows
    assert task["mode"] == "ocs" and task["key"] == "at-uuid"


def test_worker_opportunities_scans_every_opportunity_and_says_so():
    fake = _connect()
    result = ExportFacade(fake.client()).worker_opportunities(BOB)
    assert sorted(r["opportunity_id"] for r in result.rows) == [1, 2]
    assert result.coverage.complete is True
    assert "one by one" in result.coverage.notes[0]


def test_worker_opportunities_reports_what_it_could_not_read():
    fake = _connect(missing={opp_path(2, "user_data")})
    result = ExportFacade(fake.client()).worker_opportunities(BOB, opportunity_ids=[1, 2])
    assert [r["opportunity_id"] for r in result.rows] == [1]
    assert result.coverage.complete is False


def test_what_connect_does_not_record_comes_back_empty_with_a_reason():
    facade = ExportFacade(_connect().client())
    events = facade.events(1, since=None)
    notes = facade.notifications(ALICE, since=None)
    assert events.rows == [] and events.coverage.complete is False and "history" in events.coverage.notes[0]
    assert notes.rows == [] and "does not record" in notes.coverage.notes[0]


def test_learn_module_names_come_from_the_opportunity_detail():
    (module,) = ExportFacade(_connect().client()).learn_modules(1).rows
    assert module["name"] == "Intro" and module["key"] == "7"


@pytest.mark.django_db
def test_visits_prefer_the_pulse_tail_for_a_recent_since():
    from connect_labs.pulse.models import PulseEvent

    now = timezone.now()
    PulseEvent.objects.create(
        connect_visit_id=101,
        opportunity_id=1,
        field_ts=now,
        sync_ts=now,
        status="approved",
        worker_hash=ALICE,
        review_status="agree",
    )
    fake = _connect()
    result = ExportFacade(fake.client()).entities(1, "visits_thin", username=ALICE, since=now - timedelta(hours=1))
    assert [r["visit_id"] for r in result.rows] == ["101"]
    assert result.coverage.source == "labs-pulse"
    assert fake.count("user_visits") == 0  # never downloads a form


@pytest.mark.django_db
def test_visits_read_the_analysis_cache_without_forms():
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

    RawVisitCache.objects.create(
        opportunity_id=1,
        pipeline_id=USER_VISITS_RAW_SLOT,
        visit_count=1,
        expires_at=timezone.now() + timedelta(hours=1),
        visit_id="555",
        username=ALICE,
        status="pending",
        entity_id="Jane Doe",
        form_json={"form": {"secret": "x"}},
    )
    result = ExportFacade(_connect().client()).entities(1, "visits_thin", username=ALICE)
    (row,) = result.rows
    assert row["visit_id"] == "555" and row["status"] == "pending"
    assert "form_json" not in row and "Jane" not in str(row) and row["entity_key"]
    assert result.coverage.complete is True and result.coverage.source == "labs-visit-cache"


@pytest.mark.django_db
def test_visits_with_no_cache_are_incomplete_unless_fetching_is_allowed():
    visit = {
        "id": 9,
        "username": ALICE,
        "status": "approved",
        "form_json": {"big": "x" * 100},
        "entity_id": "Jane",
        "visit_date": "2026-10-01",
    }
    fake = _connect(lists={opp_path(1, "user_visits"): [visit]})
    cold = ExportFacade(fake.client()).entities(1, "visits_thin", username=ALICE)
    assert cold.rows == [] and cold.coverage.complete is False
    assert fake.count("user_visits") == 0

    streamed = ExportFacade(fake.client(), visit_fetch=True).entities(1, "visits_thin", username=ALICE)
    (row,) = streamed.rows
    assert row["visit_id"] == "9" and "form_json" not in row and streamed.coverage.complete is True


class _NativeEvents(ConnectWorkerSource):
    name = "native"
    capabilities = Capabilities(native=frozenset({EVENTS}))

    def events(self, opp_id, *, since, username=None):
        return Result([{"key": "e1"}], Coverage(complete=True, exact=True, method="native"))

    def accessible_opportunities(self, *, probe_ids=None):
        raise AssertionError("not native")

    opportunity_workers = worker_opportunities = entities = notifications = learn_modules = accessible_opportunities


def test_a_native_source_takes_over_only_the_methods_it_declares():
    fake = _connect()
    routed = RoutedSource([_NativeEvents()], ExportFacade(fake.client()))
    assert routed.events(1, since=None).rows == [{"key": "e1"}]
    assert routed.source_for(ACCESSIBLE_OPPORTUNITIES) is routed.fallback
    assert {o.id for o in routed.accessible_opportunities().opportunities} == {1, 2}
    assert routed.capabilities.serves_natively(EVENTS)


def test_get_source_wraps_the_facade(monkeypatch):
    routed = get_source(_connect().client())
    assert isinstance(routed.fallback, ExportFacade)
    assert routed.sources == []
