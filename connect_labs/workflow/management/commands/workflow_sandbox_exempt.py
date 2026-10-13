"""Record, remove or list a workflow kept out of the sandbox frame under ``LABS_WORKFLOW_SANDBOX=all``.

For a live copy whose render code needs what the frame will never give it -- a retired
endpoint, a definition write -- and that is kept only to stay readable. The ruling
(spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Recorded exemptions")
is not to open routes for those, but to record each copy by hand, with why.

The row (``workflow.models.SandboxExemption``) holds for THIS definition, on THIS owner,
drawing THIS code: the command reads the definition and the render code the run page
would draw (``render_source.resolve_render_code``) server-side, AS the named user with
their stored Connect login -- the same way ``workflow_sandbox_scan --as-user`` reads
real-opportunity records -- and records the digest of that code. Any later edit voids
it, and the workflow is sandboxed again. A page is never exempt. Nothing else (no MCP
tool, no view) writes these rows.

    workflow_sandbox_exempt --definition 1035 --opportunity 740 --as-user jonathan \\
        --reason "deprecated MBW monitoring copy, edited; needs retired /custom_analysis/ endpoints"
    workflow_sandbox_exempt --definition 1035 --remove --as-user jonathan
    workflow_sandbox_exempt --list

On prod: ``gh workflow run run-labs-command.yml ... --field command="workflow_sandbox_exempt ..."``.
Every add and remove is recorded in the audit trail (``audit_trail.service.record``).
Prints ids, owners, digests and reasons; never code.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.audit_trail.context import audit_context
from connect_labs.audit_trail.models import Action
from connect_labs.audit_trail.service import record

RESOURCE_TYPE = "sandbox_exemption"


class Command(BaseCommand):
    help = "Record (or --remove, --list) a workflow kept out of the sandbox frame under LABS_WORKFLOW_SANDBOX=all."

    def add_arguments(self, parser):
        parser.add_argument("--definition", type=int, help="The workflow definition's id.")
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument("--opportunity", type=int, help="The opportunity that owns the definition.")
        scope.add_argument("--program", type=int, help="The program that owns the definition.")
        scope.add_argument("--organization", type=int, help="The organization that owns the definition.")
        parser.add_argument(
            "--as-user",
            metavar="USERNAME",
            help="Read the definition as this labs user (their stored Connect login); recorded as added_by.",
        )
        parser.add_argument("--reason", help="Why this copy stays out of the frame (required to add).")
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--remove", action="store_true", help="Delete the exemption for --definition.")
        action.add_argument("--list", action="store_true", help="Print every recorded exemption.")

    def handle(self, *args, **options):
        if options["list"]:
            return self._list()
        if options["definition"] is None:
            raise CommandError("--definition is required (or --list)")
        if options["remove"]:
            return self._remove(options["definition"], self._user(options["as_user"], who="who is removing it"))
        return self._add(options)

    # ------------------------------------------------------------------

    def _user(self, username, who="the definition is read as that user"):
        if not username:
            raise CommandError(f"--as-user is required: {who}")
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"no labs user {username!r}")
        return user

    def _list(self):
        from connect_labs.workflow.models import SandboxExemption

        rows = list(SandboxExemption.objects.all())
        for row in rows:
            self.stdout.write(
                f"{row.definition_id} | {row.owner()} | sha256 {row.render_code_sha256[:12]} | "
                f"added by {row.added_by} {row.created_at:%Y-%m-%d} | {row.reason}"
            )
        self.stdout.write(f"{len(rows)} recorded exemption(s)")

    def _remove(self, definition_id, user):
        from connect_labs.workflow.models import SandboxExemption

        row = SandboxExemption.objects.filter(definition_id=definition_id).first()
        if row is None:
            raise CommandError(f"no recorded exemption for definition {definition_id}")
        owner = row.owner()
        with audit_context(user=user, source="script"):
            row.delete()
            record(
                Action.DELETE,
                resource_type=RESOURCE_TYPE,
                resource_id=definition_id,
                opportunity_id=row.opportunity_id,
                program_id=row.program_id,
                metadata={"owner": owner, "render_code_sha256": row.render_code_sha256},
                user=user,
            )
        self.stdout.write(f"removed: definition {definition_id} ({owner}) is sandboxed under `all` again")

    def _add(self, options):
        from connect_labs.workflow.models import SandboxExemption
        from connect_labs.workflow.sandbox import definition_owner, render_code_digest

        definition_id = options["definition"]
        reason = (options["reason"] or "").strip()
        if not reason:
            raise CommandError("--reason is required: say why this copy stays out of the frame")
        scope = {
            "opportunity_id": options["opportunity"],
            "program_id": options["program"],
            "organization_id": options["organization"],
        }
        scope = {k: v for k, v in scope.items() if v not in (None, "")}
        if not scope:
            raise CommandError("name the definition's owner: --opportunity, --program or --organization")
        user = self._user(options["as_user"])

        definition, code = self._read(user, definition_id, scope)
        ((key, value),) = scope.items()
        expected = f"{key.split('_')[0]}:{value}"
        actual = definition_owner(definition)
        if actual != expected:
            raise CommandError(f"definition {definition_id} is owned by {actual}, not {expected}")

        from connect_labs.workflow.page_mode import is_page

        if is_page(definition):
            raise CommandError(f"definition {definition_id} is a page: pages are always sandboxed")
        if not isinstance(code, str) or not code.strip():
            raise CommandError(f"definition {definition_id} has no render code to record")

        digest = render_code_digest(code)
        existing = SandboxExemption.objects.filter(definition_id=definition_id).first()
        if existing is not None and existing.owner() != expected:
            raise CommandError(
                f"definition {definition_id} already has an exemption on {existing.owner()}; --remove it first"
            )
        previous = existing.render_code_sha256 if existing is not None else None
        with audit_context(user=user, source="script"):
            row, created = SandboxExemption.objects.update_or_create(
                definition_id=definition_id,
                defaults={
                    "opportunity_id": scope.get("opportunity_id"),
                    "program_id": scope.get("program_id"),
                    "organization_id": str(scope.get("organization_id") or ""),
                    "render_code_sha256": digest,
                    "reason": reason,
                    "added_by": user.username,
                },
            )
            record(
                Action.CREATE if created else Action.UPDATE,
                resource_type=RESOURCE_TYPE,
                resource_id=definition_id,
                opportunity_id=row.opportunity_id,
                program_id=row.program_id,
                metadata={
                    "owner": expected,
                    "render_code_sha256": digest,
                    "previous_render_code_sha256": previous,
                    "reason": reason,
                },
                user=user,
            )
        name = str((getattr(definition, "data", None) or {}).get("name") or "")
        self.stdout.write(
            f"{'recorded' if created else 'updated'}: definition {definition_id} ({expected}) {name[:60]!r} "
            f"stays out of the sandbox under `all` while its render code hashes to {digest[:12]}"
        )

    def _read(self, user, definition_id, scope):
        """The definition and its effective render code, read as ``user``."""
        from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
        from connect_labs.labs.integrations.connect.api_client import LabsAPIError
        from connect_labs.workflow.data_access import WorkflowDataAccess
        from connect_labs.workflow.render_source import resolve_render_code

        try:
            token = get_valid_access_token(user)
        except ConnectTokenError as exc:
            raise CommandError(f"{user.username} has no usable stored Connect login: {exc}") from exc
        da = WorkflowDataAccess(access_token=token, **scope)
        try:
            definition = da.get_definition(definition_id)
            if definition is None:
                raise CommandError(f"definition {definition_id} is not readable by {user.username} in {scope}")
            code, _source = resolve_render_code(da, definition)
        except LabsAPIError as exc:
            raise CommandError(f"could not read definition {definition_id}: {exc}") from exc
        finally:
            da.close()
        return definition, code
