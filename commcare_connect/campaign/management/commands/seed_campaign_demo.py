from django.core.management.base import BaseCommand

from commcare_connect.campaign.services import seed


class Command(BaseCommand):
    help = "Seed the Campaign Utility Tool demo dataset (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--fresh", action="store_true", help="Delete and rebuild the campaign.")
        parser.add_argument(
            "--workers",
            type=int,
            default=64,
            help="Number of synthetic workers to generate (default 64). Use a larger value "
            "to exercise the UX at realistic scale.",
        )

    def handle(self, *args, **options):
        c = seed.seed_campaign(fresh=options["fresh"], worker_count=options["workers"])
        self.stdout.write(self.style.SUCCESS(f"Seeded campaign {c.code} with {c.workers.count()} workers."))
