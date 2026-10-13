"""`pytest --clock-shift=+30d`: run the suite as if today were another day.

A test that passes only on some dates -- a weekday, a fixed date still inside a
"last 90 days" window, a year that has not turned yet -- passes in CI right up
until the calendar breaks it (main went red on 2026-10-13 for exactly that).
This moves the clock so those tests fail now, on purpose, where they can be
fixed. CI runs it after every merge and nightly (`pytest-shifted-clock`).

It moves Python's wall clock -- `date.today()`, `datetime.now()`,
`timezone.now()`, `time.time()` -- with time-machine, which patches the C-level
clock functions, so every date and datetime is the real type (freezegun's
stand-in classes are what made an earlier attempt unreadable). The clock keeps
ticking from the shifted start. Monotonic clocks, sleeps and timeouts are left
alone, and so are subprocesses and Postgres: product code must take "now" from
the app and pass it into SQL, never call now() / CURRENT_DATE in a query.

libfaketime was tried first. On macOS it works; on the Linux runner it made
every time.sleep() fail with EINVAL, or, faking the monotonic clock too, hung
the run at 99% -- and it moved node and every other subprocess with it.

The shift is `+<N>d` (or `-<N>d`, or hours with `h`), or an ISO date/datetime
to start at, e.g. `--clock-shift=2026-12-31T23:40`. Each xdist worker starts
its own clock from the same option, a second or two apart.
"""

from __future__ import annotations

import datetime as dt
import re

_RELATIVE = re.compile(r"^([+-])(\d+)([dh])$")


def pytest_addoption(parser):
    parser.addoption(
        "--clock-shift",
        default=None,
        help="Run with the wall clock moved: +30d, -1d, +12h, or a start like 2026-12-31T23:40 (UTC).",
    )


def destination(spec: str, now: dt.datetime) -> dt.datetime:
    """Where the clock starts for `spec`, from the real `now` (aware, UTC)."""
    match = _RELATIVE.match(spec.strip())
    if match:
        sign, amount, unit = match.groups()
        delta = dt.timedelta(days=int(amount)) if unit == "d" else dt.timedelta(hours=int(amount))
        return now + delta if sign == "+" else now - delta
    start = dt.datetime.fromisoformat(spec.strip())
    return start if start.tzinfo else start.replace(tzinfo=dt.UTC)


def pytest_configure(config):
    spec = config.getoption("--clock-shift", default=None)
    if not spec:
        return
    import time_machine

    target = destination(spec, dt.datetime.now(dt.UTC))
    traveller = time_machine.travel(target, tick=True)
    traveller.start()
    config._clock_shift_traveller = traveller


def pytest_report_header(config):
    if config.getoption("--clock-shift", default=None):
        return f"clock shifted ({config.getoption('--clock-shift')}): today is {dt.date.today():%A %Y-%m-%d}"


def pytest_unconfigure(config):
    traveller = getattr(config, "_clock_shift_traveller", None)
    if traveller is not None:
        traveller.stop()
