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

from .ensurers.opp_data import ensure_opp_data
from .ensurers.rollup import ensure_rollup
from .ensurers.run_audits import ensure_run_audits
from .ensurers.tasks import ensure_tasks
from .ensurers.weekly_runs import ensure_weekly_runs
from .env_manifest import EnvManifest
from .window import resolve_window


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
}


def ensure_synthetic_data(env_path: str, out: str | None = None) -> dict:
    """Realize the env manifest at ``env_path``; return the cumulative realized map.

    Walks the manifest's resources in declared order, dispatching each by
    ``kind`` to :data:`ENSURERS`. Raises ``KeyError`` if a resource declares a
    kind with no registered ensurer. If ``out`` is given, writes the realized
    map there as indented JSON.
    """
    path = Path(env_path)
    em = EnvManifest.from_yaml(path.read_text())
    weeks, current = resolve_window(em.timeline.completed_weeks, em.timeline.include_current_week)
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
