"""PHASE 2 (offline): generate fixtures from bundles and register labs-only opps.

Spec-driven (recommended) — the same YAML you used for Phase 1::

    python manage.py synthetic_generate_opps --spec kmc.yaml [--fresh]

Explicit flags (no spec)::

    python manage.py synthetic_generate_opps \\
        --bundles gdrive:<folder_id> \\
        --program "KMC (Synthetic)" --org "Dimagi-KMC (Synthetic)" [--fresh]
"""

from django.core.management.base import BaseCommand, CommandError

from commcare_connect.labs.synthetic.clone_from_prod import generate_cohort, generate_opps_bulk
from commcare_connect.labs.synthetic.cohort import load_cohort_spec, save_cohort_spec
from commcare_connect.labs.synthetic.gdrive import DriveClient


class Command(BaseCommand):
    help = "PHASE 2 (offline): generate fixtures from bundles + register labs-only opps."

    def add_arguments(self, parser):
        parser.add_argument("--spec", help="Path to the cohort spec YAML used for Phase 1.")
        parser.add_argument("--bundles", help="bundle_root (if not using --spec): a path or 'gdrive:<id>'.")
        parser.add_argument("--program", default="KMC (Synthetic)", help="Program name for the generated opps.")
        parser.add_argument("--org", default="Dimagi-KMC (Synthetic)", help="Org name for the generated opps.")
        parser.add_argument("--fresh", action="store_true", help="Re-generate even if the opp already exists.")

    def _print(self, results, summary):
        for r in results:
            self.stdout.write(
                f"  {r.source_opportunity_id} -> {r.opportunity_id} ({'skipped' if r.skipped else 'generated'})"
            )
        self.stdout.write(self.style.SUCCESS(summary))

    def handle(self, *args, **opts):
        if opts.get("spec"):
            spec = load_cohort_spec(opts["spec"])
            spec, results = generate_cohort(spec, drive=DriveClient(), fresh=opts["fresh"])
            save_cohort_spec(opts["spec"], spec)
            self._print(results, f"Done: {len(results)} opps under program_id {spec.program_id}.")
            return

        if not opts.get("bundles"):
            raise CommandError("Provide --spec, or --bundles.")
        results = generate_opps_bulk(
            opts["bundles"],
            drive=DriveClient(),
            program_name=opts["program"],
            org_name=opts["org"],
            fresh=opts["fresh"],
        )
        self._print(results, f"Done: {len(results)} opps under '{opts['program']}'.")
