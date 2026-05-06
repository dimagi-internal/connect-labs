"""Force-set the server-side ``public`` ACL flag on solicitations.

Background: there are two distinct flags on a solicitation record —
``data.is_public`` (application JSON) and ``LabsRecord.public`` (server-side
ACL column the marketplace listing filters on). Several existing records have
``data.is_public=True`` but ``public=False``, so they don't surface on the
public ``/solicitations/`` listing despite looking public to MCP callers.

This command issues a bare upsert (``{"id": <id>, "public": true}``) for each
ID. ``update_or_create`` on the server only writes the keys present in
``defaults``, so all other fields (data, experiment, scope FKs) are preserved.

Usage:
    python manage.py fix_solicitation_public --ids 2288 2227 2505 2837 2841
    python manage.py fix_solicitation_public --ids 2841 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.labs.integrations.connect.cli.token_manager import TokenManager


class Command(BaseCommand):
    help = "Set LabsRecord.public=True on the given solicitation IDs (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids",
            nargs="+",
            type=int,
            required=True,
            help="One or more solicitation LabsRecord IDs to patch.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be patched without sending the request.",
        )

    def handle(self, *args, **options):
        ids: list[int] = options["ids"]
        dry_run: bool = options["dry_run"]

        tm = TokenManager()
        token = tm.get_valid_token()
        if not token:
            raise CommandError(
                "No valid Connect token. Run `python -m commcare_connect.labs.integrations.connect.cli login` first."
            )

        client = LabsRecordAPIClient(access_token=token)
        url = f"{client.base_url}/export/labs_record/"

        try:
            for sol_id in ids:
                if dry_run:
                    self.stdout.write(f"[dry-run] would POST {{id: {sol_id}, public: true}}")
                    continue

                resp = client.http_client.post(url, json=[{"id": sol_id, "public": True}])
                if resp.status_code >= 400:
                    self.stdout.write(self.style.ERROR(f"  {sol_id}: HTTP {resp.status_code} — {resp.text[:200]}"))
                    continue

                result = resp.json()
                if not result:
                    self.stdout.write(self.style.WARNING(f"  {sol_id}: empty response"))
                    continue

                record = result[0]
                self.stdout.write(
                    self.style.SUCCESS(f"  {sol_id}: public={record.get('public')} type={record.get('type')}")
                )
        finally:
            client.close()
