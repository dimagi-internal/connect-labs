"""Write the repository's research notes into the database.

Re-runnable: a note is matched on (indicator, topic) and overwritten. Notes
authored through the MCP that have no seed counterpart are left alone, so a
session's findings survive a reseed.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from connect_labs.labs.indicators import research
from connect_labs.labs.indicators.models import ResearchNote
from connect_labs.labs.indicators.research_seed import NOTES


class Command(BaseCommand):
    help = "Seed targeting research notes from research_seed.NOTES"

    def add_arguments(self, parser):
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Re-run every seed note's checks and report, without writing",
        )

    def handle(self, *args, **opts):
        written = 0
        for spec in NOTES:
            checks = spec.get("checks") or []
            results = [research.run_check(c) for c in checks]
            failed = [r for r in results if not r.holds]

            label = f"{spec['indicator'] or 'general'}/{spec['topic']}"
            if failed:
                self.stdout.write(self.style.WARNING(f"  {label}: {len(failed)}/{len(results)} checks fail"))
                for r in failed:
                    self.stdout.write(f"      {r.describes}: expected {r.expected!r}, got {r.actual!r} — {r.detail}")
            else:
                self.stdout.write(f"  {label}: {len(results)} checks pass")

            if opts["check_only"]:
                continue

            ResearchNote.objects.update_or_create(
                indicator=spec.get("indicator", ""),
                topic=spec["topic"],
                defaults={
                    "summary": spec["summary"],
                    "body": spec["body"],
                    "checks": checks,
                    "alternatives": spec.get("alternatives") or [],
                    "scanned_at": timezone.now() if spec.get("scanned_now") else None,
                    "author": "seed",
                },
            )
            written += 1

        if opts["check_only"]:
            self.stdout.write(self.style.SUCCESS(f"Checked {len(NOTES)} seed notes; nothing written."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{written} research notes seeded."))
