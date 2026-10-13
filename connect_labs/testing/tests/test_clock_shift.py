"""`--clock-shift`: where the clock starts, and that a shifted clock is a real one."""

import datetime as dt

import pytest

from connect_labs.testing.clock_shift import destination

NOW = dt.datetime(2026, 10, 13, 5, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("+30d", dt.datetime(2026, 11, 12, 5, 0, tzinfo=dt.UTC)),
        ("-1d", dt.datetime(2026, 10, 12, 5, 0, tzinfo=dt.UTC)),
        ("+20h", dt.datetime(2026, 10, 14, 1, 0, tzinfo=dt.UTC)),
        ("2026-12-31T23:40", dt.datetime(2026, 12, 31, 23, 40, tzinfo=dt.UTC)),
        ("2027-01-01", dt.datetime(2027, 1, 1, tzinfo=dt.UTC)),
    ],
)
def test_where_the_clock_starts(spec, expected):
    assert destination(spec, NOW) == expected


def test_a_spec_it_cannot_read_is_refused_rather_than_ignored():
    with pytest.raises(ValueError):
        destination("next tuesday", NOW)


def test_a_shifted_clock_moves_python_and_keeps_its_types_real():
    import time

    import time_machine
    from django.utils import timezone

    before = dt.date.today()
    with time_machine.travel(destination("+400d", dt.datetime.now(dt.UTC)), tick=True):
        today = dt.date.today()
        assert type(today) is dt.date and type(timezone.now()) is dt.datetime
        assert (today - before).days in (399, 400, 401)
        assert dt.date.fromtimestamp(time.time()) == today
        assert abs((timezone.now().date() - today).days) <= 1
