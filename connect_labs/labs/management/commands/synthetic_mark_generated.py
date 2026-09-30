"""Backfill provenance on labs-only opps whose fixture folder the generator wrote.

``SyntheticOpportunity.generated_folder_id`` is set by generation code as it writes
(see ``connect_labs/labs/synthetic/provenance.py``), so opps generated before that
field existed are unmarked, and so is an opp generated on a laptop
(``synthetic_generate_opps --no-register``) and then pointed at its folder with
``synthetic_repoint_by_source``: re-pointing never marks. This finds them by the one
thing that distinguishes a generated folder from anything else: its NAME. The
generator names every folder
``opp-<id>-<YYYYmmdd-HHMMSS>-generated``; a dump of real exports is the same
without the ``-generated`` suffix, and a hand-made folder is anything at all. Only an
exact match is marked. Everything else stays unmarked, which is the safe default.

    python manage.py synthetic_mark_generated            # dry run: report only
    python manage.py synthetic_mark_generated --apply    # write the marks
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import F

from connect_labs.labs.synthetic.generator.io.uploader import GENERATED_FOLDER_NAME_RE
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import mark_generated


def _drive():
    from connect_labs.labs.synthetic.gdrive import DriveClient

    return DriveClient()


class Command(BaseCommand):
    help = "Mark labs-only synthetic opps whose Drive folder the generator wrote (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the marks. Without it, only report.")

    def handle(self, *args, **options):
        apply = options["apply"]
        # Unmarked (null) rows, and rows marked for a folder they no longer serve: the
        # local flow (synthetic_generate_opps --no-register, then repoint) leaves a
        # generator-written folder behind a row that still names the previous one.
        rows = (
            SyntheticOpportunity.objects.filter(labs_only=True)
            .exclude(gdrive_folder_id="")
            .exclude(generated_folder_id=F("gdrive_folder_id"))
            .order_by("opportunity_id")
        )
        drive = None
        counts = {"mark": 0, "skip": 0, "error": 0}
        for row in rows:
            if drive is None:
                drive = _drive()
            try:
                name = drive.get_name(row.gdrive_folder_id)
            except Exception as exc:  # noqa: BLE001 — one unreadable folder must not stop the report
                counts["error"] += 1
                self.stdout.write(f"ERROR  opp {row.opportunity_id}  folder {row.gdrive_folder_id}: {exc}")
                continue
            if GENERATED_FOLDER_NAME_RE.match(name):
                counts["mark"] += 1
                verb = "MARKED" if apply else "WOULD MARK"
                if apply:
                    mark_generated(row.opportunity_id, row.gdrive_folder_id)
            else:
                counts["skip"] += 1
                verb = "SKIP"
            self.stdout.write(f"{verb}  opp {row.opportunity_id}  folder {row.gdrive_folder_id}  name {name!r}")

        mode = "applied" if apply else "dry run, nothing written (pass --apply)"
        self.stdout.write(
            f"{counts['mark']} generated, {counts['skip']} not generated, {counts['error']} unreadable ({mode})"
        )
