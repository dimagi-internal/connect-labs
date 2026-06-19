"""PHASE 2 (offline): generate fixtures from bundles and register labs-only opps.

Usage::

    python manage.py synthetic_generate_opps \\
        --bundles /tmp/bundles \\
        --program "KMC (Synthetic)" \\
        --org "Dimagi-KMC (Synthetic)" \\
        [--fresh]
"""

from django.core.management.base import BaseCommand

from commcare_connect.labs.synthetic.clone_from_prod import generate_opps_bulk
from commcare_connect.labs.synthetic.gdrive import DriveClient


class Command(BaseCommand):
    help = "PHASE 2 (offline): generate fixtures from bundles + register labs-only opps."

    def add_arguments(self, parser):
        parser.add_argument("--bundles", required=True, help="Directory of per-opp bundle files.")
        parser.add_argument("--program", default="KMC (Synthetic)", help="Program name for the generated opps.")
        parser.add_argument("--org", default="Dimagi-KMC (Synthetic)", help="Org name for the generated opps.")
        parser.add_argument("--fresh", action="store_true", help="Re-generate even if the opp already exists.")

    def handle(self, *args, **opts):
        results = generate_opps_bulk(
            opts["bundles"],
            drive=DriveClient(),
            program_name=opts["program"],
            org_name=opts["org"],
            fresh=opts["fresh"],
        )
        for r in results:
            self.stdout.write(
                f"  {r.source_opportunity_id} -> {r.opportunity_id} " f"({'skipped' if r.skipped else 'generated'})"
            )
        self.stdout.write(self.style.SUCCESS(f"Done: {len(results)} opps under '{opts['program']}'."))
