"""One-off probe: does organization-scoped LabsRecord CRUD round-trip?

Writes a throwaway record scoped to --organization-id, reads it back with the
same scope, checks an unscoped client cannot see it, then deletes it.

    make manage CMD="supply_scope_probe --organization-id 42 --token $TOKEN"
"""

from django.core.management.base import BaseCommand

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient


class Command(BaseCommand):
    help = "Probe whether organization-scoped LabsRecord writes round-trip."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", required=True)
        parser.add_argument("--token", required=True)

    def handle(self, *args, **options):
        raw = options["organization_id"]
        try:
            org_id = int(raw)
        except ValueError:
            org_id = raw
            self.stdout.write(
                self.style.WARNING(
                    f"organization_id {raw!r} is not an integer. create_record() drops "
                    "a non-int organization_id, so this write will be UNSCOPED."
                )
            )

        client = LabsRecordAPIClient(access_token=options["token"], organization_id=org_id)
        created = client.create_record(
            experiment="supply",
            type="supply_scope_probe",
            data={"probe": True},
        )
        self.stdout.write(f"created id={created.id} organization_id={created.organization_id!r}")

        found = client.get_records(experiment="supply", type="supply_scope_probe")
        self.stdout.write(f"read back {len(found)} record(s) with an org-scoped client")

        unscoped = LabsRecordAPIClient(access_token=options["token"])
        leaked = unscoped.get_records(experiment="supply", type="supply_scope_probe")
        self.stdout.write(f"read back {len(leaked)} record(s) with an UNSCOPED client")

        client.delete_record(created.id)
        self.stdout.write(self.style.SUCCESS("deleted probe record"))
