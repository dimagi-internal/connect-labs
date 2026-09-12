"""Pull worker-reported stock on hand from Connect and record it.

The path a worker's figure travels: they answer a stock question on the
CommCare deliver form, the submission reaches Connect, and this command reads
it back through the export API and posts it as a `self_reported` stock count.

Which form question holds the figure differs per programme, so
`--quantity-path` is required rather than guessed. There is no safe default:
reading the wrong path yields plausible numbers for the wrong quantity, which
is worse than reading nothing.

Safe to re-run and safe to schedule. Ingestion is idempotent on the form
submission id, so a widened date window or a retried job re-reads without
double-posting.

    make manage CMD="supply_ingest_stock_reports \\
        --program 10501 --opportunity 10501 \\
        --commodity rutf --unit carton \\
        --quantity-path form.stock.cartons_on_hand"

    # from a saved export, for a dry run or a backfill
    make manage CMD="supply_ingest_stock_reports ... --from-json visits.json"
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.integrations.connect.export_client import ExportAPIClient
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services.ingest import extract_rows


class Command(BaseCommand):
    help = "Read worker-reported stock on hand from Connect visits and record it."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True)
        parser.add_argument("--opportunity", type=int, required=True)
        parser.add_argument("--commodity", required=True, help="Commodity slug, e.g. rutf")
        parser.add_argument("--unit", required=True, help="The unit workers report in, e.g. carton")
        parser.add_argument(
            "--quantity-path",
            required=True,
            help="Dotted path to the form question holding stock on hand, " "e.g. form.stock.cartons_on_hand",
        )
        parser.add_argument("--item-sku", help="Restrict the count to one trade item.")
        parser.add_argument("--since", help="Only visits on or after this date (YYYY-MM-DD).")
        parser.add_argument(
            "--from-json",
            help="Read visits from a file instead of Connect -- for a backfill or a dry run.",
        )
        parser.add_argument(
            "--create-missing-points",
            action="store_true",
            help="Create a user_held supply point for a username that has none. Off by "
            "default: a typo would become a phantom worker holding phantom stock.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would be recorded.")

    def handle(self, *args, **options):
        access = SupplyDataAccess(
            access_token=self._token(options),
            program_id=options["program"],
            opportunity_id=options["opportunity"],
        )

        visits = self._visits(options)
        rows = extract_rows(visits, quantity_path=options["quantity_path"])
        self.stdout.write(f"{len(visits)} visit(s) read, {len(rows)} carrying a stock figure")
        if not rows:
            self.stdout.write(
                self.style.WARNING(
                    f"no visit answered {options['quantity_path']!r}. Check the path against the "
                    "form -- a wrong one reads as 'nobody reported', which looks identical to "
                    "nobody having reported."
                )
            )
            return

        item = access.get_item_by_sku(options["item_sku"]) if options.get("item_sku") else None
        if options.get("item_sku") and item is None:
            raise CommandError(f"no trade item with sku {options['item_sku']!r} in this scope")

        if options["dry_run"]:
            for row in rows[:20]:
                self.stdout.write(
                    f"  would record {row['quantity']} {options['unit']} for "
                    f"{row['connect_username']} on {row['counted_on']}"
                )
            if len(rows) > 20:
                self.stdout.write(f"  ... and {len(rows) - 20} more")
            return

        report = call_operation(
            "stock_report_ingest",
            access,
            {
                "rows": rows,
                "commodity_slug": options["commodity"],
                "quantity_unit": options["unit"],
                "opportunity_id": options["opportunity"],
                **({"item_id": item.pk} if item else {}),
                "create_missing_points": options["create_missing_points"],
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"recorded {report['created']}, " f"skipped {report['skipped_already_ingested']} already ingested"
            )
        )
        if report["created_supply_points"]:
            self.stdout.write(f"created {len(report['created_supply_points'])} user_held supply point(s)")
        for unmatched in report["unmatched"]:
            self.stdout.write(
                self.style.WARNING(f"  unmatched: {unmatched['connect_username']} -- {unmatched['reason']}")
            )

    def _visits(self, options):
        if options.get("from_json"):
            payload = json.loads(Path(options["from_json"]).read_text())
            return payload["results"] if isinstance(payload, dict) else payload

        token = self._token(options)
        params = {}
        if options.get("since"):
            params["visit_date__gte"] = options["since"]
        visits = []
        with ExportAPIClient(base_url=self._base_url(), access_token=token) as client:
            for page in client.paginate(f"/export/opportunity/{options['opportunity']}/user_visits/", params=params):
                visits.extend(page)
        return visits

    def _base_url(self):
        url = getattr(settings, "CONNECT_PRODUCTION_URL", None)
        if not url:
            raise CommandError(
                "no Connect base URL in settings (CONNECT_PRODUCTION_URL). Use --from-json to "
                "ingest a saved export instead."
            )
        return url

    def _token(self, options):
        """An export-scoped token, or empty when reading from a file.

        Deliberately read from the environment rather than taken as an
        argument: a token on a command line lands in the shell history and
        the process list.
        """
        import os

        token = os.environ.get("SUPPLY_EXPORT_TOKEN", "")
        if not token and not options.get("from_json"):
            raise CommandError(
                "set SUPPLY_EXPORT_TOKEN to an OAuth token with the export scope, or pass "
                "--from-json to ingest a saved export."
            )
        return token
