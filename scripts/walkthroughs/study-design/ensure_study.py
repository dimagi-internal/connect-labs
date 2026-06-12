#!/usr/bin/env python
"""CLI for the microplans study-design CREATION demo seeder.

Idempotently builds the Vitamin-A Kaura two-arm study (one study group of per-ward
microplans per round) on the labs-only program ``-opportunity_id``, reading the SAME
``../verified-monitoring/demo_config.json`` the monitoring narrative reads — so the
two narratives can't drift. All logic lives in
``commcare_connect.microplans.study_seed``; this is a thin Django-bootstrapping CLI.

Usage (from the repo root; venv on PATH):

    # Ensure all 6 rounds exist + are sampled (the always-present backdrop). No-op on re-run.
    python scripts/walkthroughs/study-design/ensure_study.py ensure

    # Ensure only one round (e.g. just the live-demo round's backdrop).
    python scripts/walkthroughs/study-design/ensure_study.py ensure --round r6

    # Create groups/plans but skip the slow Overture building fetch (structure only).
    python scripts/walkthroughs/study-design/ensure_study.py ensure --no-generate

    # Reset the live-demo round so the walkthrough can re-create it on camera.
    python scripts/walkthroughs/study-design/ensure_study.py reset r6

On macOS prefix with the GDAL/GEOS paths (django.contrib.gis):

    GDAL_LIBRARY_PATH=/opt/homebrew/lib/libgdal.dylib \
    GEOS_LIBRARY_PATH=/opt/homebrew/lib/libgeos_c.dylib \
    python scripts/walkthroughs/study-design/ensure_study.py ensure
"""

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _bootstrap_django() -> None:
    sys.path.insert(0, str(_REPO_ROOT))
    # django.contrib.gis dlopen's GDAL/GEOS; on Apple Silicon Homebrew installs them
    # under /opt/homebrew/lib (not the loader default). Set before django.setup().
    for var, path in (
        ("GDAL_LIBRARY_PATH", "/opt/homebrew/lib/libgdal.dylib"),
        ("GEOS_LIBRARY_PATH", "/opt/homebrew/lib/libgeos_c.dylib"),
    ):
        if not os.environ.get(var) and Path(path).exists():
            os.environ[var] = path
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed/reset the Kaura two-arm study (microplans creation demo).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ensure = sub.add_parser("ensure", help="Idempotently create/reuse the study (groups + ward plans + samples).")
    p_ensure.add_argument("--round", dest="round_key", default=None, help="Only this round, e.g. r6.")
    p_ensure.add_argument(
        "--no-generate",
        dest="generate",
        action="store_false",
        help="Create boundary-only plans + group, skip the Overture sampling pass.",
    )

    p_reset = sub.add_parser("reset", help="Delete one round's group + plans so it can be re-created live.")
    p_reset.add_argument("round_key", help="Round to reset, e.g. r6.")

    args = parser.parse_args()
    _bootstrap_django()

    from commcare_connect.microplans import study_seed

    manifest = study_seed.load_manifest()
    study_seed.ensure_synthetic_program(manifest)
    da = study_seed.data_access_for(manifest)

    def progress(done, total, results, ok):
        print(f"  sampling {done}/{total} (ok={ok})", file=sys.stderr)

    if args.cmd == "ensure":
        out = study_seed.ensure_study(
            da, manifest, generate=args.generate, only_round=args.round_key, progress=progress
        )
    else:  # reset
        out = study_seed.reset_round(da, manifest, args.round_key)

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
