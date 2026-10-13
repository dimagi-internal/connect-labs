"""Seed the RUTF supply chain behind a synthetic clone of the real RUTF opportunity.

    make manage CMD="supply_seed_clone_supply --program 10700 --opportunity 10100"

Stores, the RUTF dispensing rule on the released app's paths, a supply point per
worker, invented fortnightly distributions to each worker (labelled as invented
on every record), and the visit reader run week by week to today. See
supply_chain/demo/clone_supply.py. Refuses anything but a labs-only programme
and one of its own labs-only opportunities. --reset purges that programme's
supply data and seeds it again.

Every run -- with --reset or without -- also makes or refreshes what the demo is
seen through (demo/clone_pages.py): the Stock review and Forecast tabs, following
their templates; the program's home page at /labs/p/programme/<id>/; and, if it has
none, the organisation's home. Without --reset on a seeded clone, that is all it does.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.supply_chain.demo import clone_supply


class Command(BaseCommand):
    help = "Seed the RUTF supply chain behind a synthetic clone of the real RUTF opportunity."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True)
        parser.add_argument("--opportunity", type=int, required=True)
        parser.add_argument("--reset", action="store_true", help="Purge this programme's supply data first.")
        parser.add_argument(
            "--top-up", action="store_true", help="Only record the invented deliveries due since (daily on beat)."
        )

    def handle(self, *args, **options):
        try:
            if options["top_up"]:
                result = clone_supply.top_up(program_id=options["program"], opportunity_id=options["opportunity"])
            else:
                result = clone_supply.seed(
                    program_id=options["program"], opportunity_id=options["opportunity"], reset=options["reset"]
                )
        except ValueError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(result, indent=2))
