"""visit_consumption_ingest end to end: through the synthetic export client, as one recorded call.

THIS REPOSITORY IS PUBLIC. Every id and answer here is invented.
"""

from datetime import date
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django_celery_beat.models import PeriodicTask

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import visit_reader
from connect_labs.supply_chain.stock.services.dispensing import validate_lines

pytestmark = pytest.mark.django_db

OPP = 20871  # the programme is the opportunity's own
PATH = "form.rutf_dispensing.rutf_sachets_dispensed"
FETCH = "connect_labs.supply_chain.stock.services.visit_source.fetch_visits"


def export_record(vid, sachets, status="pending"):
    """One row as Connect's user_visits export (and a synthetic fixture) holds it."""
    return {
        "id": vid,
        "opportunity_id": OPP,
        "username": "worker-acacia",
        "user_id": "uuid-acacia",
        "visit_date": "2026-09-20",
        "status": status,
        "flag_reason": {},
        "status_modified_date": "2026-09-20T12:00:00",
        "form_json": {
            "id": f"xf-{vid}",
            "form": {"@name": "Visit Form", "rutf_dispensing": {"rutf_sachets_dispensed": str(sachets)}},
        },
        "images": [],
    }


@pytest.fixture
def synthetic():
    SyntheticOpportunity.objects.create(
        opportunity_id=OPP, labs_only=True, enabled=True, label="reader tests", gdrive_folder_id="folder-test"
    )
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


@pytest.fixture
def da(synthetic):
    return SupplyDataAccess(program_id=OPP, opportunity_id=OPP, caller=SYSTEM)


@pytest.fixture
def rule(da):
    commodity = Commodity.objects.create(
        scope_key=f"prog:{OPP}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150
    )
    item = Item.objects.create(
        scope_key=f"prog:{OPP}",
        sku="rutf",
        name="RUTF",
        commodity=commodity,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    store = SupplyPoint.objects.create(
        program_id=OPP, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded"
    )
    return DispensingRule.objects.create(
        program_id=OPP,
        opportunity_id=OPP,
        item=item,
        resupply_point=store,
        active_from=date(2026, 8, 1),
        lines=validate_lines([{"kind": "stated", "paths": [PATH], "unit": "sachet"}], item),
    )


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def load_endpoint(self, opp_id, endpoint_key):
        self.calls.append((opp_id, endpoint_key))
        return self.rows if endpoint_key == "user_visits" else []


def test_the_reader_reads_through_the_synthetic_export_client(da, rule):
    store = FakeStore([export_record(9001, 14)])
    with patch("connect_labs.labs.integrations.connect.factory._get_fixture_store", return_value=store):
        report = call_operation("visit_consumption_ingest", da, {"opportunity_id": OPP})

    assert report["posted"] == 1
    assert (OPP, "user_visits") in store.calls
    assert Movement.objects.get(kind="consumption").visit_id == "9001"


def test_the_run_is_one_recorded_call_and_its_rows_carry_revisions(da, rule):
    with patch(FETCH, return_value=[export_record(9001, 14)]):
        call_operation("visit_consumption_ingest", da, {"opportunity_id": OPP})

    call = OperationCall.objects.get(operation="visit_consumption_ingest")
    movement = Movement.objects.get(kind="consumption")
    worker = SupplyPoint.objects.get(kind="user_held")
    remembered = WorkerVisit.objects.get(visit_id="9001")
    for row in (movement, worker, remembered):
        assert Revision.objects.filter(call=call, object_id=str(row.pk), action="create").exists()


def test_until_and_refresh_reach_the_reader_and_the_source(da, rule):
    with patch(FETCH, return_value=[export_record(9001, 14)]) as fetch:
        report = call_operation(
            "visit_consumption_ingest", da, {"opportunity_id": OPP, "until": "2026-09-19", "refresh": True}
        )

    assert fetch.call_args.kwargs == {"force_refresh": True}
    assert (report["posted"], report["after_until"]) == (0, 1)


def test_a_real_programme_is_refused(rule):
    real = SupplyDataAccess(program_id=263, opportunity_id=2230, caller=SYSTEM)
    with patch(FETCH) as fetch, pytest.raises(ValueError, match="refusing"):
        call_operation("visit_consumption_ingest", real, {"opportunity_id": 2230})
    fetch.assert_not_called()


def test_the_command_dry_run_keeps_nothing(da, rule, capsys):
    with patch(FETCH, return_value=[export_record(9001, 14)]):
        call_command("supply_ingest_visit_consumption", "--program", str(OPP), "--opportunity", str(OPP), "--dry-run")

    assert "would post 1" in capsys.readouterr().out
    assert not Movement.objects.filter(kind="consumption").exists()
    assert not WorkerVisit.objects.exists()
    assert not OperationCall.objects.filter(operation="visit_consumption_ingest").exists()


def test_the_command_posts(da, rule, capsys):
    with patch(FETCH, return_value=[export_record(9001, 14)]):
        call_command("supply_ingest_visit_consumption", "--program", str(OPP), "--opportunity", str(OPP))
    assert "posted 1" in capsys.readouterr().out
    assert Movement.objects.filter(kind="consumption").count() == 1


def test_the_scheduled_run_reads_synthetic_programmes_and_skips_real_ones(da, rule):
    real_store = SupplyPoint.objects.create(
        program_id=263, slug="real-store", name="Real store", kind="regional_store", source="we_recorded"
    )
    DispensingRule.objects.create(
        program_id=263,
        opportunity_id=2230,
        item=rule.item,
        resupply_point=real_store,
        active_from=date(2026, 8, 1),
        lines=rule.lines,
    )

    with patch(FETCH, return_value=[export_record(9001, 14)]) as fetch:
        results = visit_reader.run_scheduled()

    assert results[str(OPP)]["posted"] == 1
    assert results["2230"] == {"skipped": "not a synthetic programme"}
    assert [c.args[0] for c in fetch.call_args_list] == [OPP]


def test_one_failing_opportunity_does_not_stop_the_scheduled_run(da, rule):
    with patch(FETCH, side_effect=RuntimeError("export unavailable")):
        results = visit_reader.run_scheduled()
    assert results[str(OPP)] == {"error": "export unavailable"}


def test_the_beat_task_runs_the_scheduled_reader():
    from connect_labs.supply_chain.tasks import ingest_visit_consumption

    with patch.object(visit_reader, "run_scheduled", return_value={"ok": True}) as run:
        assert ingest_visit_consumption() == {"ok": True}
    run.assert_called_once_with()


def test_the_beat_schedule_exists():
    """Runs the migration's own function rather than trusting the seeded row: a
    TransactionTestCase elsewhere can flush the table (as test_alerts notes)."""
    import importlib

    importlib.import_module(
        "connect_labs.supply_chain.migrations.0039_seed_visit_consumption_beat_task"
    ).create_periodic_task(None, None)
    task = PeriodicTask.objects.get(name="supply_chain_visit_consumption")
    assert task.task == "connect_labs.supply_chain.tasks.ingest_visit_consumption"
    assert (task.interval.every, task.interval.period) == (1, "hours")


@pytest.mark.parametrize("bad", ["2026/09/19", "yesterday", "2026-9-19", "20260919", "", "2026-09-19T00:00"])
def test_a_bad_until_is_refused_before_anything_is_read(da, rule, bad):
    with patch(FETCH) as fetch, pytest.raises(ValueError, match="is not a YYYY-MM-DD day"):
        call_operation("visit_consumption_ingest", da, {"opportunity_id": OPP, "until": bad})
    fetch.assert_not_called()
    assert not OperationCall.objects.filter(operation="visit_consumption_ingest").exists()


def test_the_command_refuses_a_bad_until(da, rule):
    from django.core.management.base import CommandError

    with patch(FETCH) as fetch, pytest.raises(CommandError, match="is not a YYYY-MM-DD day"):
        call_command(
            "supply_ingest_visit_consumption",
            "--program",
            str(OPP),
            "--opportunity",
            str(OPP),
            "--until",
            "21/09/2026",
        )
    fetch.assert_not_called()


def test_the_scheduled_run_reverses_under_a_rule_since_switched_off(da, rule):
    with patch(FETCH, return_value=[export_record(9001, 14)]):
        visit_reader.run_scheduled()
    DispensingRule.objects.filter(pk=rule.pk).update(status="inactive")

    with patch(FETCH, return_value=[export_record(9001, 14, status="rejected")]):
        results = visit_reader.run_scheduled()

    assert results[str(OPP)]["reversed"] == 1
    assert Movement.objects.filter(visit_id="9001", reverses__isnull=False).count() == 1
