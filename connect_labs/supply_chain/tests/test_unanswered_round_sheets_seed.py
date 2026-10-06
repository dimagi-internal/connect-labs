"""The unanswered-round seeder, as the supply-sophie-sheets narrative uses it.

THIS REPOSITORY IS PUBLIC. Everything the replay writes is invented.

Two things the sheets story needs from the shared seed: a clearing day for the
held shipment that differs from the one already seeded (typed into its Expected
cell, so the same day would change nothing), and -- under `--sheets` only --
Kanem's email stating the pack its AI-recorded quote leaves out.
"""

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[3] / "scripts/walkthroughs/supply-sophie-unanswered-round"


def _load(name, filename):
    sys.path.insert(0, str(HERE.parent))
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def replay():
    return _load("unanswered_round_replay_sheets", "replay.py")


@pytest.fixture(scope="module")
def seed():
    return _load("unanswered_round_seed_sheets", "seed.py")


def _kanem_excerpt():
    from connect_labs.supply_chain.history.models import OperationCall

    return (
        OperationCall.objects.filter(operation="quote_record", source_ref__contains="CAK9q2719").get().source_excerpt
    )


def test_the_clearing_day_follows_the_render_day(replay):
    assert replay.forwarder_clear_dates(dt.date(2026, 10, 2)) == {
        "forwarder_clear_date_iso": "2026-10-14",
        "forwarder_clear_date": "14 Oct",
    }
    assert replay.forwarder_clear_dates(dt.date(2026, 11, 20))["forwarder_clear_date"] == "2 Dec"


@pytest.mark.django_db
def test_the_clearing_day_is_later_than_the_shipments_seeded_eta(replay):
    from connect_labs.supply_chain.models import Shipment

    today = dt.date.today() - dt.timedelta(days=40)
    replay.ensure_program()
    out = replay.seed_world(create_buyer=True, today=today)
    shipment = Shipment.objects.get(contract_id=out["contract_id"])
    clear = dt.date.fromisoformat(out["forwarder_clear_date_iso"])
    assert shipment.expected_on is not None and clear > shipment.expected_on
    assert (clear - today).days == 12
    assert out["forwarder_clear_date"] == f"{clear.day} {clear:%b}"


@pytest.mark.django_db
def test_without_sheets_kanems_email_is_unchanged(replay):
    replay.ensure_program()
    replay.seed_world(create_buyer=True, today=dt.date.today())
    assert replay.KANEM_PACK_SENTENCE not in _kanem_excerpt()


@pytest.mark.django_db
def test_under_sheets_the_email_states_the_pack_the_ai_left_out(replay):
    from connect_labs.supply_chain.models import Quote

    replay.ensure_program()
    replay.seed_world(create_buyer=True, today=dt.date.today(), sheets=True)
    assert _kanem_excerpt().endswith("Pro-forma KF/Q/2719 attached: cartons of 150 x 92 g sachets.")
    quote = Quote.objects.get(supplier_reference="KF/Q/2719")
    assert quote.pack_spec_source == "not_stated"
    assert quote.base_per_pack_stated is None


def test_only_a_sheets_seed_names_the_keyword_on_the_server(seed):
    assert 'replay["run"](mint=True, **{})' in seed._driver("")
    assert "replay[\"run\"](mint=True, **{'sheets': True})" in seed._driver("", sheets=True)
