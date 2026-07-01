from django.core.management.base import BaseCommand

from connect_labs.campaign.services import geography, seed, synthetic_campaign


class Command(BaseCommand):
    help = "Seed the Campaign Utility Tool dataset (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--fresh", action="store_true", help="Delete and rebuild the campaign.")
        parser.add_argument(
            "--workers",
            type=int,
            default=64,
            help="Number of synthetic workers to generate (default 64). Use a larger value "
            "to exercise the UX at realistic scale.",
        )
        parser.add_argument(
            "--national",
            action="store_true",
            help="Build a national-scale campaign on real Nigeria geography (labs AdminBoundary) "
            "with workers generated as CommCare cases. Requires NGA boundaries loaded.",
        )
        parser.add_argument(
            "--states",
            type=int,
            default=None,
            help="With --national: cap the number of states the roster spreads across "
            "(default: all loaded, i.e. full national).",
        )

    def handle(self, *args, **options):
        if options["national"]:
            if not geography.is_loaded():
                self.stderr.write(
                    self.style.ERROR(
                        "NGA admin boundaries not loaded. Run " "`manage.py load_geopode_from_drive --iso NGA` first."
                    )
                )
                return
            c = synthetic_campaign.build_synthetic_campaign(
                worker_count=options["workers"], states_limit=options["states"]
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Built national campaign {c.code}: {c.workers.count()} workers across "
                    f"{c.regions.count()} states, {c.microplans.count()} microplans."
                )
            )
            return
        c = seed.seed_campaign(fresh=options["fresh"], worker_count=options["workers"])
        self.stdout.write(self.style.SUCCESS(f"Seeded campaign {c.code} with {c.workers.count()} workers."))
