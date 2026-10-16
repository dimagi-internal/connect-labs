"""An LLO buys a phone for every adult in three villages and hands each over at a visit.

THIS REPOSITORY IS PUBLIC. Everything seeded here is invented.
"""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import transaction
from django.test import override_settings

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.fixture_store import ENDPOINT_FILES
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.demo import phones
from connect_labs.supply_chain.models import Contract, Movement, ReceiptLine, SupplyPoint, Tender
from connect_labs.supply_chain.runner import running_org

START = date(2026, 9, 14)
TODAY = date(2026, 10, 12)


# ---- the world, before any database ----------------------------------------


def test_no_worker_hands_over_more_than_they_were_issued():
    world = phones.build_world(START)
    issued = {w: sum(i["lines"].get(w, 0) for i in world.issues) for w in phones.WORKERS}
    given = {w: sum(1 for v in world.visits if v["username"] == w) for w in phones.WORKERS}
    assert all(given[w] <= issued[w] for w in phones.WORKERS)


def test_the_issues_never_exceed_what_passed_inspection():
    world = phones.build_world(START)
    issued = sum(q for i in world.issues for q in i["lines"].values())
    assert issued == phones.ADULTS - phones.REJECTED


def test_the_handover_is_still_under_way():
    world = phones.build_world(START)
    assert 0 < len(world.visits) < phones.ADULTS - phones.REJECTED


def test_every_handover_gives_one_phone_with_a_valid_invented_imei():
    world = phones.build_world(START)
    for visit in world.visits:
        handover = visit["form_json"]["form"]["handover"]
        assert handover["phones_given"] == "1"
        imei = handover["imei"]
        assert len(imei) == 15 and imei.startswith("99")
        digits = [int(c) for c in imei]
        total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(reversed(digits)))
        assert total % 10 == 0


def test_one_worker_counts_one_phone_fewer_than_they_hold():
    world = phones.build_world(START)
    last = {}
    for visit in world.visits:
        last[visit["username"]] = visit
    issued = {w: sum(i["lines"].get(w, 0) for i in world.issues) for w in phones.WORKERS}
    given = {w: sum(1 for v in world.visits if v["username"] == w) for w in phones.WORKERS}
    for w, visit in last.items():
        reported = int(visit["form_json"]["form"]["var"]["phones_remaining"])
        assert reported == issued[w] - given[w] - (1 if w == phones.ONE_SHORT else 0)


def test_the_world_is_deterministic():
    assert phones.build_world(START).visits == phones.build_world(START).visits


# ---- seeded through the real operations ------------------------------------


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
    def __init__(self, drive):
        self.drive = drive

    def load_endpoint(self, opp_id, endpoint_key):
        folder = SyntheticOpportunity.objects.get(opportunity_id=opp_id).gdrive_folder_id
        return self.drive.files.get((folder, ENDPOINT_FILES[endpoint_key]), [])

    def reload(self, opp_id):
        return None


@pytest.fixture(scope="module")
def _seeded_once(django_db_setup, django_db_blocker):
    """Seed the phones world ONCE for this module, inside an outer transaction rolled back at the end.

    Each test still runs in its own savepoint (the `db` fixture nests inside this), so a
    test's writes -- the reset re-seed included -- roll back as before.
    """
    drive = FakeDrive()
    with django_db_blocker.unblock(), override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="parent"):
        outer = transaction.atomic()
        outer.__enter__()
        try:
            registry.invalidate_cache()
            with (
                patch(
                    "connect_labs.labs.integrations.connect.factory._get_fixture_store",
                    return_value=FakeStore(drive),
                ),
                patch("connect_labs.labs.synthetic.provisioning.invalidate_synthetic_caches"),
            ):
                summary = phones.seed(drive=drive, today=TODAY)
            yield {"drive": drive, "summary": summary, "program_id": summary["program_id"]}
        finally:
            transaction.set_rollback(True)
            outer.__exit__(None, None, None)
            registry.invalidate_cache()


@pytest.fixture
def seeded(_seeded_once, db, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    registry.invalidate_cache()
    yield _seeded_once
    registry.invalidate_cache()


@pytest.mark.real_standing
def test_the_llo_runs_its_own_procurement(seeded):
    program_id = seeded["program_id"]
    assert running_org(program_id).slug == "labs-synthetic-demo-phones"
    assert Tender.objects.filter(program_id=program_id).count() == 1
    order = Contract.objects.get(program_id=program_id)
    assert order.buyer_org_id == running_org(program_id).pk


@pytest.mark.real_standing
def test_six_phones_were_rejected_at_inspection(seeded):
    line = ReceiptLine.objects.get(receipt__contract__program_id=seeded["program_id"])
    assert line.quantity_rejected == phones.REJECTED
    assert line.quantity_accepted == phones.ADULTS - phones.REJECTED


@pytest.mark.real_standing
def test_every_handover_was_read_into_the_ledger(seeded):
    program_id = seeded["program_id"]
    posted = sum(w["posted"] for w in seeded["summary"]["weeks"])
    assert posted == seeded["summary"]["handovers"]
    workers = SupplyPoint.objects.filter(program_id=program_id, kind="user_held")
    assert workers.count() == len(phones.WORKERS)
    assert Movement.objects.filter(program_id=program_id, kind="consumption").exists()


@pytest.mark.real_standing
def test_seeding_again_changes_nothing_unless_reset(seeded):
    with (
        patch(
            "connect_labs.labs.integrations.connect.factory._get_fixture_store",
            return_value=FakeStore(seeded["drive"]),
        ),
        patch("connect_labs.labs.synthetic.provisioning.invalidate_synthetic_caches"),
    ):
        again = phones.seed(drive=seeded["drive"], today=TODAY)
        assert again["seeded"] is False
        reset = phones.seed(drive=seeded["drive"], reset=True, today=TODAY)
        assert reset["seeded"] is True and reset["program_id"] == seeded["program_id"]
    assert Tender.objects.filter(program_id=seeded["program_id"]).count() == 1


def test_quantities_are_whole_phones():
    world = phones.build_world(START)
    for issue in world.issues:
        assert all(isinstance(q, int) and q >= 0 for q in issue["lines"].values())
    assert Decimal(phones.ADULTS) == Decimal(1200)


@pytest.mark.real_standing
def test_the_programme_map_shows_only_what_the_story_means_it_to(seeded):
    """A tidy demo: the open checks are the store's six phones short and the worker one phone short.

    The map's panel draws every check as a blocker. It read six -- an order 30-odd days
    "late" that had arrived (refusals counted as undelivered), a VAT with no amount, a
    delivery and a consignment with no paperwork -- on a demo that has two real stories,
    and "1 never reported stock" for an office store nobody had counted.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.checks import run_checks
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.stock.services.visit_source import SYNTHETIC_TOKEN
    from connect_labs.supply_chain.summary import chain_summary

    program_id = seeded["program_id"]
    access = SupplyDataAccess(
        access_token=SYNTHETIC_TOKEN, program_id=program_id, opportunity_id=program_id, caller=SYSTEM
    )
    kinds = sorted(c["kind"] for c in run_checks(access, as_of=TODAY))
    assert "contract_delivery_overdue" not in kinds
    assert set(kinds) <= {"stock_stockout", "stock_variance"}, kinds
    network = chain_summary(access)["deliver"]["network"]
    assert network["never_reported"] == 0
