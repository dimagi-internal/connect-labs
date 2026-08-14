"""Which workflow templates can have a dead run resumed, and how.

A run whose worker died mid-job (a deploy cutover, a crash, an OOM) can only be
re-fired safely if its job handler is IDEMPOTENT — able to recognise the work a
previous invocation already finished and pick up from there. That is a property
of each individual handler, not of the job runner, so it cannot be assumed: a
handler that simply redoes everything would, on resume, duplicate whatever it
had already written.

This registry is the explicit list of handlers that have been audited for it.
Both entry points read it — the manual MCP tool and the periodic stale-run sweep
— so adding a template is one registration here plus that template's own
idempotency work, rather than a template name hardcoded in each caller.

``weekly_dual_track_audit`` qualifies because its handler skips any
(opportunity, tag, window) call already COMPLETED for the run, and anything left
unfinished re-enters ``run_audit_creation``, which recognises its own sessions
and lets the per-session review/detection stages resume from their own
completion flags. See ``connect_labs.audit.run_checkpoints``.
"""

from __future__ import annotations

# template_type -> resume callable(definition, run, *, access_token, force=False)
_RESUME_HANDLERS: dict[str, str] = {
    "weekly_dual_track_audit": "connect_labs.workflow.audit_generation.resume_batch_run",
}


def is_resumable(template_type: str | None) -> bool:
    return template_type in _RESUME_HANDLERS


def resumable_template_types() -> frozenset[str]:
    return frozenset(_RESUME_HANDLERS)


def resume_handler_for(template_type: str | None):
    """The resume callable for a template, or None if it has none.

    Resolved by dotted path at call time rather than imported at module scope:
    the handlers live in modules that import the workflow task layer, which
    imports this registry.
    """
    path = _RESUME_HANDLERS.get(template_type)
    if not path:
        return None
    module_path, _, attr = path.rpartition(".")
    from importlib import import_module

    return getattr(import_module(module_path), attr)
