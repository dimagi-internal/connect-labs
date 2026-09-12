"""Import a procurement tracker from Drive -- the command-line wrapper.

Thin on purpose. Every line of logic lives in
`procurement/services/tracker_import.py` and is reached through the
`tracker_import` OPERATION, so the same import runs from a laptop, from the
HTTP API, and from MCP against the deployed environment. It used to be a
command only, which made it the one capability in this domain the API could
not reach -- and left no way at all to run it where there is no shell.

    make manage CMD="supply_load_bootstrap --program 10501 --dry-run"
    make manage CMD="supply_load_bootstrap --program 10501 --ensure-commodity"
"""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.tracker_import import SPREADSHEET_ID, TrackerImportError


class Command(BaseCommand):
    help = "Import a procurement due-diligence tracker from Drive into a programme."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True)
        parser.add_argument("--spreadsheet-id", default=SPREADSHEET_ID)
        parser.add_argument("--commodity", default="rutf")
        parser.add_argument(
            "--ensure-commodity",
            action="store_true",
            help="Create the RUTF commodity from the published specification if the "
            "programme's catalogue does not have it yet. The ration table is left "
            "empty, because that is the programme's decision and not the "
            "specification's.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        access = SupplyDataAccess(access_token="local", program_id=options["program"])
        try:
            report = call_operation(
                "tracker_import",
                access,
                {
                    "spreadsheet_id": options["spreadsheet_id"],
                    "commodity_slug": options["commodity"],
                    "ensure_commodity": options["ensure_commodity"],
                    "dry_run": options["dry_run"],
                },
            )
        except TrackerImportError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"{report['rows']} supplier row(s) in the tracker")
        for line in report.get("would_import", []):
            self.stdout.write(f"  {line}")

        if report["imported"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nimported: " + ", ".join(f"{n} {label}" for label, n in report["imported"].items())
                )
            )
        if report["refused"]:
            self.stdout.write(
                f"\n{len(report['refused'])} thing(s) the sheet knows that this system will not guess at:"
            )
            for refusal in report["refused"]:
                self.stdout.write(self.style.WARNING(f"  - {refusal}"))
