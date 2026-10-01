"""What the worker's own app says: its running balance, and the stock it says it received.

THIS REPOSITORY IS PUBLIC. Every username, id and figure here is invented.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import jsonschema
import pytest
from django.core.management import call_command

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, StockCount, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import network, soh
from connect_labs.supply_chain.stock.services.dispensing import validate_lines, validate_reports
from connect_labs.supply_chain.stock.services.ingest import extract_rows
from connect_labs.supply_chain.stock.services.visit_reader import ingest_visit_consumption

pytestmark = pytest.mark.django_db

PROGRAM = 10516
OPP = 10516
TODAY = date(2026, 9, 28)


def real_shaped(vid, answers, xform="xf-1", on="2026-09-20", username="worker-acacia", name="Stock Management"):
    body = {"@name": name}
    for path, value in answers.items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {
        "id": vid,
        "xform_id": xform,
        "username": username,
        "user_id": "uuid-a",
        "visit_date": on,
        "status": "pending",
        "flag_reason": {},
        "form_json": {"id": xform, "form": body},
    }


# ---- extract_rows, the old path -------------------------------------------


def test_extract_rows_reads_the_answer_under_form_json():
    rows = extract_rows(
        [real_shaped(9001, {"form.stock_balance.sachets_remaining": "40"}, xform="xf-9")],
        quantity_path="form.stock_balance.sachets_remaining",
    )
    assert rows == [
        {
            "connect_username": "worker-acacia",
            "quantity": "40",
            "counted_on": "2026-09-20",
            "form_submission_id": "xf-9",
            "visit_id": "9001",
        }
    ]


def test_a_top_level_path_reads_nothing_from_a_real_row():
    """The documented paths start `form.`; the old code applied them to the visit dict itself."""
    visit = real_shaped(9001, {"form.stock_balance.sachets_remaining": "40"})
    assert extract_rows([visit], quantity_path="stock_balance.sachets_remaining") == []


def test_a_row_with_no_xform_id_still_has_a_stable_submission_id():
    visit = real_shaped(9001, {"form.stock_balance.sachets_remaining": "40"})
    visit["xform_id"] = None
    del visit["form_json"]["id"]
    assert (
        extract_rows([visit], quantity_path="form.stock_balance.sachets_remaining")[0]["form_submission_id"]
        == "visit-9001"
    )


@pytest.fixture
def labs_only():
    from connect_labs.labs.synthetic import registry
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=OPP, program_id=PROGRAM, labs_only=True, enabled=True, label="worker reports"
    )
    registry.invalidate_cache()


def _stock_reports(program=PROGRAM, opportunity=OPP, *extra):
    call_command(
        "supply_ingest_stock_reports",
        "--program",
        str(program),
        "--opportunity",
        str(opportunity),
        "--commodity",
        "rutf",
        "--unit",
        "sachet",
        "--quantity-path",
        "form.stock_balance.sachets_remaining",
        *extra,
    )


def test_the_stock_report_command_reads_through_the_visit_source(labs_only):
    from connect_labs.supply_chain.management.commands import supply_ingest_stock_reports as command

    assert not hasattr(command, "ExportAPIClient")
    with (
        patch.object(command, "fetch_visits", return_value=[]) as fetch,
        patch.dict("os.environ", {"SUPPLY_EXPORT_TOKEN": "t"}),
    ):
        _stock_reports()
    fetch.assert_called_once_with(OPP, "t")


# ---- stock reports: labs-only, as the visit reader -------------------------


@pytest.mark.parametrize(
    "program, opportunity, reason",
    [
        (263, 2230, "programme 263"),  # a real programme
        (PROGRAM, 2230, "not a registered labs-only opportunity"),  # a real opportunity under a synthetic one
    ],
)
def test_the_stock_report_command_refuses_a_real_programme_before_it_reads_anything(
    labs_only, program, opportunity, reason
):
    from django.core.management.base import CommandError

    from connect_labs.supply_chain.management.commands import supply_ingest_stock_reports as command

    # A token is set, so the refusal is not the missing-token one.
    with (
        patch.object(command, "fetch_visits") as fetch,
        patch.dict("os.environ", {"SUPPLY_EXPORT_TOKEN": "t"}),
        pytest.raises(CommandError, match=reason),
    ):
        _stock_reports(program, opportunity)
    fetch.assert_not_called()
    assert not StockCount.objects.exists()


def test_the_stock_report_command_refuses_a_saved_export_of_a_real_programme(labs_only, tmp_path):
    from django.core.management.base import CommandError

    saved = tmp_path / "visits.json"
    saved.write_text('[{"username": "w", "form_json": {"form": {"stock_balance": {"sachets_remaining": "4"}}}}]')
    with pytest.raises(CommandError, match="refusing"):
        _stock_reports(263, 2230, "--from-json", str(saved))
    assert not StockCount.objects.exists()


def test_the_stock_report_command_checks_again_right_before_the_fetch(labs_only):
    """Whatever reaches the fetch, a real opportunity's visits are not read."""
    from django.core.management.base import CommandError

    from connect_labs.supply_chain.management.commands import supply_ingest_stock_reports as command

    with (
        patch.object(command, "fetch_visits") as fetch,
        patch.dict("os.environ", {"SUPPLY_EXPORT_TOKEN": "t"}),
        pytest.raises(CommandError, match="not a registered labs-only opportunity"),
    ):
        command.Command()._visits({"program": PROGRAM, "opportunity": 2230, "from_json": None})
    fetch.assert_not_called()


def _report_rows():
    return [{"connect_username": "w", "quantity": "4", "counted_on": "2026-09-20", "form_submission_id": "xf-1"}]


@pytest.mark.parametrize(
    "program, opportunity, reason",
    [(263, 2230, "programme 263"), (PROGRAM, 2230, "not a registered labs-only opportunity")],
)
def test_the_stock_report_operation_refuses_a_real_programme_and_writes_nothing(
    world, labs_only, program, opportunity, reason
):
    da = SupplyDataAccess(program_id=program, caller=SYSTEM)
    payload = {
        "rows": _report_rows(),
        "commodity_slug": "rutf",
        "quantity_unit": "sachet",
        "opportunity_id": opportunity,
        "create_missing_points": True,
    }
    with pytest.raises(ValueError, match=reason):
        call_operation("stock_report_ingest", da, payload)
    assert not StockCount.objects.exists()
    assert not SupplyPoint.objects.filter(kind="user_held").exists()


def test_the_stock_report_operation_records_a_labs_only_programmes_reports(world, labs_only):
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    payload = {
        "rows": _report_rows(),
        "commodity_slug": "rutf",
        "quantity_unit": "sachet",
        "opportunity_id": OPP,
        "item_id": world["item"].pk,
        "create_missing_points": True,
    }
    assert call_operation("stock_report_ingest", da, payload)["created"] == 1
    assert StockCount.objects.get().kind == "self_reported"


# ---- reported receipts are not counts --------------------------------------


@pytest.fixture
def world():
    commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}",
        slug="rutf",
        name="RUTF",
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    item = Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku="rutf",
        name="RUTF",
        commodity=commodity,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    store = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded"
    )
    return {"item": item, "store": store}


def _count(world, point, kind, quantity, on):
    return StockCount.objects.create(
        program_id=PROGRAM,
        supply_point=point,
        item=world["item"],
        commodity=world["item"].commodity,
        kind=kind,
        counted_on=on,
        quantity=Decimal(quantity),
        quantity_unit="sachet",
        source="commcare_form",
    )


def test_a_reported_receipt_never_becomes_the_last_count(world):
    worker = SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        slug="w",
        name="w",
        kind="user_held",
        connect_username="w",
        source="we_recorded",
    )
    _count(world, worker, "self_reported", 40, date(2026, 9, 1))
    _count(world, worker, "reported_receipt", 300, date(2026, 9, 10))

    assert soh.last_count(PROGRAM, worker, item=world["item"]).quantity == Decimal("40")
    assert network._latest_counts(PROGRAM, [worker], item=world["item"])[worker.pk].kind == "self_reported"


def test_a_receipt_cannot_be_typed_in_as_a_count(world):
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    with pytest.raises(jsonschema.ValidationError):
        call_operation(
            "stock_count_record",
            da,
            {
                "data": {
                    "supply_point_id": world["store"].pk,
                    "commodity_slug": "rutf",
                    "kind": "reported_receipt",
                    "counted_on": "2026-09-01",
                    "quantity": "1",
                    "quantity_unit": "sachet",
                    "source": "we_recorded",
                }
            },
        )


# ---- the reader records what the app reports -------------------------------

REPORTS = {
    "balance_paths": ["form.var.new_stock_balance", "form.stock_balance.sachets_remaining"],
    "receipt": {
        "quantity_paths": ["form.current_stock.sachets_received"],
        "date_paths": ["form.current_stock.date_received"],
    },
}


@pytest.fixture
def rule(world):
    item = world["item"]
    return DispensingRule.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        item=item,
        resupply_point=world["store"],
        active_from=date(2026, 8, 1),
        lines=validate_lines(
            [{"kind": "stated", "paths": ["form.rutf_dispensing.rutf_sachets_dispensed"], "unit": "sachet"}], item
        ),
        forms=["Visit Form"],
        reports=validate_reports(REPORTS),
    )


def read(visits):
    da = SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)
    return ingest_visit_consumption(da, opportunity_id=OPP, visits=visits, today=TODAY)


def test_the_apps_balance_is_recorded_as_a_self_reported_count(rule):
    visit = real_shaped(
        9001,
        {"form.rutf_dispensing.rutf_sachets_dispensed": "14", "form.var.new_stock_balance": "86"},
        xform="xf-9001",
        name="Visit Form",
    )

    report = read([visit])
    again = read([visit])

    count = StockCount.objects.get(kind="self_reported")
    assert (count.quantity, count.counted_on, count.form_submission_id, count.visit_id) == (
        Decimal("86"),
        date(2026, 9, 20),
        "xf-9001",
        "9001",
    )
    assert (report["balances_recorded"], again["balances_recorded"], again["reports_already_recorded"]) == (1, 0, 1)


def test_a_stock_management_form_records_both_its_receipt_and_its_balance(rule):
    visit = real_shaped(
        9002,
        {
            "form.current_stock.sachets_received": "300",
            "form.current_stock.date_received": "2026-09-18",
            "form.stock_balance.sachets_remaining": "320",
        },
        xform="xf-9002",
    )

    report = read([visit])

    receipt = StockCount.objects.get(kind="reported_receipt")
    assert (receipt.quantity, receipt.counted_on) == (Decimal("300"), date(2026, 9, 18))
    assert StockCount.objects.get(kind="self_reported").quantity == Decimal("320")
    assert (report["receipts_recorded"], report["balances_recorded"]) == (1, 1)
    # A receipt the worker reports is their account, never a ledger movement.
    assert not Movement.objects.filter(kind="distribution").exists()


def test_a_balance_of_zero_is_recorded_it_is_a_stockout(rule):
    read([real_shaped(9003, {"form.stock_balance.sachets_remaining": "0"}, xform="xf-9003")])
    assert StockCount.objects.get(kind="self_reported").quantity == Decimal("0")


def test_a_rejected_visits_balance_is_not_recorded(rule):
    visit = {
        **real_shaped(9004, {"form.stock_balance.sachets_remaining": "12"}, xform="xf-9004"),
        "status": "rejected",
    }
    report = read([visit])
    assert not StockCount.objects.exists()
    assert report["balances_recorded"] == 0


def test_a_rule_whose_item_lost_its_unit_is_refused_and_the_rest_of_the_run_goes_on(world, rule):
    other_commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}", slug="zinc", name="Zinc", base_unit="tablet"
    )
    other = Item.objects.create(
        scope_key=f"prog:{PROGRAM}", sku="zinc", name="Zinc", commodity=other_commodity, base_unit="tablet"
    )
    bad = DispensingRule.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        item=other,
        resupply_point=world["store"],
        active_from=date(2026, 8, 1),
        lines=validate_lines([{"kind": "stated", "paths": ["form.zinc.given"], "unit": "tablet"}], other),
        forms=["Visit Form"],
        reports=validate_reports(REPORTS),
    )
    Item.objects.filter(pk=other.pk).update(base_unit="")
    Commodity.objects.filter(pk=other_commodity.pk).update(base_unit="")
    visit = real_shaped(
        9005,
        {
            "form.rutf_dispensing.rutf_sachets_dispensed": "14",
            "form.var.new_stock_balance": "86",
            "form.zinc.given": "2",
        },
        xform="xf-9005",
        name="Visit Form",
    )

    report = read([visit])

    assert report["posted"] == 1
    assert report["balances_recorded"] == 1  # the sound rule only
    assert StockCount.objects.get(kind="self_reported").item == world["item"]
    assert [(row["rule_id"], row["item_id"]) for row in report["unit_refused"] if "rule_id" in row] == [
        (bad.pk, other.pk)
    ]


def test_latest_counts_loads_one_row_per_point(world):
    """The newest on-hand count per point -- and ONLY that row comes back from the database."""
    from django.db.models.signals import post_init

    workers = [
        SupplyPoint.objects.create(
            program_id=PROGRAM,
            opportunity_id=OPP,
            slug=f"w{n}",
            name=f"w{n}",
            kind="user_held",
            connect_username=f"w{n}",
            source="we_recorded",
        )
        for n in range(2)
    ]
    for day in range(1, 11):
        for worker in workers:
            _count(world, worker, "self_reported", 100 - day, date(2026, 9, day))
    _count(world, workers[0], "reported_receipt", 300, date(2026, 9, 20))
    _count(world, workers[1], "physical_count", 55, date(2026, 9, 10))  # same day, later id: wins

    loaded = []

    def seen(sender, instance, **kwargs):
        loaded.append(instance)

    post_init.connect(seen, sender=StockCount)
    try:
        latest = network._latest_counts(PROGRAM, workers, item=world["item"])
        as_of = network._latest_counts(PROGRAM, workers, item=world["item"], on_date=date(2026, 9, 4))
    finally:
        post_init.disconnect(seen, sender=StockCount)

    assert {w.name: (latest[w.pk].kind, latest[w.pk].quantity) for w in workers} == {
        "w0": ("self_reported", Decimal("90")),
        "w1": ("physical_count", Decimal("55")),
    }
    assert {w.name: as_of[w.pk].quantity for w in workers} == {"w0": Decimal("96"), "w1": Decimal("96")}
    assert len(loaded) == 4  # two points, two calls: one row each
