"""Compare each live model Sheet with the vendored spec, and fail if they have drifted.

    python manage.py effectiveness_check_sheets            # human report, exit 1 on drift
    python manage.py effectiveness_check_sheets --json     # the same, as JSON

Reads the Model tab of each model's live Sheet as the labs service account (the Sheets are public, and
the account needs no edit access). Run it on a schedule, or before relying on a number in a proposal.
When it reports drift, refresh the specs from the model repo (``effectiveness_vendor --from <checkout>``) and
commit the result; the vendored commit is shown on every page of the cost-effectiveness explorer.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from django.core.management.base import BaseCommand, CommandError

from connect_labs.effectiveness import engine, explore, sheets_check


def sheet_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    if not m:
        raise CommandError(f"No spreadsheet id in {url!r}")
    return m.group(1)


def read_model_tab(spreadsheet_id: str) -> list[list]:
    """The Model tab with numbers as numbers (``UNFORMATTED_VALUE``), as the labs service account."""
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote('Model!A1:N400', safe='')}"
        "?valueRenderOption=UNFORMATTED_VALUE"
    )
    got = httpx.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=60)
    got.raise_for_status()
    return got.json().get("values", [])


class Command(BaseCommand):
    help = "Compare each live model Sheet's computed results with the vendored spec; exit 1 on drift."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Print the report as JSON.")

    def handle(self, *args, as_json=False, **options):
        meta = explore.models()
        reports = []
        for model in engine.MODELS:
            reports.append(sheets_check.compare(model, read_model_tab(sheet_id(meta[model]["sheet"]))))
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
