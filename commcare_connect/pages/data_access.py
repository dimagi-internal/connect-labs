"""Surface persistence over the Labs Record API.

A "surface" is a LabsRecord with type="surface" and public=True. Its `data`
holds the page config: slug, title, an ordered list of card instances, and
display options. Surfaces are public so any authenticated user can resolve them
by slug; per-card entitlement (in providers) protects the underlying data.
"""

from __future__ import annotations

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

SURFACE_TYPE = "surface"


class SurfaceDataAccess:
    def __init__(self, access_token: str, program_id: int | None = None, opportunity_id: int | None = None):
        self.access_token = access_token
        self.program_id = program_id
        self.opportunity_id = opportunity_id
        self.client = LabsRecordAPIClient(
            access_token,
            program_id=program_id,
            opportunity_id=opportunity_id,
        )

    def _experiment(self) -> str:
        scope = self.program_id or self.opportunity_id or ""
        return str(scope)

    @staticmethod
    def _normalize(record) -> dict:
        data = record.data or {}
        return {
            "id": record.id,
            "slug": data.get("slug"),
            "title": data.get("title"),
            "cards": data.get("cards", []),
            "options": data.get("options", {}),
        }

    def get_surface_by_slug(self, slug: str) -> dict | None:
        records = self.client.get_records(type=SURFACE_TYPE, public=True, **{"data__slug": slug})
        records = list(records or [])
        if not records:
            return None
        return self._normalize(records[0])

    def list_surfaces(self) -> list[dict]:
        records = self.client.get_records(type=SURFACE_TYPE, experiment=self._experiment())
        return [self._normalize(r) for r in (records or [])]

    def create_surface(self, slug: str, title: str, cards: list[dict], options: dict | None = None) -> dict:
        data = {"slug": slug, "title": title, "cards": cards, "options": options or {}}
        record = self.client.create_record(
            experiment=self._experiment(),
            type=SURFACE_TYPE,
            data=data,
            program_id=self.program_id,
            public=True,
        )
        return self._normalize(record)

    def update_surface(
        self, record_id: int, slug: str, title: str, cards: list[dict], options: dict | None = None
    ) -> dict:
        data = {"slug": slug, "title": title, "cards": cards, "options": options or {}}
        record = self.client.update_record(
            record_id=record_id,
            type=SURFACE_TYPE,
            data=data,
            program_id=self.program_id,
            public=True,
        )
        return self._normalize(record)
