"""Create a database-backed semantic registry from an on-disk one."""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.validation import RegistryInvalid
from connect_labs.workflow.data_access import SemanticRegistryDataAccess


class Command(BaseCommand):
    help = "Seed a semantic_registry record from an on-disk registry directory."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="kmc", help="on-disk registry directory (default: kmc)")
        parser.add_argument("--opportunity-id", type=int, default=None)
        parser.add_argument("--program-id", type=int, default=None)
        parser.add_argument("--access-token", default=None)
        parser.add_argument("--shared", action="store_true", help="mark the record shared on creation")

    def handle(self, *args, **options):
        try:
            payload = registry_payload(options["name"])
        except RegistryInvalid as exc:
            raise CommandError(
                "the on-disk registry does not validate, so it will not be seeded:\n  " + "\n  ".join(exc.errors)
            ) from exc

        access = SemanticRegistryDataAccess(
            opportunity_id=options["opportunity_id"],
            program_id=options["program_id"],
            access_token=options["access_token"],
        )
        try:
            record = access.create_registry(
                name=payload["name"],
                description=payload["description"],
                properties=payload["properties"],
                indicators=payload["indicators"],
                deployment=payload["deployment"],
                is_shared=options["shared"],
            )
        finally:
            access.close()

        self.stdout.write(
            self.style.SUCCESS(
                f"created semantic registry {record.id} "
                f"({len(payload['indicators'].get('measures', []))} measures). "
                f'Bind it with registry_source={{"registry_id": {record.id}}}.'
            )
        )
