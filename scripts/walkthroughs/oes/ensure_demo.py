"""Setup entrypoint for the four OES supply-chain walkthroughs.

The single ``setup:`` command each OES recipe declares. Its whole job is to
reseed the demo world before a render, because every one of these narratives is
state-mutating — Zara raises a shortfall, Ada reallocates against it — and a
second take must find the world as the first one did.

**Why this exists rather than ``python manage.py seed_supply_demo`` inline.**
The recorder runs the setup command as a subprocess of the *canopy* runtime,
whose interpreter has no Django. A bare ``python manage.py`` therefore resolves
to the wrong venv and dies before the browser opens, with an error that reads
like a labs problem and isn't. This script finds the labs virtualenv itself and
re-execs the seed through it, so the recipe stays portable and the failure mode
disappears.

It also sets the GDAL/GEOS paths Django's GIS stack needs on macOS, where the
Homebrew prefix is not where the loader looks.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _main_clone_venv() -> Path | None:
    """The venv in the main clone this worktree shares, found via git.

    An emdash worktree keeps no venv of its own — it borrows the main clone's.
    Asking git where the common .git lives locates that clone whatever the
    checkout is called and wherever it sits, which a hardcoded path cannot:
    this list used to name ``~/emdash-projects/connect-labs`` and silently
    stopped resolving when the clone moved to ``~/emdash/repositories``, so
    every OES render failed preflight with an error that reads like a missing
    Django rather than a moved directory.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not common:
        return None
    return (REPO_ROOT / common).resolve().parent / ".venv/bin/python"


# Candidate interpreters, best first. The main-repo venv is the one a worktree
# shares (this repo uses emdash worktrees, whose venv lives in the main clone).
CANDIDATES = [
    p
    for p in (
        _main_clone_venv(),
        REPO_ROOT / ".venv/bin/python",
        Path.home() / "emdash-projects/connect-labs/.venv/bin/python",
    )
    if p is not None
]

# Homebrew's GDAL/GEOS, which django.contrib.gis will not find unaided on
# Apple Silicon (it looks under /usr/local, Homebrew installs under /opt).
GEO_LIBS = {
    "GDAL_LIBRARY_PATH": "/opt/homebrew/lib/libgdal.dylib",
    "GEOS_LIBRARY_PATH": "/opt/homebrew/lib/libgeos_c.dylib",
}


def _find_python() -> str:
    for candidate in CANDIDATES:
        if candidate.exists():
            return str(candidate)
    # Last resort: a `python` on PATH that can actually import Django.
    found = shutil.which("python") or sys.executable
    probe = subprocess.run([found, "-c", "import django"], capture_output=True)
    if probe.returncode == 0:
        return found
    raise SystemExit(
        "ensure_demo: no interpreter with Django available. Looked for:\n  "
        + "\n  ".join(str(c) for c in CANDIDATES)
        + f"\n  {found} (on PATH — cannot import django)"
    )


def main() -> int:
    python = _find_python()
    env = dict(os.environ)
    for key, path in GEO_LIBS.items():
        if os.path.exists(path):
            env.setdefault(key, path)

    cmd = [python, "manage.py", "seed_supply_demo", "--reset"]
    print(f"ensure_demo: {' '.join(cmd)}  (cwd={REPO_ROOT})", flush=True)
    result = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    if result.returncode != 0:
        print("ensure_demo: seed failed — the world is not in a recordable state.", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
