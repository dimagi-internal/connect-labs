"""Load GeoPoDe admin boundaries from the canonical Drive folder.

The boundary ZIPs live in a shared Google Drive folder (shared with the
connect-labs service account). This command is the *repeatable* way to load
them: it reads the checked-in manifest
(`admin_boundaries/fixtures/geopode_sources.json`), downloads each country's ZIP
via the labs Drive client, and runs it through `GeoPoDELoader` with the manifest's
canonical ISO-3 (so e.g. the `GeoPoDe_DRC_...` file lands under `COD`).

Idempotent: each load clears that country/level/source first, then re-inserts.

Usage:
    python manage.py load_geopode_from_drive --iso NGA
    python manage.py load_geopode_from_drive --iso NGA,KEN
    python manage.py load_geopode_from_drive --all
    python manage.py load_geopode_from_drive --all --dry-run

Requires `LABS_SYNTHETIC_GDRIVE_SA_KEY` (the labs Drive service account) in the
environment — already configured in the deployed labs container.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

MANIFEST = Path(__file__).resolve().parent.parent.parent / "fixtures" / "geopode_sources.json"


class Command(BaseCommand):
    help = "Load GeoPoDe admin boundaries from the canonical Drive folder (manifest-driven)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--iso", type=str, help="ISO-3 code(s), comma-separated (e.g. NGA or NGA,KEN)")
        group.add_argument("--all", action="store_true", help="Load every country in the manifest")
        parser.add_argument("--manifest", type=str, default=str(MANIFEST), help="Path to the sources manifest")
        parser.add_argument(
            "--no-clear",
            action="store_true",
            help="Append instead of replacing existing rows for the country (default: clear first)",
        )
        parser.add_argument("--dry-run", action="store_true", help="List what would load without downloading")

    def handle(self, *args, **opts):
        manifest = json.loads(Path(opts["manifest"]).read_text())
        by_iso = {c["iso"].upper(): c for c in manifest.get("countries", [])}

        if opts["all"]:
            selected = list(by_iso.values())
        else:
            wanted = [s.strip().upper() for s in opts["iso"].split(",") if s.strip()]
            missing = [w for w in wanted if w not in by_iso]
            if missing:
                raise CommandError(f"Not in manifest: {', '.join(missing)}. Known: {', '.join(sorted(by_iso))}")
            selected = [by_iso[w] for w in wanted]

        self.stdout.write(f"Selected {len(selected)} country(ies): {', '.join(c['iso'] for c in selected)}")
        if opts["dry_run"]:
            for c in selected:
                self.stdout.write(
                    f"  [dry-run] {c['iso']} <- drive {c['drive_file_id']} (provider {c.get('provider')})"
                )
            return

        # Imports deferred so --dry-run / --help work without Drive creds or heavy deps.
        from commcare_connect.labs.admin_boundaries.services import GeoPoDELoader
        from commcare_connect.labs.synthetic.gdrive import DriveAuthError, DriveClient

        try:
            client = DriveClient()
        except DriveAuthError as e:
            raise CommandError(f"{e} Set LABS_SYNTHETIC_GDRIVE_SA_KEY to the connect-labs service account key.") from e

        clear = not opts["no_clear"]
        total = 0
        for c in selected:
            iso, fid = c["iso"].upper(), c["drive_file_id"]
            self.stdout.write(f"\n{iso}: downloading {fid} ...")
            try:
                content = client.download_file(fid)
            except Exception as e:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"  {iso}: download failed: {e}"))
                continue

            result = GeoPoDELoader().load_from_zip(
                io.BytesIO(content),
                clear=clear,
                filename=f"GeoPoDe_{iso}_Geometry.zip",
                iso_override=iso,
                on_progress=lambda msg: None,
            )
            total += result.total_loaded
            status = self.style.SUCCESS("OK") if result.success else self.style.ERROR("FAILED")
            self.stdout.write(f"  {iso}: {status} loaded {result.total_loaded} boundaries")
            for lr in result.levels:
                if lr.error:
                    self.stderr.write(self.style.WARNING(f"      ADM{lr.level}: {lr.error}"))
                else:
                    self.stdout.write(f"      ADM{lr.level}: {lr.count}")

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. {total} boundaries loaded across {len(selected)} country(ies).")
        )
