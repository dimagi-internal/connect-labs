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


class MopupRunDataAccess(BaseDataAccess):
    """CRUD for MopupRunRecord, scoped to one program."""

    def __init__(self, program_id, **kwargs):
        super().__init__(program_id=program_id, **kwargs)
        self._experiment = str(program_id)

    def create_run(self, *, target_opportunity_id: int, name: str = "") -> MopupRunRecord:
        data = {
            "status": STATUS_SETUP,
            "name": name or "CHC Mop-up",
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
