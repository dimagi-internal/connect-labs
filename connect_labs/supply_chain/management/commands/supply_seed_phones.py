"""Seed the phones demo: an LLO buys a phone for every adult in three villages. Invented data; labs-only.

    make manage CMD="supply_seed_phones"           # once
    make manage CMD="supply_seed_phones --reset"   # rebuild it, dated from today

Against the deployed environment, through the task (the recipe in
docs/OUTBOUND_EMAIL.md):

    aws ecs execute-command --profile labs --region us-east-1 \\
      --cluster labs-jj-cluster --task <task-id> --container web --interactive \\
      --command "python manage.py supply_seed_phones"

Server-side on purpose: it uploads the
handover visits to Drive with the labs service account and dates history with
seed_overrides. See connect_labs/supply_chain/demo/phones.py.
"""

import json

from django.core.management.base import BaseCommand

from connect_labs.supply_chain.demo.phones import seed


class Command(BaseCommand):
    help = "Seed the phones-for-every-adult demo programme (labs-only, invented data)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Purge this demo programme and seed it again.")

    def handle(self, *args, **options):
        from connect_labs.labs.synthetic.gdrive import DriveClient

        summary = seed(drive=DriveClient(), reset=options["reset"])
        self.stdout.write(json.dumps(summary, indent=2, default=str))
