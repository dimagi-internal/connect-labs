"""The ensure engine: an ordered dispatcher that realizes an env manifest.

:func:`ensure_synthetic_data` loads an :class:`~.env_manifest.EnvManifest`,
resolves its week window once, then walks the declared resources *in order*,
dispatching each to its registered ensurer by ``resource.kind``. A single
:class:`EnsureContext` is threaded through every call so ensurers can read the
window, see ids realized by earlier resources, and contribute to the cumulative
``realized`` map. The realized map is the engine's output — optionally written
to ``realized.json`` — and is what later steps (and the recorder) read to know
what actually exists.

The per-kind ensurers themselves live in later tasks; this module only owns the
ordering, context, and output contract.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .ensurers.campaign import ensure_campaign
from .ensurers.opp_data import ensure_opp_data
from .ensurers.rollup import ensure_rollup
from .ensurers.run_audits import ensure_run_audits
from .ensurers.tasks import ensure_tasks
from .ensurers.weekly_runs import ensure_weekly_runs
from .env_manifest import EnvManifest
from .registry import ENVS_DIR, get_env_path
from .window import resolve_window

__all__ = ["EnsureContext", "ENSURERS", "ENVS_DIR", "resolve_env_path", "ensure_synthetic_data"]


@dataclass
class EnsureContext:
    """Threaded through every ensurer call for one ``ensure_synthetic_data`` run.

    ``weeks`` / ``current_week`` are the resolved PAR window (trailing complete
    Mondays) and the in-progress week's Monday (or ``None``). ``env_dir`` is the
    directory of the env manifest, so ensurers can resolve relative paths (e.g.
    a per-opp generator manifest). ``ids`` accumulates ids realized by earlier
    resources for later ones to reference; ``realized`` is the cumulative output
    map returned by the run.
    """

    weeks: list[str] = field(default_factory=list)
    current_week: str | None = None
    env_dir: Path | None = None
    ids: dict = field(default_factory=dict)
    realized: dict = field(default_factory=dict)


# Maps ``resource.kind`` -> ensurer callable ``(resource, ctx) -> dict | None``.
# The ensurer modules import from env_manifest/generator/workflow — never from
# this module — so a top-of-file import here is safe (no cycle). Kept a plain
# module-level dict so existing tests can monkeypatch ``engine.ENSURERS``.
ENSURERS: dict[str, Callable] = {
    "opp_data": ensure_opp_data,
    "weekly_runs": ensure_weekly_runs,
    "run_audits": ensure_run_audits,
    "tasks": ensure_tasks,
    "rollup": ensure_rollup,
    "campaign": ensure_campaign,
}


# Env name -> manifest path resolution (and ``ENVS_DIR``) is centralized in
# ``registry.py``. ``resolve_env_path`` is kept as a thin alias of the
# registry's ``get_env_path`` so existing callers (and ``ensure_synthetic_data``)
# keep working unchanged.
resolve_env_path = get_env_path


# LabsLocalRecord types the ensurers regenerate on every run. A ``fresh`` rebuild
# wipes exactly these for the env's opps before re-seeding, so records stranded by
# a prior window (e.g. duplicated audits/tasks, flags glued to a slid-past week)
# are cleared. Scaffolding that must keep stable ids — ``workflow_definition``,
# ``workflow_render_code``, ``pipeline_definition`` — is deliberately NOT listed.
_FRESH_RESET_TYPES = ("workflow_run", "Flag", "AuditSession", "Task")


def _env_opportunity_ids(em: EnvManifest) -> set[int]:
    """Every opportunity_id the manifest's resources touch (singular + plural)."""
    opps: set[int] = set()
    for r in em.resources:
        oid = getattr(r, "opportunity_id", None)
        if oid is not None:
            opps.add(int(oid))
        for oid in getattr(r, "opportunity_ids", None) or []:
            opps.add(int(oid))
    return opps


def _reset_env_records(em: EnvManifest) -> int:
    """Delete the regenerable records for this env's opps; return the deleted count.

    Local import of the model keeps the engine importable without Django set up
    (mirrors how the ensurers defer their backend imports).
    """
    from commcare_connect.labs.synthetic.models import LabsLocalRecord

    opps = _env_opportunity_ids(em)
    if not opps:
        return 0
    deleted, _ = LabsLocalRecord.objects.filter(opportunity_id__in=opps, type__in=_FRESH_RESET_TYPES).delete()
    return deleted


def ensure_synthetic_data(env_path: str, out: str | None = None, *, fresh: bool = False) -> dict:
    """Realize the env manifest at ``env_path``; return the cumulative realized map.

    Walks the manifest's resources in declared order, dispatching each by
    ``kind`` to :data:`ENSURERS`. Raises ``KeyError`` if a resource declares a
    kind with no registered ensurer. If ``out`` is given, writes the realized
    map there as indented JSON.

    ``fresh=True`` deletes the env's regenerable records (runs/flags/audits/tasks
    for its opps) before re-seeding — use it to rebuild cleanly when prior records
    no longer match the manifest (e.g. after pinning a window that used to slide).
    Stable scaffolding (definitions, render code, pipelines) is preserved.
    """
    path = Path(env_path)
    em = EnvManifest.from_yaml(path.read_text())
    weeks, current = resolve_window(
        em.timeline.completed_weeks,
        em.timeline.include_current_week,
        start_monday=em.timeline.start_monday,
    )
    if fresh:
        _reset_env_records(em)
    ctx = EnsureContext(weeks=weeks, current_week=current, env_dir=path.parent)

    for resource in em.resources:
        try:
            ensurer = ENSURERS[resource.kind]
        except KeyError as exc:
            raise KeyError(f"No ensurer registered for resource kind {resource.kind!r}") from exc
        result = ensurer(resource, ctx)
        if result:
            ctx.realized.update(result)

    if out:
        Path(out).write_text(json.dumps(ctx.realized, indent=2))
    return ctx.realized
