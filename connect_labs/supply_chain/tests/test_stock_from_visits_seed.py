"""The synthetic stock-from-visits world, seeded end to end through the real operations.

THIS REPOSITORY IS PUBLIC. Every name here is invented; the form paths and
answer strings are copied from the released app's definition, and no
submission was read.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.fixture_store import ENDPOINT_FILES
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.demo.stock_from_visits import (
    AMOX_DOSES,
    FORM_SCREENING,
    FORM_STOCK,
    FORM_VISIT,
    NEVER_ANSWERS,
    OVER_COUNTS,
    PATHS,
    REJECTED_LATER,
    RUNS_OUT,
    WORKERS,
    build_world,
    seed,
)
from connect_labs.supply_chain.history.as_of import end_of_day
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.history.rewind import rewind
from connect_labs.supply_chain.models import DispensingRule, Item, Movement, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import belief
from connect_labs.supply_chain.stock.services.visit_reader import outcome_key

SKUS = {"syn-rutf-150", "syn-vita100k-100", "syn-vita200k-100", "syn-amox-100", "syn-mrdt-25"}
START = date(2026, 8, 3)


def leaf_paths(node, prefix="form"):
    for key, value in node.items():
        if isinstance(value, dict):
            yield from leaf_paths(value, f"{prefix}.{key}")
        else:
            yield f"{prefix}.{key}"


def answer(visit, path):
    node = visit["form_json"]
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def forms_named(world, name):
    return [v for v in world.visits if v["form_json"]["form"]["@name"] == name]


# ---- the world, before any database ----------------------------------------


def test_the_world_uses_the_released_apps_paths_and_invented_names():
    world = build_world(START)
    paths = {p for v in world.visits for p in leaf_paths(v["form_json"]["form"]) if "@" not in p}

    for key in (
        "rutf_screening_total",
        "rutf_screening_ration",
        "rutf_visit",
        "appetite_visit",
        "balance",
        "received",
        "received_on",
        "remaining",
        "amox_presumptive",
        "amox_fast_breathing",
        "amox_comorbid",
        "amox_visit",
        "mrdt_screening",
        "mrdt_visit",
    ):
        assert PATHS[key] in paths, key
    assert not any("ors" in p or "zinc" in p or "paracetamol" in p or "child_age" in p for p in paths)
    assert len(WORKERS) == 20 and all(w.startswith("worker-") for w in WORKERS)
    assert {v["username"] for v in world.visits} == set(WORKERS)
    assert {FORM_SCREENING, FORM_VISIT, FORM_STOCK} == {v["form_json"]["form"]["@name"] for v in world.visits}


def test_amoxicillin_doses_are_the_apps_exact_answer_strings():
    world = build_world(START)
    answered = {
        answer(v, PATHS[key])
        for v in world.visits
        for key in ("amox_fast_breathing", "amox_comorbid", "amox_visit")
        if answer(v, PATHS[key]) is not None
    }
    # The missing space before "(total 20" is the app's, and must match exactly.
    assert answered == {"1 tablet every 12 hours (total 10 tablets)", "2 tablets every 12 hours(total 20 tablets)"}
    assert AMOX_DOSES == {
        "1 tablet every 12 hours (total 10 tablets)": 10,
        "2 tablets every 12 hours(total 20 tablets)": 20,
    }


def test_the_screening_deduction_is_the_total_and_the_appetite_test_is_a_whole_sachet():
    world = build_world(START)
    for v in forms_named(world, FORM_SCREENING):
        ration = answer(v, PATHS["rutf_screening_ration"])
        total = answer(v, PATHS["rutf_screening_total"])
        if ration is not None:
            assert total == ration  # enrolled: the deduction IS the ration, never ration + 1
    appetite = {answer(v, PATHS["appetite_visit"]) for v in forms_named(world, FORM_VISIT)} - {None}
    assert appetite == {"0", "1"}


def test_one_visit_is_rejected_after_it_was_counted():
    world = build_world(START)
    rejected = [v for v in world.visits if v["status"] == "rejected"]
    assert [v["username"] for v in rejected] == [REJECTED_LATER]
    assert rejected[0]["status_modified_date"][:10] > rejected[0]["visit_date"]


def test_the_world_is_deterministic():
    assert build_world(START).visits == build_world(START).visits


# ---- seeded -----------------------------------------------------------------


class FakeDrive:
    def __init__(self):
        self.files = {}
        self.folders = 0

    def create_folder(self, name, parent_id):
        self.folders += 1
        return f"folder-{self.folders}"

    def upload_file(self, folder_id, filename, content):
        self.files[(folder_id, filename)] = json.loads(content)
        return f"file-{len(self.files)}"


class FakeStore:
    """Serves what FakeDrive holds for the opportunity's registered folder."""

    def __init__(self, drive):
        self.drive = drive

    def load_endpoint(self, opp_id, endpoint_key):
        folder = SyntheticOpportunity.objects.get(opportunity_id=opp_id).gdrive_folder_id
        return self.drive.files.get((folder, ENDPOINT_FILES[endpoint_key]), [])

    def reload(self, opp_id):
        return None


FIXTURE_STORE = "connect_labs.labs.integrations.connect.factory._get_fixture_store"


@pytest.fixture
def seeded(db, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    drive = FakeDrive()
    registry.invalidate_cache()
    with patch(FIXTURE_STORE, return_value=FakeStore(drive)):
        summary = seed(drive=drive)
        yield {"drive": drive, "summary": summary, "opp": summary["opportunity_id"]}
    registry.invalidate_cache()


def item(opp, sku="syn-rutf-150"):
    return Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=sku)


def by_name(opp, **kwargs):
    return {b.point.connect_username: b for b in belief.worker_beliefs(opp, item(opp), **kwargs)}


def world_of(seeded):
    return build_world(date.fromisoformat(seeded["summary"]["start"]))


def posted_for(opp, visit, sku):
    return Movement.objects.filter(
        program_id=opp, visit_id=str(visit["id"]), item__sku=sku, reverses__isnull=True
    ).first()


def test_it_is_a_registered_labs_only_opportunity(seeded):
    row = SyntheticOpportunity.objects.get(opportunity_id=seeded["opp"])
    assert row.labs_only and row.enabled and row.gdrive_folder_id.startswith("folder-")
    assert seeded["opp"] >= 10_000
    assert seeded["summary"]["seeded"] is True and len(seeded["summary"]["weeks"]) == 8


def test_every_weekly_read_is_clean(seeded):
    """Nothing the reader could not place: every path and answer string is one a rule knows."""
    weeks = seeded["summary"]["weeks"]
    for week in weeks:
        assert (week["unmatched"], week["unmapped"], week["unit_refused"], week["skipped"]) == (0, 0, 0, 0), week
        assert week["posted"] > 0 and week["balances_recorded"] > 0, week
    assert [w["reversed"] for w in weeks] == [0, 0, 0, 0, 1, 0, 0, 0]
    assert sum(w["receipts_recorded"] for w in weeks) == 20 + 19 + 1  # two deliveries, and marula's own


def test_rules_exist_for_rutf_two_vitamin_a_strengths_amoxicillin_and_mrdt_only(seeded):
    opp = seeded["opp"]
    rules = DispensingRule.objects.filter(program_id=opp)
    assert set(rules.values_list("item__sku", flat=True)) == SKUS
    assert set(rules.values_list("opportunity_id", flat=True)) == {opp}
    names = " ".join(Item.objects.filter(scope_key=f"prog:{opp}").values_list("sku", flat=True)).lower()
    for off in ("ors", "zinc", "al-", "paracetamol", "albendazole"):
        assert off not in names


def test_the_reader_posted_visits_for_every_item(seeded):
    posted = Movement.objects.filter(program_id=seeded["opp"], kind="consumption", source="connect_visit")
    assert posted.filter(item__sku="syn-rutf-150").count() > 300
    assert not posted.filter(item__sku="syn-rutf-150", estimated=True).exists()  # stated, never estimated
    assert posted.filter(item__sku="syn-amox-100", estimated=True).exists()
    assert set(posted.values_list("item__sku", flat=True)) == SKUS


def test_a_screening_posts_its_deduction_alone_and_a_visit_adds_the_whole_appetite_sachet(seeded):
    opp, world = seeded["opp"], world_of(seeded)
    enrolled = next(
        v
        for v in forms_named(world, FORM_SCREENING)
        if v["status"] != "rejected" and answer(v, PATHS["rutf_screening_ration"]) == "14"
    )
    assert posted_for(opp, enrolled, "syn-rutf-150").quantity == Decimal("14")
    appetite_only = next(
        v
        for v in forms_named(world, FORM_VISIT)
        if answer(v, PATHS["appetite_visit"]) == "1" and v["username"] != RUNS_OUT
    )
    assert posted_for(opp, appetite_only, "syn-rutf-150").quantity == Decimal("1")


def test_vitamin_a_strength_follows_the_dose_question_answered(seeded):
    opp, world = seeded["opp"], world_of(seeded)

    def given(v, dose_keys):
        return any(answer(v, PATHS[k]) is not None for k in dose_keys) and "child_fine" in str(
            answer(v, PATHS["vita_screening"]) or answer(v, PATHS["vita_visit"])
        )

    infant = next(v for v in world.visits if given(v, ("vita_6_11_screening", "vita_6_11_visit")))
    older = next(
        v
        for v in world.visits
        if given(v, ("vita_1_2_screening", "vita_1_2_visit", "vita_2_5_screening", "vita_2_5_visit"))
    )
    assert posted_for(opp, infant, "syn-vita100k-100").quantity == Decimal("1")
    assert posted_for(opp, infant, "syn-vita200k-100") is None
    assert posted_for(opp, older, "syn-vita200k-100").quantity == Decimal("1")
    assert posted_for(opp, older, "syn-vita100k-100") is None


def test_amoxicillin_reads_the_dose_the_app_chose(seeded):
    opp, world = seeded["opp"], world_of(seeded)
    twenty = next(
        v for v in world.visits if answer(v, PATHS["amox_visit"]) == "2 tablets every 12 hours(total 20 tablets)"
    )
    assert posted_for(opp, twenty, "syn-amox-100").quantity == Decimal("20")


def test_one_worker_never_answers_the_rutf_question(seeded):
    key = outcome_key(item(seeded["opp"]).pk)
    unanswered = WorkerVisit.objects.filter(
        program_id=seeded["opp"], connect_username=NEVER_ANSWERS, **{f"outcomes__{key}": "no_answer"}
    )
    assert unanswered.count() >= 20
    assert by_name(seeded["opp"])[NEVER_ANSWERS].no_answer_visits >= 20
    others = WorkerVisit.objects.filter(program_id=seeded["opp"], **{f"outcomes__{key}": "no_answer"})
    assert set(others.values_list("connect_username", flat=True)) == {NEVER_ANSWERS}


def test_one_visit_is_reversed(seeded):
    reversals = Movement.objects.filter(program_id=seeded["opp"], reverses__isnull=False)
    assert reversals.count() == 1
    assert reversals.get().to_supply_point.connect_username == REJECTED_LATER


def test_one_worker_runs_out(seeded):
    beliefs = by_name(seeded["opp"])
    assert beliefs[RUNS_OUT].status == "stockout"
    assert [name for name, b in beliefs.items() if b.status == "stockout"] == [RUNS_OUT]


def test_one_worker_counts_well_above_the_ledger_and_reports_a_receipt_nobody_recorded(seeded):
    marula = by_name(seeded["opp"])[OVER_COUNTS]
    assert marula.variance.amount >= 90
    unmatched = belief.unmatched_receipts(seeded["opp"], marula.point, item(seeded["opp"]))
    assert len(unmatched) == 1
    for name, b in by_name(seeded["opp"]).items():
        if name != OVER_COUNTS:
            assert not b.unmatched_receipts, name


def test_most_workers_reconcile(seeded):
    beliefs = by_name(seeded["opp"])
    reconciled = {name for name, b in beliefs.items() if b.variance is not None and b.variance.amount == 0}
    assert reconciled == set(WORKERS) - {OVER_COUNTS, REJECTED_LATER}
    # The rejected visit's sachets left the bag on the phone's arithmetic, and
    # the ledger has put them back: the reversal is exactly the difference.
    assert beliefs[REJECTED_LATER].variance.amount == -14


def test_history_is_dated_week_by_week(seeded):
    three_days_ago = timezone.now() - timedelta(days=3)
    assert Revision.objects.filter(program_id=seeded["opp"], recorded_at__lt=three_days_ago).exists()


def _dispensed(opp, on_date=None):
    return sum(b.dispensed.amount for b in by_name(opp, on_date=on_date).values())


def test_as_of_four_weeks_ago_shows_less_dispensed_than_today(seeded):
    opp = seeded["opp"]
    today = _dispensed(opp)
    past = timezone.localdate() - timedelta(weeks=4)
    with transaction.atomic():
        undone = rewind(opp, end_of_day(past))
        # Rewound by recorded time alone -- no date filter -- the later weeks' reads are gone.
        rewound = _dispensed(opp)
        rewound_as_page = _dispensed(opp, on_date=past)
        transaction.set_rollback(True)
    assert undone > 0
    assert 0 < rewound < today
    assert rewound_as_page == rewound
    assert _dispensed(opp) == today


def test_the_reversal_appears_on_the_day_the_rejection_was_read(seeded):
    opp = seeded["opp"]
    week_4 = date.fromisoformat(seeded["summary"]["weeks"][4]["week_of"])
    assert seeded["summary"]["weeks"][4]["reversed"] == 1
    for day, expected in ((week_4 + timedelta(days=5), 0), (week_4 + timedelta(days=6), 1)):
        with transaction.atomic():
            rewind(opp, end_of_day(day))
            found = Movement.objects.filter(program_id=opp, reverses__isnull=False).count()
            transaction.set_rollback(True)
        assert found == expected, day


def test_seeding_again_changes_nothing_unless_reset(seeded):
    opp = seeded["opp"]
    before = Movement.objects.filter(program_id=opp).count()
    with patch(FIXTURE_STORE, return_value=FakeStore(seeded["drive"])):
        again = seed(drive=seeded["drive"])
        assert again["seeded"] is False
        assert Movement.objects.filter(program_id=opp).count() == before
        reset = seed(drive=seeded["drive"], reset=True)
    assert reset["opportunity_id"] == opp and reset["seeded"] is True
    assert Movement.objects.filter(program_id=opp).count() == before
    assert SupplyPoint.objects.filter(program_id=opp, kind="user_held").count() == 20
    assert SyntheticOpportunity.objects.filter(labs_only=True).count() == 1


def test_the_command_seeds_through_drive_and_prints_the_summary(db, settings, capsys):
    from django.core.management import call_command

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    drive = FakeDrive()
    registry.invalidate_cache()
    with (
        patch("connect_labs.labs.synthetic.gdrive.DriveClient", return_value=drive),
        patch(FIXTURE_STORE, return_value=FakeStore(drive)),
    ):
        call_command("supply_seed_stock_from_visits")
    registry.invalidate_cache()
    printed = json.loads(capsys.readouterr().out)
    assert printed["seeded"] is True and printed["workers"] == 20
    assert DispensingRule.objects.filter(program_id=printed["opportunity_id"]).count() == len(SKUS)
