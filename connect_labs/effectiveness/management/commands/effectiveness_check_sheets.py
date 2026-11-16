"""Compare each live model Sheet with the vendored spec, and fail if they have drifted.

    python manage.py effectiveness_check_sheets            # human report, exit 1 on drift
    python manage.py effectiveness_check_sheets --json     # the same, as JSON

Reads the Model tab of each model's live Sheet as the labs service account (the Sheets are public, and
the account needs no edit access). A daily task (``tasks.check_model_sheets``) runs the same comparison;
run this by hand before relying on a number in a proposal.
When it reports drift, refresh the specs from the model repo (``effectiveness_vendor --from <checkout>``) and
commit the result; the vendored commit is shown on every page of the cost-effectiveness explorer.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.effectiveness import engine, sheet_drift


class Command(BaseCommand):
    help = "Compare each live model Sheet's computed results with the vendored spec; exit 1 on drift."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Print the report as JSON.")

    def handle(self, *args, as_json=False, **options):
        reports = sheet_drift.check_all()
        if as_json:
            self.stdout.write(json.dumps(reports, indent=1))
        else:
            src = engine.source()
            self.stdout.write(f"vendored from {src.get('repo')} @ {str(src.get('commit'))[:7]}")
            for r in reports:
                state = "in sync" if r["in_sync"] else "DRIFTED"
                self.stdout.write(
                    f"{r['model']}: {state} ({r['cells_compared']} cells, {len(r['differing'])} differ, "
                    f"{len(r['rows_missing_in_live'])} rows missing)"
                )
                for d in r["differing"][:10]:
                    self.stdout.write(
                        f"    {d['column']} / {d['row']}: vendored {d['vendored']:.6g}, live {d['live']:.6g}"
                    )
                for label in r["rows_missing_in_live"][:5]:
                    self.stdout.write(f"    row not found in the live Sheet: {label}")
        if not all(r["in_sync"] for r in reports):
            raise CommandError("A live model Sheet differs from the vendored spec. Refresh with effectiveness_vendor.")
