"""Record history for supply_chain: what ran, what changed, and the past.

Two append-only tables (`models.py`). `OperationCall` is one row per write
operation: actor, channel (web|mcp|api|command), the optional source it
rested on, and its stored result — unique on (program, operation, source_ref)
so the same evidence is recorded once and later replayed, not repeated.
`Revision` is one row per change to one record, including M2M link rows.

`capture.py` hooks `pre_save`/`post_save`/`pre_delete`/`post_delete` plus
`m2m_changed` on every concrete model and writes the `Revision`, attributed
to whatever `OperationCall` `context.py`'s `write_context` currently names —
opened by `calls.py`'s `run_recorded`, which is what `call_operation` uses to
dedupe on `source_ref`. A save outside any call still writes a revision,
unattributed; nothing escapes history.

`?as_of=YYYY-MM-DD` (`as_of.py`) renders a program page as it stood at the
end of that day: `rewind.py` undoes the program's later revisions
newest-first, as plain ORM writes with capture suspended, inside a
`transaction.atomic()` block the view always rolls back — so every existing
read path, including the SQL stock ledger, sees the past without being
rewritten for it. The market and portfolios are cross-program and stay live.

`context.py`'s `seed_overrides` lets a synthetic-program seeder backdate
`recorded_at` (and set actor/channel) so a replayed demo history reads as
though it happened when it says, gated on `scopes.is_synthetic` so a real
program's history always means what actually happened.

`labels.py` and `timeline.py` turn a `Revision` into what a program manager
reads. See docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md.
"""
