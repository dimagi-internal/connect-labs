"""Link the organisation registry to Connect's organisations.

Runs daily on beat (`labs.tasks.sync_connect_organizations`); run it by hand
after a Connect merge, or with --dry-run to see what it would change.
"""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs import connect_orgs


class Command(BaseCommand):
    help = "Read Connect's organisations and link LabsOrg rows to them"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")

    def handle(self, *args, **opts):
        from connect_labs.marketplace import connect_profile
        from connect_labs.pulse.client import PulseAuthError, get_client

        try:
            client = get_client()
        except PulseAuthError as exc:
            raise CommandError(f"No Connect token to read with (runs as the pulse poller): {exc}") from exc

        with client:
            report = connect_orgs.sync(client, dry_run=opts["dry_run"])
        profiles = 0 if opts["dry_run"] else connect_profile.apply_all()

        prefix = "would link" if opts["dry_run"] else "linked"
        for line in report.linked:
            self.stdout.write(f"  {prefix}: {line}")
        for line in report.relinked:
            self.stdout.write(f"  relinked: {line}")
        for line in report.slug_refreshed:
            self.stdout.write(f"  slug: {line}")
        findings = report.findings()
        if findings:
            self.stdout.write(self.style.WARNING(f"{len(findings)} findings:"))
            for line in findings:
                self.stdout.write(f"  {line}")
        summary = ", ".join(f"{k}={v}" for k, v in report.summary().items())
        self.stdout.write(self.style.SUCCESS(f"{summary}, profiles_updated={profiles}"))
