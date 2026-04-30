"""
Migrate workflow run statuses to the active|frozen lifecycle.

Pre-2026-04-30 runs used `in_progress`, `completed`, and a transient
`preview` status. The new lifecycle has only two states (see
docs/plans/2026-04-30-run-lifecycle.md):

  - `active`: writable, in progress
  - `frozen`: read-only, snapshot is the canonical source

Mapping:

  in_progress  → active
  completed    → frozen   (and `legacy=true` set if no snapshot exists,
                          so render code can show "snapshot unavailable")
  preview      → unchanged (UI flag, not a real persisted status)
  anything else → active  (defensive default; the proxy property already
                          maps unknowns to active at read time)

Idempotent: a run that's already `active` or `frozen` is left alone.
Run with --dry-run to preview changes without writing.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from commcare_connect.workflow.data_access import RUN_STATUS_ACTIVE, RUN_STATUS_FROZEN, WorkflowDataAccess

logger = logging.getLogger(__name__)


_OLD_TO_NEW = {
    "in_progress": RUN_STATUS_ACTIVE,
    "completed": RUN_STATUS_FROZEN,
}


class Command(BaseCommand):
    help = "Migrate workflow_run records to the active|frozen status lifecycle."

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

        counts = {"active": 0, "frozen": 0, "skipped": 0, "legacy_marked": 0}

        for run in runs:
            old_status = run.data.get("status")
            new_status = _OLD_TO_NEW.get(old_status)

            # Already migrated, edit-mode preview, or unknown — skip.
            if new_status is None:
                counts["skipped"] += 1
                continue

            patch = {"status": new_status}
            # If we're flipping to frozen but no snapshot exists, mark legacy
            # so render code can show "snapshot unavailable for this older run."
            if new_status == RUN_STATUS_FROZEN and not run.data.get("snapshot"):
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
                + f"complete: active={counts['active']} frozen={counts['frozen']} "
                + f"skipped={counts['skipped']} legacy={counts['legacy_marked']}"
            )
        )
        data_access.close()
