"""The daily Sheet check: it runs, it remembers, and it says so on the models page."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from django.conf import settings
from django.core.cache import cache

from connect_labs.effectiveness import engine, sheet_drift, tasks


def report(model, in_sync):
    return {"model": model, "in_sync": in_sync, "cells_compared": 10, "differing": [], "rows_missing_in_live": []}


@pytest.fixture(autouse=True)
def clean_cache():
    cache.delete(sheet_drift.STATUS_KEY)
    yield
    cache.delete(sheet_drift.STATUS_KEY)


def test_the_sheet_id_is_read_from_the_url():
    assert sheet_drift.sheet_id("https://docs.google.com/spreadsheets/d/abc_123-X/edit#gid=0") == "abc_123-X"


def test_check_all_reads_every_model_once(monkeypatch):
    seen = []
    monkeypatch.setattr(sheet_drift.sheets_check, "compare", lambda model, rows: report(model, True) | {"rows": rows})
    got = sheet_drift.check_all(read=lambda sid: seen.append(sid) or [["x"]])
    assert [r["model"] for r in got] == list(engine.MODELS)
    assert len(seen) == len(engine.MODELS)


def test_nothing_is_said_before_a_check_has_run():
    assert sheet_drift.status() is None
    assert sheet_drift.drift_note() == ""


def test_in_sync_leaves_no_note():
    status = sheet_drift.record([report("chc", True)])
    assert status["drifted"] == []
    assert sheet_drift.drift_note() == ""


def test_a_drifted_model_is_named_with_the_date_checked():
    sheet_drift.record(
        [report("chc", False), report("kc", True)], now=datetime(2026, 11, 17, 6, 12, tzinfo=timezone.utc)
    )
    note = sheet_drift.drift_note()
    assert "chc" in note and "kc" not in note
    assert "2026-11-17" in note


def test_the_task_logs_an_error_on_drift_and_returns_the_models(monkeypatch, caplog):
    monkeypatch.setattr(sheet_drift, "check_all", lambda: [report("chc", False)])
    with caplog.at_level(logging.ERROR, logger=tasks.logger.name):
        assert tasks.check_model_sheets() == ["chc"]
    assert any("chc" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


def test_the_task_is_quiet_when_in_sync(monkeypatch, caplog):
    monkeypatch.setattr(sheet_drift, "check_all", lambda: [report("chc", True)])
    with caplog.at_level(logging.ERROR, logger=tasks.logger.name):
        assert tasks.check_model_sheets() == []
    assert not caplog.records


def test_a_sheet_that_cannot_be_read_fails_the_task_rather_than_passing_it(monkeypatch):
    def boom():
        raise RuntimeError("403 from Sheets")

    monkeypatch.setattr(sheet_drift, "check_all", boom)
    with pytest.raises(RuntimeError):
        tasks.check_model_sheets()


def test_the_check_is_scheduled_daily():
    entry = settings.CELERY_BEAT_SCHEDULE["effectiveness-check-model-sheets"]
    assert entry["task"] == "connect_labs.effectiveness.tasks.check_model_sheets"
    assert entry["schedule"].hour == {6}
