"""Contextvars that carry write provenance across a save, without threading
it through every call site.

Three independent knobs, each its own contextvar so they never interact:

- `write_context` names the `OperationCall` a save happens under (or `None`
  for a save outside any operation -- still captured, see `capture.py`).
- `capture_suspended` turns capture off entirely. Used by the as-of rewind
  (design doc §3.5), which replays old revisions as plain ORM writes and must
  not generate new ones for doing so.
- `seed_overrides` lets a synthetic-program seeder backdate `recorded_at` (and
  declare `actor`/`channel`) so a replayed history looks like it happened when
  it says it did, rather than "just now, by the seeder". Gated on
  `scopes.is_synthetic` -- a real program's history must always mean what it
  says.
"""

import contextvars
from contextlib import contextmanager

_call = contextvars.ContextVar("supply_history_call", default=None)
_suspended = contextvars.ContextVar("supply_history_suspended", default=False)
_overrides = contextvars.ContextVar("supply_history_overrides", default={})


@contextmanager
def write_context(call):
    """Attribute every save inside this block to `call` (an `OperationCall` or `None`)."""
    token = _call.set(call)
    try:
        yield call
    finally:
        _call.reset(token)


def current_call():
    return _call.get()


@contextmanager
def capture_suspended():
    """Turn off revision capture for the duration of the block."""
    token = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(token)


def is_suspended():
    return _suspended.get()


@contextmanager
def seed_overrides(program_id, *, actor=None, channel=None, recorded_at=None):
    """Seed-only: attribute and date writes as a replayed history would have them.

    Raises `PermissionError` unless `program_id` is a registered synthetic
    program -- a real program's revisions must always carry the moment they
    actually happened, not a seeder's chosen one.
    """
    from connect_labs.supply_chain.scopes import is_synthetic

    if not is_synthetic(program_id):
        raise PermissionError(f"seed overrides are only allowed on synthetic programs, not {program_id!r}")
    values = {
        k: v for k, v in {"actor": actor, "channel": channel, "recorded_at": recorded_at}.items() if v is not None
    }
    token = _overrides.set(values)
    try:
        yield
    finally:
        _overrides.reset(token)


def current_overrides():
    return _overrides.get()
