"""The visit reader, over visit dicts shaped exactly as fetch_raw_visits returns them.

THIS REPOSITORY IS PUBLIC. Every username, id and answer here is invented.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.stock.services.dispensing import read_date, validate_lines
from connect_labs.supply_chain.stock.services.visit_reader import ingest_visit_consumption, outcome_key, visit_status
from connect_labs.supply_chain.values import Quantity

pytestmark = pytest.mark.django_db

PROGRAM = 10515
OPP = 10515
TODAY = date(2026, 9, 28)
FROM = date(2026, 8, 1)
RUTF_PATH = "form.rutf_dispensing.rutf_sachets_dispensed"
RUTF_LINES = [{"kind": "stated", "paths": [RUTF_PATH], "unit": "sachet"}]
VITA_LINES = [
    {
        "kind": "protocol",
        "given_paths": ["form.vita_group.va_delivered"],
        "given_values": ["child_fine"],
        "quantity": "1",
        "unit": "capsule",
    }
]
AMOX_PATH = "form.visit_2_or_greater.cough_assessment.dosage_pneumonia"
AMOX_LINES = [
    {
        "kind": "value_map",
        "paths": [AMOX_PATH],
        "map": {"1 tablet every 12 hours (total 10 tablets)": "10"},
        "unit": "tablet",
    }
]


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)


def _item(sku, slug, base, pack, per):
    commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}", slug=slug, name=slug, base_unit=base, pack_unit=pack, base_per_pack=per
    )
    return Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku=sku,
        name=sku,
        commodity=commodity,
        base_unit=base,
        pack_unit=pack,
        base_per_pack=per,
    )


@pytest.fixture
def rutf():
    return _item("rutf-150", "rutf", "sachet", "carton", 150)


@pytest.fixture
def vita():
    return _item("vita-500", "vitamin-a", "capsule", "bottle", 500)


@pytest.fixture
def amox():
    return _item("amox-100", "amoxicillin", "tablet", "box", 100)


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded"
    )


def _rule(item, store, lines, **extra):
    return DispensingRule.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        item=item,
        lines=validate_lines(lines, item),
        resupply_point=store,
        active_from=extra.pop("active_from", FROM),
        **extra,
    )


@pytest.fixture
def rutf_rule(rutf, store):
    return _rule(rutf, store, RUTF_LINES)


def visit(
    vid,
    *,
    on="2026-09-20",
    username="worker-acacia",
    user_id="uuid-acacia",
    status="pending",
    name="Visit Form",
    answers=None,
    **extra,
):
    body = {"@name": name}
    for path, value in (answers or {}).items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {
        "id": vid,
        "xform_id": f"xf-{vid}",
        "username": username,
        "user_id": user_id,
        "visit_date": on,
        "status": status,
        "flag_reason": {},
        "status_modified_date": None,
        "form_json": {"id": f"xf-{vid}", "form": body},
        **extra,
    }


def read(da, visits, **kwargs):
    return ingest_visit_consumption(da, opportunity_id=OPP, visits=visits, today=TODAY, **kwargs)


def _worker():
    return SupplyPoint.objects.get(kind="user_held", connect_username="worker-acacia")


def test_a_visit_posts_its_consumption_and_is_remembered(da, rutf, rutf_rule):
    report = read(da, [visit(9001, answers={RUTF_PATH: "14"})])

    assert (report["posted"], report["visits_read"], len(report["created_supply_points"])) == (1, 1, 1)
    movement = Movement.objects.get(kind="consumption")
    assert (movement.visit_id, movement.quantity, movement.quantity_unit, movement.from_supply_point_id) == (
        "9001",
        Decimal("14"),
        "sachet",
        _worker().pk,
    )
    assert (movement.occurred_on, movement.source) == (date(2026, 9, 20), "connect_visit")
    assert (_worker().source, _worker().parent_id) == ("connect_visit", rutf_rule.resupply_point_id)
    remembered = WorkerVisit.objects.get(visit_id="9001")
    assert (remembered.status, remembered.xform_id, remembered.supply_point_id) == ("pending", "xf-9001", _worker().pk)
    assert remembered.outcomes == {outcome_key(rutf.pk): "dispensed"}
    assert remembered.answers == {RUTF_PATH: "14"}


def test_reading_the_same_visits_again_writes_nothing(da, rutf, rutf_rule):
    visits = [visit(9001, answers={RUTF_PATH: "14"})]
    read(da, visits)
    movements, revisions = Movement.objects.count(), Revision.objects.count()

    report = read(da, visits)

    assert (report["posted"], report["skipped_already_posted"]) == (0, 1)
    assert (Movement.objects.count(), Revision.objects.count()) == (movements, revisions)


def test_a_steady_state_re_read_costs_the_same_queries_for_one_visit_or_forty(da, rutf, rutf_rule):
    """No per-visit query once everything has been read: an hourly run over a
    whole opportunity must not grow with the opportunity."""

    def many(n):
        return [
            visit(9000 + i, username=f"worker-{i % 3}", user_id=f"uuid-{i % 3}", answers={RUTF_PATH: "2"})
            for i in range(n)
        ]

    few, lots = many(1), many(40)
    read(da, lots)

    with CaptureQueriesContext(connection) as one:
        read(da, few)
    with CaptureQueriesContext(connection) as forty:
        report = read(da, lots)

    assert report["skipped_already_posted"] == 40
    assert len(forty.captured_queries) == len(one.captured_queries)


def test_a_rejected_visit_is_reversed_once(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])

    first = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])
    second = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])

    assert (first["reversed"], second["reversed"], second["skipped_already_reversed"]) == (1, 0, 1)
    assert ledger.balance(PROGRAM, _worker(), item=rutf, unit="sachet") == Quantity(Decimal("0"), "sachet")
    remembered = WorkerVisit.objects.get(visit_id="9001")
    assert (remembered.status, remembered.outcomes) == ("rejected", {outcome_key(rutf.pk): "reversed"})


def test_a_visit_marked_duplicate_is_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    assert read(da, [visit(9001, status="duplicate", answers={RUTF_PATH: "14"})])["reversed"] == 1


DUPLICATE_FLAG = {"flags": [["duplicate_submission", "seen"]]}


def test_a_pending_visit_flagged_as_a_duplicate_is_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    flagged = visit(9001, status="pending", answers={RUTF_PATH: "14"}, flag_reason=DUPLICATE_FLAG)
    assert read(da, [flagged])["reversed"] == 1


@pytest.mark.parametrize("status", ["approved", "over_limit"])
def test_an_approved_visit_carrying_a_duplicate_flag_stands(da, rutf, rutf_rule, status):
    """A reviewer who approved a flagged visit judged it genuine: the sachets left the bag."""
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    flagged = visit(9001, status=status, answers={RUTF_PATH: "14"}, flag_reason=DUPLICATE_FLAG)

    assert read(da, [flagged])["reversed"] == 0
    assert ledger.balance(PROGRAM, _worker(), item=rutf, unit="sachet") == Quantity(Decimal("-14"), "sachet")
    assert WorkerVisit.objects.get(visit_id="9001").status == status


def test_an_approved_flagged_visit_seen_first_is_posted(da, rutf, rutf_rule):
    flagged = visit(9001, status="approved", answers={RUTF_PATH: "14"}, flag_reason=DUPLICATE_FLAG)
    assert read(da, [flagged])["posted"] == 1


def test_a_visit_first_seen_rejected_is_never_posted(da, rutf, rutf_rule):
    report = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])
    assert (report["posted"], report["rejected_unposted"]) == (0, 1)
    assert not Movement.objects.filter(kind="consumption").exists()
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {outcome_key(rutf.pk): "not_counted"}


def test_every_approval_status_counts_on_arrival(da, rutf, rutf_rule):
    visits = [
        visit(9001, status="pending", answers={RUTF_PATH: "1"}),
        visit(9002, status="approved", answers={RUTF_PATH: "1"}),
        visit(9003, status="over_limit", answers={RUTF_PATH: "1"}),
    ]
    assert read(da, visits)["posted"] == 3


def test_no_answer_never_posts(da, rutf, rutf_rule):
    report = read(da, [visit(9001, answers={"form.other.question": "1"})])
    assert (report["posted"], report["no_answer"]) == (0, 1)
    assert not Movement.objects.filter(kind="consumption").exists()
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {outcome_key(rutf.pk): "no_answer"}


def test_a_zero_is_nothing_given_not_no_answer(da, rutf, rutf_rule):
    report = read(da, [visit(9001, answers={RUTF_PATH: "0"})])
    assert (report["posted"], report["nothing_given"], report["no_answer"]) == (0, 1, 0)


def test_active_from_is_respected(da, rutf, rutf_rule):
    report = read(da, [visit(9001, on="2026-07-31", answers={RUTF_PATH: "14"})])
    assert (report["posted"], report["before_active_from"]) == (0, 1)
    assert not WorkerVisit.objects.exists()


def test_a_visit_naming_no_worker_is_reported(da, rutf, rutf_rule):
    report = read(da, [visit(9001, username="", user_id="", answers={RUTF_PATH: "14"})])
    assert report["unmatched"] == [
        {"visit_id": "9001", "reason": "the visit names no worker (no username or user id)"}
    ]
    assert not Movement.objects.filter(kind="consumption").exists()


def test_a_unit_the_item_cannot_convert_is_refused_and_reported(da, rutf, store):
    _rule(rutf, store, [{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}])
    Item.objects.filter(pk=rutf.pk).update(base_per_pack=None)
    Commodity.objects.filter(pk=rutf.commodity_id).update(base_per_pack=None)

    report = read(da, [visit(9001, answers={"form.cartons": "1"})])

    assert report["posted"] == 0
    assert report["unit_refused"][0]["visit_id"] == "9001"
    assert report["unit_refused"][0]["item_id"] == rutf.pk


def test_an_item_with_no_single_unit_is_refused_not_a_crash(da, rutf, store):
    """base_unit raises ValueError when the item states no single unit: one
    visit's refusal, reported, never a failed run."""
    _rule(rutf, store, RUTF_LINES)
    Item.objects.filter(pk=rutf.pk).update(base_unit="")
    Commodity.objects.filter(pk=rutf.commodity_id).update(base_unit="")

    report = read(da, [visit(9001, answers={RUTF_PATH: "14"}), visit(9002, answers={RUTF_PATH: "3"})])

    assert report["posted"] == 0
    assert [row["visit_id"] for row in report["unit_refused"]] == ["9001", "9002"]
    assert "single unit" in report["unit_refused"][0]["reasons"][0]
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {outcome_key(rutf.pk): "unit_refused"}


def test_an_answer_the_map_does_not_know_is_reported_with_the_answer(da, amox, store):
    _rule(amox, store, AMOX_LINES)

    report = read(da, [visit(9001, answers={AMOX_PATH: "3 tablets every 8 hours"})])

    assert (report["posted"], report["no_answer"]) == (0, 0)
    assert report["unmapped"] == [
        {"visit_id": "9001", "item_id": amox.pk, "answers": [{"path": AMOX_PATH, "answer": "3 tablets every 8 hours"}]}
    ]
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {outcome_key(amox.pk): "unmapped"}


def test_a_quantity_too_small_to_record_is_skipped_with_a_reason(da, rutf, rutf_rule):
    report = read(da, [visit(9001, answers={RUTF_PATH: "0.00001"})])

    assert report["posted"] == 0
    assert report["skipped"] == [
        {
            "visit_id": "9001",
            "item_id": rutf.pk,
            "reason": "what it gave rounds to nothing at the ledger's precision (0.0001 sachet)",
        }
    ]
    assert not Movement.objects.filter(kind="consumption").exists()


def test_protocol_rows_are_marked_estimated(da, vita, store):
    _rule(vita, store, VITA_LINES)
    report = read(da, [visit(9001, answers={"form.vita_group.va_delivered": "child_fine"})])
    assert (report["posted"], report["estimated"]) == (1, 1)
    assert Movement.objects.get(kind="consumption").estimated is True


def test_a_second_rule_on_the_same_visit_posts_its_own_item(da, rutf, vita, store, rutf_rule):
    _rule(vita, store, VITA_LINES)
    read(da, [visit(9001, answers={RUTF_PATH: "14", "form.vita_group.va_delivered": "child_fine"})])
    assert sorted(Movement.objects.filter(kind="consumption").values_list("item_id", flat=True)) == sorted(
        [rutf.pk, vita.pk]
    )


def test_a_form_the_rule_does_not_read_is_neither_posted_nor_unanswered(da, rutf, store):
    _rule(rutf, store, RUTF_LINES, forms=["Visit Form"])
    report = read(da, [visit(9001, name="Stock Management", answers={"form.stock_balance.sachets_remaining": "40"})])
    assert (report["posted"], report["no_answer"], report["not_applicable"]) == (0, 0, 1)
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {}


def test_a_line_narrowed_to_one_form_is_not_applicable_on_another(da, rutf, store):
    _rule(rutf, store, [{**RUTF_LINES[0], "forms": ["Visit Form"]}])
    report = read(da, [visit(9001, name="Screening", answers={RUTF_PATH: "14"})])
    assert (report["posted"], report["no_answer"], report["not_applicable"]) == (0, 0, 1)


def test_editing_a_rule_does_not_repost_old_visits(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    rutf_rule.lines = validate_lines([{"kind": "stated", "paths": [RUTF_PATH], "unit": "carton"}], rutf)
    rutf_rule.save()

    report = read(da, [visit(9001, answers={RUTF_PATH: "14"}), visit(9002, answers={RUTF_PATH: "1"})])

    assert (report["posted"], report["skipped_already_posted"]) == (1, 1)
    assert Movement.objects.get(visit_id="9001").quantity == Decimal("14")
    assert Movement.objects.get(visit_id="9002").quantity == Decimal("150")


def test_an_inactive_rule_reads_nothing(da, rutf, store):
    _rule(rutf, store, RUTF_LINES, status="inactive")
    report = read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    assert (report["rules"], report["posted"]) == (0, 0)


def test_a_visit_reinstated_after_reversal_is_reported_not_reposted(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])

    report = read(da, [visit(9001, status="approved", answers={RUTF_PATH: "14"})])

    assert report["reinstated_after_reversal"] == ["9001"]
    assert Movement.objects.filter(visit_id="9001").count() == 2
    assert ledger.balance(PROGRAM, _worker(), item=rutf, unit="sachet") == Quantity(Decimal("0"), "sachet")


def test_future_and_undated_visits_are_skipped(da, rutf, rutf_rule):
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    report = read(
        da,
        [
            visit(9001, on=tomorrow, answers={RUTF_PATH: "14"}),
            visit(9002, on="", answers={RUTF_PATH: "14"}),
            visit(9003, on="not a date", answers={RUTF_PATH: "14"}),
            visit(None, answers={RUTF_PATH: "14"}),
        ],
    )
    assert (report["future"], report["undated"], report["posted"]) == (1, 3, 0)
    assert not WorkerVisit.objects.exists()


def test_until_reads_a_status_as_it_was_then(da, rutf, rutf_rule):
    later = visit(9001, status="rejected", answers={RUTF_PATH: "14"}, status_modified_date="2026-09-25T10:00:00")

    then = read(da, [later], until=date(2026, 9, 21))
    now = read(da, [later])

    assert (then["posted"], then["reversed"]) == (1, 0)
    assert now["reversed"] == 1


def test_visits_after_until_are_left_for_later(da, rutf, rutf_rule):
    report = read(da, [visit(9001, on="2026-09-22", answers={RUTF_PATH: "14"})], until=date(2026, 9, 21))
    assert (report["posted"], report["after_until"]) == (0, 1)


def test_a_status_change_is_remembered_and_revisioned(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    remembered = WorkerVisit.objects.get(visit_id="9001")

    read(da, [visit(9001, status="approved", answers={RUTF_PATH: "14"})])

    assert WorkerVisit.objects.get(pk=remembered.pk).status == "approved"
    assert Revision.objects.filter(object_id=str(remembered.pk), action="update").count() == 1


def test_read_date_takes_the_day_of_a_timestamp_and_refuses_anything_else():
    assert read_date("2026-09-25T10:00:00") == date(2026, 9, 25)
    assert read_date(date(2026, 9, 25)) == date(2026, 9, 25)
    assert [read_date(v) for v in ("", None, "not a date", "2026-13-01")] == [None, None, None, None]


def test_visit_status_reads_a_later_rejection_as_pending_then():
    rejected = {"status": "Rejected", "status_modified_date": "2026-09-25T10:00:00"}
    assert visit_status(rejected) == "rejected"
    assert visit_status(rejected, until=date(2026, 9, 24)) == "pending"
    assert visit_status(rejected, until=date(2026, 9, 25)) == "rejected"


def test_a_visit_rejected_after_its_rule_was_switched_off_is_still_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    DispensingRule.objects.filter(pk=rutf_rule.pk).update(status="inactive")

    report = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])

    assert (report["rules"], report["reversed"]) == (0, 1)
    assert ledger.balance(PROGRAM, _worker(), item=rutf, unit="sachet") == Quantity(Decimal("0"), "sachet")
    remembered = WorkerVisit.objects.get(visit_id="9001")
    assert (remembered.status, remembered.outcomes) == ("rejected", {outcome_key(rutf.pk): "reversed"})
    assert read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])["skipped_already_reversed"] == 1


def test_a_visit_rejected_after_its_rule_moved_past_it_is_still_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    DispensingRule.objects.filter(pk=rutf_rule.pk).update(active_from=date(2026, 9, 25))

    report = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])

    assert (report["reversed"], report["before_active_from"]) == (1, 1)
    assert ledger.balance(PROGRAM, _worker(), item=rutf, unit="sachet") == Quantity(Decimal("0"), "sachet")


def test_a_rejected_visit_whose_worker_no_longer_resolves_is_still_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])

    report = read(da, [visit(9001, status="rejected", username="", user_id="", answers={RUTF_PATH: "14"})])

    assert report["reversed"] == 1
    assert ledger.balance(PROGRAM, _worker(), item=rutf, unit="sachet") == Quantity(Decimal("0"), "sachet")


def test_two_runs_in_one_transaction_take_the_lock_and_post_once(da, rutf, rutf_rule):
    visits = [visit(9001, answers={RUTF_PATH: "14"})]
    with CaptureQueriesContext(connection) as queries:
        first, second = read(da, visits), read(da, visits)

    assert (first["posted"], second["posted"], second["skipped_already_posted"]) == (1, 0, 1)
    assert Movement.objects.filter(kind="consumption").count() == 1
    assert sum("pg_advisory_xact_lock" in q["sql"] for q in queries.captured_queries) == 2


# ---- where a worker is: the middle of their visits ---------------------------
# Coordinates are invented placeholders (public repo).


def _at(lat, lng):
    return {"location": f"{lat} {lng} 300 5"}


def test_a_worker_is_placed_at_the_middle_of_their_visits_and_their_store_among_them(da, rutf, rutf_rule, store):
    read(
        da,
        [
            visit(9101, answers={RUTF_PATH: "14"}, **_at(9.0, 7.0)),
            visit(9102, answers={RUTF_PATH: "14"}, **_at(9.2, 7.2)),
            # One stray fix far away does not drag a median off.
            visit(9103, answers={RUTF_PATH: "14"}, **_at(40.0, 60.0)),
            visit(9104, username="worker-baobab", user_id="uuid-baobab", answers={RUTF_PATH: "7"}, **_at(9.4, 7.4)),
        ],
    )

    acacia = _worker()
    assert (acacia.latitude, acacia.longitude) == (9.2, 7.2)
    assert acacia.location_source == "visits"
    assert acacia.location_label == "middle of 3 visits"
    store.refresh_from_db()
    assert store.location_source == "served"
    assert (store.latitude, store.longitude) == (pytest.approx(9.3), pytest.approx(7.3))


def test_gps_comes_from_the_forms_metadata_when_the_export_has_none_and_is_rounded(da, rutf, rutf_rule):
    row = visit(9111, answers={RUTF_PATH: "14"})
    row["form_json"]["metadata"] = {"location": "9.12345 7.98765 300 5"}
    read(da, [row])

    remembered = WorkerVisit.objects.get(visit_id="9111")
    assert (remembered.latitude, remembered.longitude) == (9.123, 7.988)
    assert _worker().location_source == "visits"


def test_a_visit_without_usable_gps_leaves_the_worker_at_their_store(da, rutf, rutf_rule, store):
    store.latitude, store.longitude = 9.0, 7.0
    store.save()
    read(da, [visit(9121, answers={RUTF_PATH: "14"}, location="0.0 0.0 0 0")])

    worker = _worker()
    assert worker.location_source == "parent"
    assert WorkerVisit.objects.get(visit_id="9121").latitude is None


def test_re_reading_visits_with_gps_writes_and_places_nothing_new(da, rutf, rutf_rule):
    visits = [visit(9131 + i, answers={RUTF_PATH: "14"}, **_at(9.0 + i / 10, 7.0)) for i in range(3)]
    read(da, visits)
    revisions = Revision.objects.count()

    read(da, visits)

    assert Revision.objects.count() == revisions
