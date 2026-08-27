"""Write a snapshot of the targeting dataset, optionally straight to Drive.

    make manage CMD="targeting_export"
    make manage CMD="targeting_export --out /tmp/snap.zip"
    make manage CMD="targeting_export --to-drive <folder_id>"
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.indicators import snapshot


class Command(BaseCommand):
    help = "Export boundaries and indicator values as a portable snapshot ZIP"

    def add_arguments(self, parser):
        parser.add_argument("--out", help="Output path; defaults to a dated file in the cwd")
        parser.add_argument("--iso", help="Comma-separated ISO-3 codes; default is all of Africa")
        parser.add_argument(
            "--no-geometry",
            action="store_true",
            help=(
                "Values only (under 1 MB instead of ~52 MB). Useful when the target "
                "already holds the boundaries; values whose boundary is missing are "
                "skipped on import rather than invented."
            ),
        )
        parser.add_argument(
            "--to-drive",
            metavar="FOLDER_ID",
            help="Also upload to this Drive folder, using the labs service account",
        )

    def handle(self, *args, **opts):
        iso = [c.strip().upper() for c in opts["iso"].split(",")] if opts.get("iso") else None

        self.stdout.write("Building snapshot ...")
        blob = snapshot.export(iso_codes=iso, include_geometry=not opts["no_geometry"])

        stamp = datetime.now(UTC).strftime("%Y%m%d")
        out = Path(opts["out"] or f"targeting-snapshot-{stamp}.zip")
        out.write_bytes(blob)
        self.stdout.write(self.style.SUCCESS(f"  wrote {out} ({len(blob) / 1e6:.1f} MB)"))

        if opts["to_drive"]:
            from connect_labs.labs.synthetic.gdrive import DriveAPIError, DriveAuthError, DriveClient

            try:
                client = DriveClient()
            except DriveAuthError as e:
                raise CommandError(
                    f"{e} Set LABS_SYNTHETIC_GDRIVE_SA_KEY to the connect-labs "
                    "service account key (the same one the synthetic fixtures use)."
                ) from e
            self.stdout.write(f"  uploading {len(blob) / 1e6:.1f} MB ...")
            try:
                file_id = client.upload_file(opts["to_drive"], out.name, blob)
            except DriveAPIError as e:
                # The local file is already written, so the upload failing is
                # not a lost export — say so rather than leaving it ambiguous.
                raise CommandError(f"Upload failed: {e}\nThe snapshot is still at {out}.") from e
            self.stdout.write(self.style.SUCCESS(f"  uploaded to Drive as {file_id}"))
            self.stdout.write(
                "  Pin it in connect_labs/labs/indicators/fixtures/snapshot.json so\n"
                "  `targeting_import --from-manifest` finds it."
            )
