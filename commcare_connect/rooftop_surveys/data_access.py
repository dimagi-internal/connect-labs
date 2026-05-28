"""Data access for rooftop_surveys — wraps LabsRecordAPIClient.

Persists the drawn area + generated frame as LabsRecords scoped by
experiment=<opportunity_id>. No Django models; reads/writes go to the
production LabsRecord API via BaseDataAccess.labs_api.
"""

from __future__ import annotations

from datetime import datetime, timezone

from commcare_connect.rooftop_surveys.models import TYPE_AREA, TYPE_FRAME, RooftopAreaRecord, RooftopFrameRecord
from commcare_connect.workflow.data_access import BaseDataAccess


class RooftopDataAccess(BaseDataAccess):
    """CRUD for rooftop_area + rooftop_frame records, scoped to one opportunity."""

    @property
    def _experiment(self) -> str:
        return str(self.opportunity_id)

    def save_area(self, areas: list[dict], config: dict, name: str = "") -> RooftopAreaRecord:
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_AREA,
            data={
                "name": name,
                "areas": areas,
                "config": config,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return RooftopAreaRecord(record.to_dict())

    def save_frame(
        self,
        area_record_id: int,
        pins: dict,
        hulls: dict,
        stats: list[dict],
    ) -> RooftopFrameRecord:
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_FRAME,
            data={
                "pins": pins,
                "hulls": hulls,
                "stats": stats,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            labs_record_id=area_record_id,
        )
        return RooftopFrameRecord(record.to_dict())

    def list_frames(self) -> list[RooftopFrameRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_FRAME,
            model_class=RooftopFrameRecord,
        )

    def list_areas(self) -> list[RooftopAreaRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_AREA,
            model_class=RooftopAreaRecord,
        )
