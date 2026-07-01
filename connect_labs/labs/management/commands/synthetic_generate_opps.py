"""PHASE 2 (offline): generate fixtures from bundles and register labs-only opps.

PREFERRED (local compute, no prod DB) — run the heavy generation on a fast machine
with only the GDrive service-account creds, then repoint over the connect_labs MCP::

    LABS_SYNTHETIC_GDRIVE_SA_KEY=<...> LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID=<...> \\
        python manage.py synthetic_generate_opps --spec kmc.yaml --no-register
    # prints `source_opp -> gdrive_folder_id` per opp; then for each line call the MCP:
    #   synthetic_repoint_by_source(source_opportunity_id=<src>, gdrive_folder_id=<folder>)
    #     -> overwrites the EXISTING labs opp (matched by cloned_from) in place, OR
    #   synthetic_create_labs_only(gdrive_folder_id=<folder>, ...) for a brand-new opp.
    # This keeps all DB writes server-side (no prod DB on the generating box) and avoids
    # the slow / timeout-prone server-side generation path.

Spec-driven, server-side (needs the labs DB — e.g. running inside labs)::

    python manage.py synthetic_generate_opps --spec kmc.yaml [--fresh]

Explicit flags (no spec)::

    python manage.py synthetic_generate_opps \\
        --bundles gdrive:<folder_id> \\
        --program "KMC (Synthetic)" --org "Dimagi-KMC (Synthetic)" [--fresh]
"""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.synthetic.clone_from_prod import generate_cohort, generate_fixtures_only, generate_opps_bulk
from connect_labs.labs.synthetic.cohort import load_cohort_spec, save_cohort_spec
from connect_labs.labs.synthetic.gdrive import DriveClient


class Command(BaseCommand):
    help = "PHASE 2 (offline): generate fixtures from bundles + register labs-only opps."

    def add_arguments(self, parser):
        parser.add_argument("--spec", help="Path to the cohort spec YAML used for Phase 1.")
        parser.add_argument("--bundles", help="bundle_root (if not using --spec): a path or 'gdrive:<id>'.")
        parser.add_argument("--program", default="KMC (Synthetic)", help="Program name for the generated opps.")
        parser.add_argument("--org", default="Dimagi-KMC (Synthetic)", help="Org name for the generated opps.")
        parser.add_argument("--fresh", action="store_true", help="Re-generate even if the opp already exists.")
        parser.add_argument(
            "--no-register",
            action="store_true",
            help="Generate fixtures to GDrive ONLY — write no database rows. Prints each "
            "source_opp -> gdrive_folder_id; then over the connect_labs MCP either "
            "synthetic_repoint_by_source (overwrite the existing cloned opp in place) or "
            "synthetic_create_labs_only (new opp). Lets the heavy generation run locally "
            "without DB access — the PREFERRED path.",
        )

    def _print(self, results, summary):
        for r in results:
            self.stdout.write(
                f"  {r.source_opportunity_id} -> {r.opportunity_id} ({'skipped' if r.skipped else 'generated'})"
            )
        self.stdout.write(self.style.SUCCESS(summary))

    def handle(self, *args, **opts):
        if opts.get("no_register"):
            bundle_root = load_cohort_spec(opts["spec"]).bundle_root if opts.get("spec") else opts.get("bundles")
            if not bundle_root:
                raise CommandError("--no-register needs --spec or --bundles to locate the bundles.")
            rows = generate_fixtures_only(bundle_root, drive=DriveClient())
            for r in rows:
                self.stdout.write(
                    f"  {r['source_opportunity_id']} -> {r['gdrive_folder_id']}  ({r['visit_count']} visits)"
                )
            self.stdout.write(
                self.style.SUCCESS(f"Generated {len(rows)} fixture sets to GDrive (no DB rows written).")
            )
            self.stdout.write(
                "Next, over the connect_labs MCP, per printed line:\n"
                "  - overwrite an existing cloned opp in place: "
                "synthetic_repoint_by_source(source_opportunity_id=<src>, gdrive_folder_id=<folder>)\n"
                "  - or register a brand-new opp: synthetic_create_labs_only(gdrive_folder_id=<folder>, ...)"
            )
            return

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
