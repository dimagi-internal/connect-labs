"""Seed local demo data for the Photo Verification Success Rate report.

    python manage.py seed_photo_verification_demo

Creates two labs-only synthetic opps (EHA=10001, C3HD=10002) with a known
audit-session mix, so the report can be exercised locally with no prod
access. Idempotent. See connect_labs.audit.photo_verification_demo.
"""

from django.core.management.base import BaseCommand

from connect_labs.audit.photo_verification_demo import seed_demo


class Command(BaseCommand):
    help = "Seed local synthetic opps + audit sessions for the Photo Verification report."

    def handle(self, *args, **options):
        demo = seed_demo()
        exp = demo["expected"]
        self.stdout.write(self.style.SUCCESS("Seeded Photo Verification demo data:"))
        for key, label in (("eha", "EHA"), ("c3hd", "C3HD"), ("combined", "Combined")):
            e = exp[key]
            self.stdout.write(f"  {label}: expect {e['rate']}%  ({e['pass']}/{e['denom']})")
        self.stdout.write(f"  Auditor filter: {demo['auditor']!r}   Audit IDs: {demo['ids_csv']}")
