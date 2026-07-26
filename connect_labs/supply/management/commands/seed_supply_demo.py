"""Build the Operation End Starvation demo world.

Thin wrapper: the world itself lives in :mod:`connect_labs.supply.demo`, so it
can be seeded from a test or a shell without going through the CLI.
"""
import os

from django.core.management.base import BaseCommand

from connect_labs.supply.demo import STAFF, SUPPLIER_LOGIN, demo_password, seed_demo_world


class Command(BaseCommand):
    help = "Seed the Operation End Starvation demo world (idempotent, deterministic)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all supply_* demo data before seeding.",
        )

    def handle(self, *args, **options):
        summary = seed_demo_world(reset=options["reset"])
        shown_password = "<from SUPPLY_DEMO_PASSWORD>" if os.environ.get("SUPPLY_DEMO_PASSWORD") else demo_password()
        logins = ", ".join([SUPPLIER_LOGIN[0]] + [s[0] for s in STAFF])
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded OES demo world: {summary['suppliers']} suppliers, "
                f"{summary['qualifications']} qualifications, "
                f"{summary['solicitations']} solicitations, {summary['awards']} awards; "
                f"{summary['execution']}. "
                f"Logins: {logins} (password: {shown_password})"
            )
        )
