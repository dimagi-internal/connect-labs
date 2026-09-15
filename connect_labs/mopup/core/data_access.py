"""Data access layer for the CHC mop-up feature.

A run is a program-scoped LabsRecord (experiment=<program_id>), persisted via
LabsRecordAPIClient — mirrors `microplans.core.data_access.ProgramPlanDataAccess`,
minus that class's optimistic-concurrency revisioning (single-reviewer use
case for now; add it if concurrent editing of one run becomes real).
"""

from __future__ import annotations

from datetime import datetime, timezone

from connect_labs.mopup.core.models import STATUS_SETUP, TYPE_RUN, MopupRunRecord
from connect_labs.workflow.data_access import BaseDataAccess


class MopupRunNotFoundError(Exception):
    """A delete targeted a run id that isn't in this program — it doesn't
    exist, or it belongs to another program the caller can't see. Refused
    rather than deleted by raw id: the production DELETE endpoint
    authorizes the caller's *membership* of any scope in the payload but
    then deletes by `pk__in` without checking the record actually belongs
    there, so a bare id would let a member of one program delete another
    program's run. Reading the run scoped to this program first (404 →
    None) closes that — same reasoning as
    `microplans.core.data_access.RecordNotInProgramError`."""


class MopupRunDataAccess(BaseDataAccess):
    """CRUD for MopupRunRecord, scoped to one program."""

    def __init__(self, program_id, **kwargs):
        super().__init__(program_id=program_id, **kwargs)
        self._experiment = str(program_id)

    def create_run(self, *, target_opportunity_id: int, name: str = "") -> MopupRunRecord:
        data = {
            "status": STATUS_SETUP,
            "name": name or "WA Revisit",
            "target_opportunity_id": target_opportunity_id,
            "selected_wards": [],
            "date_from": None,
            "date_to": None,
            "thresholds": {},
            "candidate_work_areas": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_RUN,
            program_id=self.program_id,
            data=data,
        )
        return MopupRunRecord(record.to_api_dict())

    def get_run(self, run_id: int) -> MopupRunRecord | None:
        return self.labs_api.get_record_by_id(
            record_id=int(run_id), experiment=self._experiment, type=TYPE_RUN, model_class=MopupRunRecord
        )

    def list_runs(self) -> list[MopupRunRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_RUN,
            program_id=self.program_id,
            model_class=MopupRunRecord,
        )

    def delete_run(self, run_id: int) -> None:
        """Hard-delete a run — no soft-delete/archive concept for mop-up
        runs today, so this is a real, unrecoverable removal. Everything
        the run owns (thresholds, locked candidates, uploaded building CSV,
        planning-gap features, Celery task ids) lives directly in the
        record's own JSON `data` (see `core/models.py`), so deleting the
        record itself is a complete cleanup — nothing else to garbage
        collect. See `MopupRunNotFoundError` for why this reads the run
        scoped to this program first rather than deleting by raw id."""
        if self.get_run(int(run_id)) is None:
            raise MopupRunNotFoundError(f"run {run_id} is not in program {self.program_id}")
        self.labs_api.delete_record(int(run_id))

    def update_run(self, run: MopupRunRecord, **field_updates) -> MopupRunRecord:
        """Merge `field_updates` into the run's stored data and save."""
        run.data.update(field_updates)
        record = self.labs_api.update_record(
            record_id=run.id,
            experiment=self._experiment,
            type=TYPE_RUN,
            program_id=self.program_id,
            data=run.data,
        )
        return MopupRunRecord(record.to_api_dict())

    def set_ward_selection(
        self,
        run: MopupRunRecord,
        *,
        wards: list[dict],
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> MopupRunRecord:
        """Phase 1's "proceed to analysis" action: record the picked ward(s) +
        optional date bound and move the run out of `setup`."""
        from connect_labs.mopup.core.models import STATUS_ANALYSIS

        return self.update_run(
            run,
            selected_wards=wards,
            date_from=date_from,
            date_to=date_to,
            status=STATUS_ANALYSIS,
        )
