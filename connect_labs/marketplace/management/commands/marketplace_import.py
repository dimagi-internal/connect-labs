"""Load the organisation registry from the LLO Directory.

    make manage CMD="marketplace_import --dry-run"
    make manage CMD="marketplace_import"

The work is ``connect_labs.marketplace.importer.run_import``; this is its terminal
face. The same function backs the daily beat and the ``marketplace_import`` MCP tool,
so nobody needs a shell on prod to make a directory edit appear on labs.

This replaces ``pulse_partner_import``, which read two columns of the same sheet
to put names on Connect slugs. The directory is the master organisation list --
an organisation that answered an EOI and was not selected never gets a Connect
row at all -- so reading only the columns Connect could already explain was
reading it backwards.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from connect_labs.marketplace import directory
from connect_labs.marketplace.importer import ImportFailed, import_directory, run_import

__all__ = ["Command", "import_directory"]  # import_directory: re-exported, tests import it from here


class Command(BaseCommand):
    help = "Import the organisation registry from the LLO Directory"

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=directory.DIRECTORY_ID)
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
        parser.add_argument(
            "--check-access",
            action="store_true",
            help="Verify a real read of every round's response sheet and record the result",
        )
        parser.add_argument(
            "--eoi",
            action="store_true",
            help="Also import EOI/RFP rounds and their submissions",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Treat the sheet as authoritative: delete organisations, contacts and attributions it does not carry",
        )

    def handle(self, *args, **opts):
        styles = {"warning": self.style.WARNING, "success": self.style.SUCCESS}

        def emit(level: str, line: str) -> None:
            self.stdout.write(styles.get(level, str)(line))

        try:
            run_import(
                spreadsheet_id=opts["spreadsheet_id"],
                dry_run=opts["dry_run"],
                eoi_rounds=opts["eoi"],
                check_access=opts["check_access"],
                prune=opts["prune"],
                emit=emit,
            )
        except ImportFailed as exc:
            raise CommandError(str(exc)) from exc
