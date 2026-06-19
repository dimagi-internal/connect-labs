"""PHASE 1 (prod): profile real opps into per-opp bundles.

Usage::

    python manage.py synthetic_profile_opps \\
        --opps 523,524 \\
        --out /tmp/bundles \\
        --token-env CONNECT_OAUTH_TOKEN \\
        --base-url https://connect.dimagi.com
"""

import os

from django.core.management.base import BaseCommand, CommandError

from commcare_connect.labs.synthetic.clone_from_prod import profile_opps_bulk
from commcare_connect.labs.synthetic.gdrive import DriveClient


class Command(BaseCommand):
    help = "PHASE 1 (prod): profile real opps into per-opp bundles."

    def add_arguments(self, parser):
        parser.add_argument("--opps", required=True, help="Comma-separated source opportunity_ids.")
        parser.add_argument(
            "--out",
            required=True,
            help=(
                "Where to write bundles: a local directory, or 'gdrive:' / "
                "'gdrive:<folder_id>' for durable Drive storage."
            ),
        )
        parser.add_argument("--token-env", default="CONNECT_OAUTH_TOKEN", help="Env var holding the OAuth token.")
        parser.add_argument("--base-url", required=True, help="Connect base URL (e.g. https://connect.dimagi.com).")

    def handle(self, *args, **opts):
        token = os.environ.get(opts["token_env"])
        if not token:
            raise CommandError(f"Env var {opts['token_env']} is empty.")
        ids = [int(x) for x in opts["opps"].split(",") if x.strip()]
        drive = DriveClient() if str(opts["out"]).startswith("gdrive:") else None
        resolved, handles = profile_opps_bulk(
            ids, base_url=opts["base_url"], oauth_token=token, bundle_root=opts["out"], drive=drive
        )
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(handles)} bundles."))
        self.stdout.write(f"Phase-2 bundle_root: {resolved}")
