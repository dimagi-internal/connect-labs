"""PHASE 1 (prod): profile real opps into per-opp bundles.

Spec-driven (recommended) — one YAML for both phases::

    python manage.py synthetic_profile_opps --spec kmc.yaml --base-url https://connect.dimagi.com

The spec's bundle_root is resolved (a bare 'gdrive:' becomes 'gdrive:<folder_id>') and
written back into kmc.yaml, so you hand the same file to synthetic_generate_opps.

Explicit flags (no spec)::

    python manage.py synthetic_profile_opps \\
        --opps 523,524 --out gdrive: --base-url https://connect.dimagi.com
"""

import os

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.synthetic.clone_from_prod import profile_cohort, profile_opps_bulk
from connect_labs.labs.synthetic.cohort import load_cohort_spec, save_cohort_spec
from connect_labs.labs.synthetic.gdrive import DriveClient


class Command(BaseCommand):
    help = "PHASE 1 (prod): profile real opps into per-opp bundles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--spec",
            help="Path to a cohort spec YAML (opportunity_ids + program + bundle_root). "
            "Updated in place with the resolved bundle_root.",
        )
        parser.add_argument("--opps", help="Comma-separated source opportunity_ids (if not using --spec).")
        parser.add_argument(
            "--out",
            help="Where to write bundles (if not using --spec): a local directory, or 'gdrive:' / 'gdrive:<id>'.",
        )
        parser.add_argument("--token-env", default="CONNECT_OAUTH_TOKEN", help="Env var holding the OAuth token.")
        parser.add_argument("--base-url", required=True, help="Connect base URL (e.g. https://connect.dimagi.com).")
        parser.add_argument(
            "--curate",
            action="store_true",
            help="Curate for analytics signal (floor flag rates + degenerate clinical categoricals, "
            "per-opp varied) for the --opps path. With --spec, set 'curate: true' in the YAML instead.",
        )

    def handle(self, *args, **opts):
        token = os.environ.get(opts["token_env"])
        if not token:
            raise CommandError(f"Env var {opts['token_env']} is empty.")

        if opts.get("spec"):
            spec = load_cohort_spec(opts["spec"])
            drive = DriveClient() if str(spec.bundle_root).startswith("gdrive:") else None
            spec = profile_cohort(spec, base_url=opts["base_url"], oauth_token=token, drive=drive)
            save_cohort_spec(opts["spec"], spec)
            self.stdout.write(self.style.SUCCESS(f"Profiled {len(spec.opportunity_ids)} opps."))
            self.stdout.write(f"Recorded bundle_root in {opts['spec']}: {spec.bundle_root}")
            return

        if not (opts.get("opps") and opts.get("out")):
            raise CommandError("Provide --spec, or both --opps and --out.")
        drive = DriveClient() if str(opts["out"]).startswith("gdrive:") else None
        ids = [int(x) for x in opts["opps"].split(",") if x.strip()]
        resolved, handles = profile_opps_bulk(
            ids,
            base_url=opts["base_url"],
            oauth_token=token,
            bundle_root=opts["out"],
            drive=drive,
            curate=opts["curate"],
        )
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(handles)} bundles."))
        self.stdout.write(f"Phase-2 bundle_root: {resolved}")
