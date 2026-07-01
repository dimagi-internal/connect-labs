"""Env-template registry: discovery + safe resolution for env manifests.

Mirrors the workflow template registry
(``connect_labs/workflow/templates/__init__.py``), which scans its package
dir, loads each template, builds a ``TEMPLATES`` dict keyed by file stem, and
exposes ``get_template`` / ``list_templates`` accessors that tolerate a bad
template without crashing discovery.

WHY a ``registry.py`` module rather than an ``envs/__init__.py`` package
registry: the workflow registry lives in ``templates/__init__.py`` because its
templates ARE Python modules (it imports them). Env manifests are ``*.yaml``
data files, not importable modules, so the closest faithful mirror is a module
that *scans* ``ENVS_DIR`` and parses each YAML — there is nothing to import.
Keeping it as a sibling module of the engine (rather than ``envs/__init__.py``)
also keeps ``envs/`` a pure data directory and avoids turning it into a Python
package whose ``__init__`` would run on every ``ENVS_DIR`` path computation.

This module also owns the safe path resolution (single-segment names, no
traversal). The engine's ``resolve_env_path`` delegates here so there is one
implementation of "name -> manifest path".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .env_manifest import EnvManifest

logger = logging.getLogger(__name__)

# Where the checked-in env manifests live, resolved off the synthetic package
# dir (NOT the cwd) so name-based resolution works identically whether the code
# runs from a dev checkout or the deployed labs app's working directory. The
# ensure package is ``.../labs/synthetic/ensure``; the manifests sit a level up
# at ``.../labs/synthetic/envs/<env>.yaml`` next to their per-opp manifests.
ENVS_DIR = Path(__file__).resolve().parent.parent / "envs"

# An env name is a single path segment of safe chars only — no separators, no
# ``..`` — so resolution can never escape ``ENVS_DIR``.
_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass
class EnvManifestEntry:
    """One discovered env template: its key, path, parsed manifest, and summary.

    ``key`` is the YAML file stem (e.g. ``"program-admin-report"``).
    ``manifest`` is the validated :class:`EnvManifest`. ``summary`` is the
    small dict surfaced by ``list_envs`` / ``synthetic_env_get`` (env name,
    resource kinds, opp ids) so callers don't have to walk the manifest.
    """

    key: str
    path: Path
    manifest: EnvManifest

    @property
    def summary(self) -> dict:
        return _summary(self.key, self.manifest)


def _opportunity_ids(manifest: EnvManifest) -> list[int]:
    """Collect the distinct opportunity ids referenced across the manifest's
    resources, in first-seen order. Different resource kinds expose them
    differently (``opportunity_id`` vs ``opportunity_ids``)."""
    seen: list[int] = []
    for resource in manifest.resources:
        single = getattr(resource, "opportunity_id", None)
        if single is not None and single not in seen:
            seen.append(int(single))
        for oid in getattr(resource, "opportunity_ids", None) or []:
            if oid not in seen:
                seen.append(int(oid))
    return seen


def _summary(key: str, manifest: EnvManifest) -> dict:
    """Build the registry summary dict for one env (the template, not a
    realization). Lists the resource kinds in declared order and the opp ids
    the env touches."""
    return {
        "key": key,
        "env": manifest.env,
        "resource_kinds": [r.kind for r in manifest.resources],
        "resource_count": len(manifest.resources),
        "opportunity_ids": _opportunity_ids(manifest),
        "completed_weeks": manifest.timeline.completed_weeks,
        "include_current_week": manifest.timeline.include_current_week,
    }


# Discovered env templates, keyed by file stem. Populated by discover_envs().
ENVS: dict[str, EnvManifestEntry] = {}


def discover_envs() -> dict[str, EnvManifestEntry]:
    """Scan ``ENVS_DIR`` for ``*.yaml`` env manifests; (re)build ``ENVS``.

    Skips ``_``-prefixed files and the ``manifests/`` subdir (those are the
    per-opp generator manifests, not env templates). Each file is loaded via
    :meth:`EnvManifest.from_yaml`; an invalid manifest is skipped with a logged
    warning rather than crashing discovery — same tolerance the workflow
    template registry has for a bad template.

    Returns the rebuilt ``ENVS`` dict (also stored module-level).
    """
    ENVS.clear()
    if not ENVS_DIR.is_dir():
        logger.warning("Env manifests dir does not exist: %s", ENVS_DIR)
        return ENVS

    for path in sorted(ENVS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        key = path.stem
        try:
            manifest = EnvManifest.from_yaml(path.read_text())
        except Exception as exc:  # noqa: BLE001 — tolerate one bad manifest
            logger.warning("Skipping invalid env manifest %s: %s", path.name, exc)
            continue
        ENVS[key] = EnvManifestEntry(key=key, path=path, manifest=manifest)
        logger.debug("Registered env template: %s", key)
    return ENVS


# Discover on module load (mirrors the workflow registry).
discover_envs()


def _validate_env_name(env: str) -> None:
    """Raise ``ValueError`` unless ``env`` is a plain single-segment name."""
    if not isinstance(env, str) or not _ENV_NAME_RE.match(env):
        raise ValueError(f"Invalid env name {env!r}: expected a plain name like 'program-admin-report'.")


def get_env_path(env: str) -> Path:
    """Map an env NAME to its manifest path, safely.

    Resolves ``<ENVS_DIR>/<env>.yaml`` off the package dir, not the cwd, so it
    works inside the deployed labs app. Rejects anything that isn't a plain,
    single-segment name (no separators, no ``..``) to foreclose path traversal,
    then verifies the resolved file exists and stays directly under
    ``ENVS_DIR``. Raises ``ValueError`` on a bad/unknown name.
    """
    _validate_env_name(env)
    candidate = (ENVS_DIR / f"{env}.yaml").resolve()
    # Defense in depth: the resolved path must live directly under ENVS_DIR.
    if candidate.parent != ENVS_DIR.resolve() or not candidate.is_file():
        available = sorted(p.stem for p in ENVS_DIR.glob("*.yaml") if not p.name.startswith("_"))
        raise ValueError(f"Unknown env {env!r}. Available envs: {available}")
    return candidate


def get_env(env: str) -> EnvManifestEntry:
    """Return the :class:`EnvManifestEntry` for ``env``.

    Validates the name (rejecting traversal) and loads from the live registry,
    re-parsing fresh from disk as a fallback if the registry was built before
    the file appeared. Raises ``ValueError`` on a bad/unknown name.
    """
    _validate_env_name(env)
    entry = ENVS.get(env)
    if entry is not None:
        return entry
    # Not in the cached registry — resolve via the safe path (raises on unknown)
    # and parse on demand so a freshly-added manifest is still reachable.
    path = get_env_path(env)
    manifest = EnvManifest.from_yaml(path.read_text())
    entry = EnvManifestEntry(key=env, path=path, manifest=manifest)
    ENVS[env] = entry
    return entry


def list_envs() -> list[dict]:
    """List all discovered env templates as registry summary dicts."""
    return [entry.summary for entry in ENVS.values()]
