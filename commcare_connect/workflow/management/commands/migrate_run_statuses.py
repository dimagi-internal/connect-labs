"""
Migrate workflow run statuses to the in_progress|completed lifecycle.

History: an earlier rename to "active"/"frozen" landed on 2026-04-30 and was
reverted on 2026-05-04 in favour of the original `in_progress`/`completed`
vocabulary. This command flips any records still stored under the interim
"active"/"frozen" names back to the canonical values.

Mapping:

  active  → in_progress
  frozen  → completed   (and `legacy=true` set if no snapshot exists, so
                        render code can show "snapshot unavailable")
  preview → unchanged   (UI flag, not a real persisted status)
  in_progress / completed → unchanged (already canonical)
  anything else → unchanged (the proxy property maps unknowns at read time)

Idempotent. Run with --dry-run to preview changes without writing.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from commcare_connect.workflow.data_access import RUN_STATUS_COMPLETED, RUN_STATUS_IN_PROGRESS, WorkflowDataAccess

logger = logging.getLogger(__name__)


_OLD_TO_NEW = {
    "active": RUN_STATUS_IN_PROGRESS,
    "frozen": RUN_STATUS_COMPLETED,
}


class Command(BaseCommand):
    help = "Migrate workflow_run records to the in_progress|completed status lifecycle."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without writing.",
        )
        parser.add_argument(
            "--access-token",
            type=str,
            required=True,
            help="OAuth access token with the export scope (run as a labs admin).",
        )

    def handle(self, *args, **options):
        token = options["access_token"]
        dry_run = options["dry_run"]

        data_access = WorkflowDataAccess(access_token=token)

        runs = data_access.list_runs(definition_id=None)
        self.stdout.write(f"Examining {len(runs)} workflow runs...")

        counts = {"in_progress": 0, "completed": 0, "skipped": 0, "legacy_marked": 0}

        for run in runs:
            old_status = run.data.get("status")
            new_status = _OLD_TO_NEW.get(old_status)

            # Already migrated, edit-mode preview, or unknown — skip.
            if new_status is None:
                counts["skipped"] += 1
                continue

            patch = {"status": new_status}
            # If we're flipping to completed but no snapshot exists, mark legacy
            # so render code can show "snapshot unavailable for this older run."
            if new_status == RUN_STATUS_COMPLETED and not run.data.get("snapshot"):
                patch["legacy"] = True
                counts["legacy_marked"] += 1

            counts[new_status] += 1
            if dry_run:
                self.stdout.write(
                    f"  [dry-run] run {run.id}: {old_status!r} → {new_status!r}"
                    + (" (legacy)" if patch.get("legacy") else "")
                )
            else:
                data_access.update_run_state(run.id, patch, run=run)

        self.stdout.write(
            self.style.SUCCESS(
                "Migration "
                + ("would be " if dry_run else "")
                + f"complete: in_progress={counts['in_progress']} completed={counts['completed']} "
                + f"skipped={counts['skipped']} legacy={counts['legacy_marked']}"
            )
        )
        data_access.close()
