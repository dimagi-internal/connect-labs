"""Load a snapshot instead of re-fetching everything from source.

    make manage CMD="targeting_import --path targeting-snapshot-20260827.zip"
    make manage CMD="targeting_import --drive-file-id <id>"
    make manage CMD="targeting_import --from-manifest"

Seconds, against 30-45 minutes of API calls — and it does not spend WorldPop's
daily quota, which a second environment can otherwise exhaust for the first.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.indicators import snapshot

MANIFEST = Path(__file__).resolve().parent.parent.parent / "fixtures" / "snapshot.json"


class Command(BaseCommand):
    help = "Import a targeting snapshot ZIP (local path, Drive file, or pinned manifest)"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--path", help="Local snapshot ZIP")
        group.add_argument("--drive-file-id", help="Drive file id of a snapshot")
        group.add_argument(
            "--from-manifest",
            action="store_true",
            help="Use the Drive id pinned in fixtures/snapshot.json",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "Make the snapshot authoritative: delete values it does not carry, "
                "within the (indicator, country) pairs it covers. Without this a "
                "restore can only add, so a row deleted in the source database "
                "survives here and the two environments quietly disagree."
            ),
        )

    def handle(self, *args, **opts):
        if opts["path"]:
            blob = Path(opts["path"]).read_bytes()
            self.stdout.write(f"Reading {opts['path']} ({len(blob) / 1e6:.1f} MB)")
        else:
            file_id = opts["drive_file_id"]
            if opts["from_manifest"]:
                if not MANIFEST.exists():
                    raise CommandError(f"{MANIFEST} does not exist — export a snapshot and pin its Drive id there")
                pinned = json.loads(MANIFEST.read_text())
                file_id = pinned.get("drive_file_id")
                if not file_id:
                    raise CommandError(f"{MANIFEST} has no drive_file_id")
                self.stdout.write(f"Pinned snapshot {pinned.get('created_at', '?')} ({file_id})")

            from connect_labs.labs.synthetic.gdrive import DriveAPIError, DriveAuthError, DriveClient

            try:
                client = DriveClient()
            except DriveAuthError as e:
                raise CommandError(
                    f"{e} Set LABS_SYNTHETIC_GDRIVE_SA_KEY, or download the file by " "hand and pass --path."
                ) from e
            try:
                blob = client.download_file(file_id)
            except DriveAPIError as e:
                raise CommandError(f"Download failed: {e}") from e
            self.stdout.write(f"Downloaded {len(blob) / 1e6:.1f} MB from Drive")

        try:
            result = snapshot.import_snapshot(blob, on_progress=self.stdout.write, prune=opts["prune"])
        except ValueError as e:
            raise CommandError(str(e)) from e

        m = result["manifest"]
        self.stdout.write(
            self.style.SUCCESS(f"\nImported {result['boundaries']:,} boundaries and {result['values']:,} values")
        )
        if result.get("values_pruned"):
            self.stdout.write(
                self.style.WARNING(
                    f"  {result['values_pruned']:,} values removed — this snapshot does not carry "
                    "them, and --prune makes it authoritative for what it covers."
                )
            )
        if result["values_skipped"]:
            self.stdout.write(
                self.style.WARNING(
                    f"  {result['values_skipped']:,} values skipped — their boundary is not in "
                    "this database. Load boundaries first, or use a snapshot with geometry."
                )
            )
        if m.get("contains_non_commercial"):
            self.stdout.write(self.style.WARNING("  This snapshot contains non-commercial data. Do not pass it on."))
        self.stdout.write('\nVerify with: make manage CMD="targeting_status"')
