"""Unanswered round v11: the replay and the `--sahel-replies` hook share ONE render day.

THIS REPOSITORY IS PUBLIC. Everything the replay writes is invented.

Both used `date.today()` -- the machine's calendar -- while the app dates
everything in its TIME_ZONE (UTC). Either side of midnight the two disagree,
and Sahel's reply was recorded a day BEFORE the chase it answers. Both now
anchor to `timezone.localdate()`.
"""

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

HERE = Path(__file__).resolve().parents[3] / "scripts/walkthroughs/supply-sophie-unanswered-round"


def _load(name, filename):
    sys.path.insert(0, str(HERE.parent))
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def replay():
    return _load("unanswered_round_replay_v11", "replay.py")


@pytest.fixture(scope="module")
def seed():
    return _load("unanswered_round_seed_v11", "seed.py")


# 23:30 on 1 Oct in Los Angeles is 06:30 on 2 Oct in UTC: the machine's calendar and the app's disagree.
_LATE_IN_LA = dt.datetime(2026, 10, 2, 6, 30, tzinfo=dt.UTC)


def test_replay_and_hook_compute_the_same_today(replay, seed):
    assert replay.story_today() == seed.story_today() == timezone.localdate()


def test_the_hook_without_django_reads_the_apps_zone(seed, settings):
    assert seed.APP_TIME_ZONE == settings.TIME_ZONE
    real = dt.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return _LATE_IN_LA.astimezone(tz) if tz else _LATE_IN_LA.astimezone(ZoneInfo("America/Los_Angeles"))

    fake_settings = mock.Mock(configured=False)
    with mock.patch.object(seed.dt, "datetime", Frozen), mock.patch("django.conf.settings", fake_settings):
        assert seed.story_today() == dt.date(2026, 10, 2)


def test_across_midnight_both_say_the_apps_day(replay, seed):
    with mock.patch.object(timezone, "now", return_value=_LATE_IN_LA):
        assert replay.story_today() == seed.story_today() == dt.date(2026, 10, 2)


@pytest.mark.django_db
def test_the_reply_never_predates_the_chase(replay, seed):
    """Seed on the render day, then record Sahel's reply as the local hook does: replied on or after the chase."""
    from connect_labs.supply_chain.models import Outreach

    replay.ensure_program()
    out = replay.seed_world(create_buyer=True)
    sahel = Outreach.objects.get(id=out["sahel_outreach_id"])
    chased = sahel.last_reminder_on
    world = replay.World(out["program_id"], replay.personas())
    today = seed.story_today()
    world.email(
        today.isoformat(),
        "outreach_update",
        **seed.SAHEL_REPLY,
        outreach_id=out["sahel_outreach_id"],
        data={"responded": True, "response_kind": "quote", "responded_on": today.isoformat()},
    )
    sahel.refresh_from_db()
    assert sahel.responded_on >= chased
    assert sahel.responded_on == timezone.localdate()
