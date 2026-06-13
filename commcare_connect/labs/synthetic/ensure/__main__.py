"""CLI entrypoint: ``python -m commcare_connect.labs.synthetic.ensure <env> [--out]``.

Realizes a composite env manifest by handing it to
:func:`~.engine.ensure_synthetic_data`, writes the cumulative realized map to
``--out`` (default ``realized.json`` in the cwd), and prints the path plus a
one-line summary of how many resources were realized.

The ensurers touch Django ORM models (synthetic opps, workflow runs, audits,
tasks), so Django must be configured before they run. When invoked as a module
this calls ``django.setup()`` once (defaulting ``DJANGO_SETTINGS_MODULE`` to
``config.settings.local`` if unset), which is a no-op if a host already set it
up. The work itself is delegated entirely to the engine.
"""

from __future__ import annotations

import argparse
import os
import sys


def _ensure_django() -> None:
    """Configure Django if it isn't already (idempotent)."""
    import django
    from django.apps import apps

    if apps.ready:
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the engine, print a summary. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m commcare_connect.labs.synthetic.ensure",
        description="Realize a composite synthetic env manifest onto labs (idempotent).",
    )
    parser.add_argument("env_path", help="Path to the composite env manifest YAML.")
    parser.add_argument(
        "--out",
        default="realized.json",
        help="Where to write the realized map as JSON (default: ./realized.json).",
    )
    args = parser.parse_args(argv)

    _ensure_django()

    # Imported after Django is configured so model imports in the engine's
    # ensurer chain resolve cleanly.
    from .engine import ensure_synthetic_data

    realized = ensure_synthetic_data(args.env_path, out=args.out)
    print(f"Realized {len(realized)} variable(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
