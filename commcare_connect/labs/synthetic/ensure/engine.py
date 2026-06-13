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
import re
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


# Where the checked-in env manifests live, resolved off the synthetic package
# dir (NOT the cwd) so name-based resolution works identically whether the code
# runs from a dev checkout or the deployed labs app's working directory. The
# ensure package is ``.../labs/synthetic/ensure``; the manifests sit a level up
# at ``.../labs/synthetic/envs/<env>.yaml`` next to their per-opp manifests.
ENVS_DIR = Path(__file__).resolve().parent.parent / "envs"

# An env name is a single path segment of safe chars only — no separators, no
# ``..`` — so resolution can never escape ``ENVS_DIR``.
_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def resolve_env_path(env: str) -> Path:
    """Map an env NAME (e.g. ``"program-admin-report"``) to its manifest path.

    Resolves ``<ENVS_DIR>/<env>.yaml`` off the package dir, not the cwd, so it
    works inside the deployed labs app. Rejects anything that isn't a plain,
    single-segment name (no path separators, no ``..``) to foreclose path
    traversal, then verifies the resolved file actually exists and stays within
    ``ENVS_DIR``. Raises ``ValueError`` on a bad/unknown name.
    """
    if not isinstance(env, str) or not _ENV_NAME_RE.match(env):
        raise ValueError(f"Invalid env name {env!r}: expected a plain name like 'program-admin-report'.")
    candidate = (ENVS_DIR / f"{env}.yaml").resolve()
    # Defense in depth: the resolved path must live directly under ENVS_DIR.
    if candidate.parent != ENVS_DIR.resolve() or not candidate.is_file():
        available = sorted(p.stem for p in ENVS_DIR.glob("*.yaml"))
        raise ValueError(f"Unknown env {env!r}. Available envs: {available}")
    return candidate


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
