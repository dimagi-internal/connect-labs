"""Seed a programme's catalogue with the CMAM reference set.

A management command and not an MCP tool, which is how every other bootstrap
in this codebase is reached -- `bootstrap_targeting`, `load_indicators`,
`targeting_import`, `marketplace_import`, `seed_semantic_registry`,
`seed_campaign_demo`. Seeding is an engineer's deliberate act against one
programme, not something to leave one mistaken tool call away from every MCP
client.

The operation still exists and is still `internal=True`, so this goes through
`call_operation` and keeps the schema validation and provenance stamping that
every other write gets.

    make manage CMD="supply_catalogue_seed --program 10063 --dry-run"
    make manage CMD="supply_catalogue_seed --program 10063"

Against the deployed environment, through the task (see
docs/OUTBOUND_EMAIL.md for the full recipe):

    aws ecs execute-command --profile labs --region us-east-1 \\
      --cluster labs-jj-cluster --task <task-id> --container web --interactive \\
      --command "python manage.py supply_catalogue_seed --program 10063"
"""

from django.core.management.base import BaseCommand

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation


class Command(BaseCommand):
    help = "Add the CMAM reference catalogue to a programme. Existing rows are left alone."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be added without writing anything.",
        )

    def handle(self, *args, **options):
        access = SupplyDataAccess(access_token="local", program_id=options["program"])
        report = call_operation("catalogue_seed", access, {"dry_run": options["dry_run"]})

        added = report["products_added"] + report["trade_items_added"]
        kept = report["products_already_present"] + report["trade_items_already_present"]

        verb = "would add" if report["dry_run"] else "added"
        self.stdout.write(f"{verb} {len(added)} row(s): {', '.join(added) or '—'}")
        self.stdout.write(f"left alone {len(kept)} already present: {', '.join(kept) or '—'}")

        # Refusals are the half worth reading: a trade item whose product is
        # absent is skipped rather than raising, so without this it is silent.
        if report["refused"]:
            self.stdout.write(f"\n{len(report['refused'])} refused:")
            for line in report["refused"]:
                self.stdout.write(self.style.WARNING(f"  - {line}"))

        if added and not report["dry_run"]:
            self.stdout.write(self.style.SUCCESS("\ndone"))
