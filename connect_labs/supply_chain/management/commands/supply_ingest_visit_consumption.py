"""Post consumption from an opportunity's visits, per its dispensing rules.

    make manage CMD="supply_ingest_visit_consumption --program 10000 --opportunity 10000 --dry-run"

Safe to re-run: posting is idempotent per visit and item. --dry-run runs the
whole thing inside a transaction it rolls back, so the report is exactly what
a real run would do and nothing -- not even the OperationCall -- is kept.

A real opportunity reads with an export-scoped token from SUPPLY_EXPORT_TOKEN
(never argv: a token on a command line lands in shell history); a synthetic
one needs none. Real programmes are refused by the operation until the
product owner says otherwise.
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.visit_operations import parse_until


class Command(BaseCommand):
    help = "Post consumption from an opportunity's visits, per its dispensing rules."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True)
        parser.add_argument("--opportunity", type=int, required=True)
        parser.add_argument("--until", help="Read visits on or before this day (YYYY-MM-DD) only.")
        parser.add_argument("--refresh", action="store_true", help="Re-read visits rather than use the cached copy.")
        parser.add_argument("--dry-run", action="store_true", help="Run it all, report it, keep nothing.")

    def handle(self, *args, **options):
        try:
            parse_until(options.get("until"))
        except ValueError as error:
            raise CommandError(str(error)) from None
        access = SupplyDataAccess(
            access_token=os.environ.get("SUPPLY_EXPORT_TOKEN", ""),
            program_id=options["program"],
            opportunity_id=options["opportunity"],
            caller=SYSTEM,
        )
        payload = {
            "opportunity_id": options["opportunity"],
            "until": options.get("until"),
            "refresh": True if options["refresh"] else None,
        }
        with transaction.atomic():
            report = call_operation("visit_consumption_ingest", access, payload, channel="command")
            if options["dry_run"]:
                transaction.set_rollback(True)

        verb = "would post" if options["dry_run"] else "posted"
        self.stdout.write(
            f"{report['visits_read']} visit(s) read; {verb} {report['posted']} "
            f"({report['estimated']} estimated), reversed {report['reversed']}, "
            f"no answer {report['no_answer']}, nothing given {report['nothing_given']}, "
            f"not applicable {report['not_applicable']}, before the rule began {report['before_active_from']}"
        )
        for row in report["unmatched"]:
            self.stdout.write(self.style.WARNING(f"  unmatched: visit {row['visit_id']} -- {row['reason']}"))
        for row in report["unmapped"]:
            answers = "; ".join(f"{a['path']} = {a['answer']!r}" for a in row["answers"])
            self.stdout.write(
                self.style.WARNING(f"  unmapped: visit {row['visit_id']} item {row['item_id']} -- {answers}")
            )
        for row in report["unit_refused"]:
            reasons = "; ".join(row["reasons"])
            self.stdout.write(
                self.style.WARNING(f"  unit refused: visit {row['visit_id']} item {row['item_id']} -- {reasons}")
            )
        for row in report["skipped"]:
            self.stdout.write(
                self.style.WARNING(f"  skipped: visit {row['visit_id']} item {row['item_id']} -- {row['reason']}")
            )
        for visit_id in report["reinstated_after_reversal"]:
            self.stdout.write(self.style.WARNING(f"  reinstated after its reversal, not re-posted: visit {visit_id}"))
