"""Seed the synthetic stock-from-visits RUTF opportunity. Invented data; labs-only.

    make manage CMD="supply_seed_stock_from_visits"           # once
    make manage CMD="supply_seed_stock_from_visits --reset"   # rebuild it

Against the deployed environment, through the task (the recipe in
docs/OUTBOUND_EMAIL.md):

    aws ecs execute-command --profile labs --region us-east-1 \\
      --cluster labs-jj-cluster --task <task-id> --container web --interactive \\
      --command "python manage.py supply_seed_stock_from_visits"

Server-side on purpose: it uploads the visit fixtures to Drive with the labs
service account (LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID), and dates history
with seed_overrides, which exists only in-process. See
connect_labs/supply_chain/demo/README.md.
"""

import json

from django.core.management.base import BaseCommand

from connect_labs.supply_chain.demo.stock_from_visits import seed


class Command(BaseCommand):
    help = "Seed the synthetic stock-from-visits RUTF opportunity (labs-only, invented data)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Purge this synthetic programme and seed it again.")

    def handle(self, *args, **options):
        from connect_labs.labs.synthetic.gdrive import DriveClient

        summary = seed(drive=DriveClient(), reset=options["reset"])
        self.stdout.write(json.dumps(summary, indent=2, default=str))
