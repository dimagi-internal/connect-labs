"""POWER USER (full production access): profile a Drive dump of real exports into a bundle.

The first-class flow profiles on the server (the ``synthetic_profile_opp`` MCP tool).
For an opportunity too large for that, dump its exports from ``/labs/synthetic/``
first, then profile the dump here. The dump is only a profiling INPUT: the output is
the same bundle a live profile writes, and a synthetic opp is generated from THAT
(``synthetic_generate_opp`` / ``synthetic_generate_opps``). Never point an opp at the
dump folder itself.

    python manage.py synthetic_profile_dump --folder <dump_folder_id> --out gdrive: --case-timelines

A dump has no app structure. Pass ``--base-url`` (with a token in ``--token-env``) to
fetch it from production so fields are typed as a live profile would type them.
"""

from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.synthetic.bundle import make_bundle_store
from connect_labs.labs.synthetic.clone_from_prod import _fetch_endpoint, profile_dump_to_bundle
from connect_labs.labs.synthetic.gdrive import DriveClient


class Command(BaseCommand):
    help = "Profile a Drive dump of real exports into a profile bundle (power users only)."

    def add_arguments(self, parser):
        parser.add_argument("--folder", required=True, help="The dump's Drive folder id.")
        parser.add_argument(
            "--out", required=True, help="Bundle root: a local directory, or 'gdrive:' / 'gdrive:<id>'."
        )
        parser.add_argument(
            "--case-timelines",
            "--mirror",
            dest="mirror",
            action="store_true",
            help="Model each worker's caseload and each case's timeline (--mirror is the old name).",
        )
        parser.add_argument("--curate", action="store_true", help="Curate for analytics signal (see profiler).")
        parser.add_argument("--base-url", help="Connect base URL, to fetch the app structure the dump lacks.")
        parser.add_argument("--token-env", default="CONNECT_OAUTH_TOKEN", help="Env var holding the OAuth token.")

    def handle(self, *args, **opts):
        drive = DriveClient()
        app_structure = None
        if opts.get("base_url"):
            token = os.environ.get(opts["token_env"])
            if not token:
                raise CommandError(f"Env var {opts['token_env']} is empty.")
            files = drive.list_folder(opts["folder"])
            if "opportunity.json" not in files:
                raise CommandError("The dump has no opportunity.json.")
            opp_id = json.loads(drive.download_file(files["opportunity.json"]).decode()).get("id")
            app_structure = _fetch_endpoint(opts["base_url"], opp_id, "app_structure", token) or {}
        store = make_bundle_store(opts["out"], drive=drive)
        try:
            handle = profile_dump_to_bundle(
                opts["folder"],
                drive=drive,
                store=store,
                app_structure=app_structure,
                curate=opts["curate"],
                mirror=opts["mirror"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        root = f"gdrive:{store.root_folder_id}" if hasattr(store, "root_folder_id") else opts["out"]
        self.stdout.write(self.style.SUCCESS(f"Profiled the dump into bundle {handle} (bundle_root {root})."))
        self.stdout.write("Generate from it with synthetic_generate_opp; never serve the dump folder itself.")
