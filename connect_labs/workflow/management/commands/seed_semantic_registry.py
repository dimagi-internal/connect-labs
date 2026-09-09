"""Create -- or REFRESH -- a database-backed semantic registry from an on-disk one.

Refresh exists because its absence was a one-way door. A record is SEEDED from the
on-disk registry and then edited live, which is the point; but when the on-disk seed
gains a field the record cannot be brought forward, and there was no path to do it
other than pushing whole documents back through the API by hand.

That bit for real. `app_asks`, `asks_as` and each measure's `inputs` /
`min_input_coverage` moved out of Python into the registry, and workflow 5456 was
bound to a record seeded BEFORE they existed. The record was missing all of them, so
the availability gate had nothing to gate on and failed open, and the thin-coverage
footnote could never fire -- on a dashboard whose on-disk registry had every field.
Green in the repo, different in production, and nothing in between to notice.

`--registry-id` refreshes in place. The payload is validated first either way, so a
seed that does not compile cannot reach a dashboard.

Reads the registry from the SERVER's own disk, which is the point: bringing a record
forward by hand means re-typing whole documents through the API, and a transcription
slip in a 22-opportunity availability map fails open and silently. Invoke against
production via run-labs-command.yml with `--owner-email`, e.g.

    seed_semantic_registry --registry-id 5500 --opportunity-id 10042 \
        --owner-email ace@dimagi-ai.com
"""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.validation import RegistryInvalid
from connect_labs.users.models import User
from connect_labs.workflow.data_access import SemanticRegistryDataAccess


class Command(BaseCommand):
    help = "Seed a semantic_registry record from an on-disk registry directory."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="kmc", help="on-disk registry directory (default: kmc)")
        parser.add_argument("--opportunity-id", type=int, default=None)
        parser.add_argument("--program-id", type=int, default=None)
        parser.add_argument("--access-token", default=None)
        parser.add_argument(
            "--owner-email",
            default=None,
            help=(
                "mint the Connect token from this user's persisted UserConnectToken, "
                "the same mechanism run_scheduled_workflow uses. Use this instead of "
                "--access-token when invoking via run-labs-command.yml, so no token "
                "appears in the dispatch input."
            ),
        )
        parser.add_argument("--shared", action="store_true", help="mark the record shared on creation")
        parser.add_argument(
            "--registry-id",
            type=int,
            default=None,
            help="refresh this existing record in place instead of creating a new one",
        )

    def handle(self, *args, **options):
        try:
            payload = registry_payload(options["name"])
        except RegistryInvalid as exc:
            raise CommandError(
                "the on-disk registry does not validate, so it will not be seeded:\n  " + "\n  ".join(exc.errors)
            ) from exc

        access_token = options["access_token"]
        if not access_token and options["owner_email"]:
            try:
                owner = User.objects.get(email=options["owner_email"])
            except User.DoesNotExist:
                raise CommandError(f"No user with email {options['owner_email']!r}")
            try:
                access_token = get_valid_access_token(owner)
            except ConnectTokenError as exc:
                raise CommandError(str(exc)) from exc

        access = SemanticRegistryDataAccess(
            opportunity_id=options["opportunity_id"],
            program_id=options["program_id"],
            access_token=access_token,
        )
        registry_id = options["registry_id"]
        try:
            if registry_id is not None:
                before = access.get_registry(registry_id)
                if before is None:
                    raise CommandError(f"no semantic registry with id {registry_id} is visible at this scope")
                record = access.update_registry(
                    registry_id,
                    properties=payload["properties"],
                    indicators=payload["indicators"],
                    deployment=payload["deployment"],
                )
                if record is None:
                    raise CommandError(f"registry {registry_id} was not updated")
            else:
                record = access.create_registry(
                    name=payload["name"],
                    description=payload["description"],
                    properties=payload["properties"],
                    indicators=payload["indicators"],
                    deployment=payload["deployment"],
                    is_shared=options["shared"],
                )
                before = None
        finally:
            access.close()

        measures = len(payload["indicators"].get("measures", []))
        if before is not None:
            # Report what the refresh actually CHANGED, not just that it ran. The
            # failure this command now prevents was invisible precisely because a
            # stale record looks identical to a fresh one from the outside.
            dep = payload["deployment"] or {}
            gated = sum(1 for m in payload["indicators"].get("measures", []) if (m.get("meta") or {}).get("inputs"))
            self.stdout.write(
                self.style.SUCCESS(
                    f"refreshed semantic registry {record.id} "
                    f"(v{getattr(before, 'version', '?')} -> v{getattr(record, 'version', '?')}): "
                    f"{measures} measures, "
                    f"{len(dep.get('app_asks') or {})} app_asks opportunities, "
                    f"{len(dep.get('llo_map') or {})} llo_map entries, "
                    f"{gated} measures declaring gate inputs."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"created semantic registry {record.id} ({measures} measures). "
                    f'Bind it with registry_source={{"registry_id": {record.id}}}.'
                )
            )
