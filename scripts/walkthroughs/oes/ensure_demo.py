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


def _labs_pat() -> str:
    """The labs MCP Personal Access Token, from the env or the MCP client config.

    Nothing to provision: a PAT is self-service at ``/labs/mcp/tokens/``, and if
    this machine can already talk to the labs MCP server then it already has one
    sitting in ``~/.claude.json``. Reading it from there means a render against a
    deployed instance needs no new secret anywhere — which is the whole point of
    not inventing one.
    """
    for var in ("CONNECT_LABS_MCP_TOKEN", "LABS_MCP_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value

    import json as _json

    config = Path.home() / ".claude.json"
    if not config.exists():
        return ""
    try:
        data = _json.loads(config.read_text())
    except (OSError, ValueError):
        return ""

    def find(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "connect_labs" and isinstance(value, dict):
                    auth = (value.get("headers") or {}).get("Authorization", "")
                    if auth.lower().startswith("bearer "):
                        return auth[7:].strip()
                found = find(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = find(item)
                if found:
                    return found
        return ""

    return find(data)


def _reseed_remotely(base_url: str, token: str, password: str | None = None) -> int:
    """Reseed a DEPLOYED site over HTTP (``/supply/api/demo/reseed/``).

    The deployed site has no shell for the render loop to use. The documented
    alternative was an interactive ``aws ecs execute-command`` — an SSO token and
    a human — which a loop cannot do between takes, so prod renders were
    effectively single-shot even though every OES narrative mutates state.
    """
    import json
    import urllib.error
    import urllib.request

    url = f"{base_url.rstrip('/')}/supply/api/demo/reseed/"
    print(f"ensure_demo: POST {url}", flush=True)
    payload = json.dumps({"password": password} if password else {}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", "replace")[:300]
        hint = ""
        if exc.code == 401:
            hint = "  (401 means the labs MCP token is invalid, revoked or expired)"
        elif exc.code == 404:
            hint = "  (404 means this instance predates the reseed route — deploy it first)"
        elif exc.code == 409:
            hint = "  (409 means another reseed is in flight — retry shortly)"
        print(f"ensure_demo: reseed failed HTTP {exc.code}: {detail}{hint}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ensure_demo: could not reach {url}: {exc}", file=sys.stderr)
        return 1

    summary = body.get("summary") or {}
    print(
        "ensure_demo: reseeded "
        f"{summary.get('suppliers', '?')} suppliers, "
        f"{summary.get('solicitations', '?')} solicitations, "
        f"{summary.get('awards', '?')} awards",
        flush=True,
    )
    return 0


def main() -> int:
    # A deployed target reseeds over HTTP; a local one runs the management
    # command. Same entry point either way, so the recipe's `setup:` block does
    # not have to know which world it is filming.
    base_url = os.environ.get("OES_BASE_URL", "").strip()
    is_local = (not base_url) or "localhost" in base_url or "127.0.0.1" in base_url
    if not is_local:
        token = _labs_pat()
        if not token:
            print(
                f"ensure_demo: OES_BASE_URL is {base_url} (not local) but no labs MCP "
                "token was found, so the demo world cannot be reset — and every OES "
                "narrative mutates state, so the take after this one would film an "
                "already-awarded tender. Mint one at /labs/mcp/tokens/ (or run "
                "/labs-token-setup) and it will be picked up from ~/.claude.json.",
                file=sys.stderr,
            )
            return 1
        # Reseeding also SETS the persona password, so the render signs in with a
        # credential it just established rather than one someone had to share.
        return _reseed_remotely(base_url, token, os.environ.get("SUPPLY_DEMO_PASSWORD", "").strip() or None)

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
