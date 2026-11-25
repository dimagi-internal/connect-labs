"""Make, adopt or import the invoices sheet (``pulse.ledger``), as the MCP tools do.

It lives in effectiveness, not pulse, because the import keeps each opportunity's people reached
(``effectiveness.implementation.reach``), which Pulse never imports.

python manage.py pulse_costs_ledger --create --share someone@dimagi.com [--sheet-id <id a person made>]
python manage.py pulse_costs_ledger --import [--dry-run]
"""

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.effectiveness.implementation.reach import people_reached
from connect_labs.pulse import ledger


class Command(BaseCommand):
    help = (
        "Make or adopt the invoices sheet, or import its Invoices tab as labs' reconstructed invoices."
    )

    def add_arguments(self, parser):
        parser.add_argument("--create", action="store_true")
        parser.add_argument("--share", action="append", default=[])
        parser.add_argument("--sheet-id", help="adopt a sheet a person made in the folder instead of making one")
        parser.add_argument("--import", dest="do_import", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if opts["create"] == opts["do_import"]:
            raise CommandError("Pass exactly one of --create or --import.")
        try:
            got = (
                ledger.create_sheet(opts["share"], spreadsheet_id=opts["sheet_id"])
                if opts["create"]
                else ledger.run_import(people_reached=people_reached, dry_run=opts["dry_run"])
            )
        except ledger.ImportFailed as e:
            raise CommandError(f"{e.code}: {e}") from e
        self.stdout.write(json.dumps(got, indent=2, default=str))
