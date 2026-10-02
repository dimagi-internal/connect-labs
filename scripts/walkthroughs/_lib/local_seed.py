"""Local mode for a walkthrough seeder: seed the build `make serve-demo` serves, not labs.

canopy's DDD inner loop renders a fix branch against a locally served build
(`.canopy/ddd/config.yaml` `inner_loop:`), and the recipe's `setup.command` --
the same seeder that reseeds labs before every checkpoint render -- runs before
every inner-loop render too. So a seeder decides per render which world it is
resetting:

- `--local` / `--base-url http://localhost:<port>` say so explicitly;
- otherwise the recorder's `CANOPY_RENDER_BASE_URL` (the origin it is about to
  film, exported to the setup command) decides: a loopback origin means local;
- anything else is labs, unchanged.

A local seed runs the seeder's own `seed_local()` INSIDE the local labs app, via
`make demo-seed` (tools/ddd_demo.py), against that port's dedicated database. It
writes the same outputs file and the persona storage states, so the recorder
cannot tell the two routes apart except by the cookie's host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
DEFAULT_LOCAL = "http://localhost:8000"
REPO_ROOT = Path(__file__).resolve().parents[3]


def add_arguments(parser) -> None:
    parser.add_argument("--local", action="store_true", help="seed the local build (make serve-demo), not labs")
    parser.add_argument("--base-url", help="the local build's origin (implies --local), e.g. http://localhost:8010")


def target(local: bool, base_url: str | None) -> str | None:
    """The local origin to seed, or None to seed labs (the default route, unchanged)."""
    rendering = os.environ.get("CANOPY_RENDER_BASE_URL", "")
    if base_url:
        url = base_url
    elif local:
        url = rendering if urlparse(rendering).hostname in LOOPBACK else DEFAULT_LOCAL
    else:
        url = rendering
    if urlparse(url).hostname in LOOPBACK:
        return url.rstrip("/")
    if local or base_url:
        sys.exit(f"local mode seeds a loopback build only, not {url!r}")
    return None


def run(narrative: str, base_url: str, outputs: Path, *, call: str = "seed_local") -> dict:
    """Run `<narrative>/seed.py:<call>` inside the local build on `base_url`'s port; return the outputs."""
    port = urlparse(base_url).port or 80
    outputs = outputs.resolve()
    subprocess.run(
        [
            "make",
            "-s",
            "-C",
            str(REPO_ROOT),
            "demo-seed",
            f"DEMO_PORT={port}",
            f"NARRATIVE={narrative}",
            f"OUTPUTS={outputs}",
            f"SEED_CALL={call}",
        ],
        check=True,
    )
    return json.loads(outputs.read_text()) if outputs.exists() else {}


def write_state(path: Path, state: dict) -> None:
    """A Playwright storage state, owner-only (the session key is a credential)."""
    path.write_text(json.dumps(state))
    os.chmod(path, 0o600)
