"""Surface persistence over the Labs Record API.

A "surface" is a LabsRecord with type="surface". Its `data` holds the page
config: slug, title, an ordered list of card instances, display options, and a
`scope` hint. A surface is scoped to exactly one of opportunity / program /
organization (via the LabsRecord's own FK) or is `public`. That scoping IS the
ACL — the prod API's membership check enforces it. Per-card entitlement (in
providers) additionally protects each card's data.
"""

from __future__ import annotations

from connect_labs.labs.integrations.connect.api_client import LabsAPIError, LabsRecordAPIClient

SURFACE_TYPE = "surface"


def _scope_descriptor(opportunity_id, program_id, organization_id, public) -> dict:
    if public:
        return {"type": "public", "id": None}
    if opportunity_id:
        return {"type": "opp", "id": opportunity_id}
    if program_id:
        return {"type": "program", "id": program_id}
    if organization_id:
        return {"type": "org", "id": organization_id}
    return {"type": "public", "id": None}


class SurfaceDataAccess:
    def __init__(
        self,
        access_token: str,
        program_id: int | None = None,
        opportunity_id: int | None = None,
        organization_id: int | None = None,
    ):
        self.access_token = access_token
        self.program_id = program_id
        self.opportunity_id = opportunity_id
        self.organization_id = organization_id
        self.client = LabsRecordAPIClient(
            access_token,
            program_id=program_id,
            opportunity_id=opportunity_id,
            organization_id=organization_id,
        )

    def _experiment(self) -> str:
        scope = self.program_id or self.opportunity_id or self.organization_id or ""
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
            "scope": data.get("scope", {"type": "public", "id": None}),
        }

    def get_surface_by_slug(self, slug: str) -> dict | None:
        records = self.client.get_records(type=SURFACE_TYPE, public=True, slug=slug)
        records = list(records or [])
        if not records:
            return None
        records.sort(key=lambda r: r.id)
        return self._normalize(records[0])

    def list_surfaces(self) -> list[dict]:
        records = self.client.get_records(type=SURFACE_TYPE, experiment=self._experiment())
        return [self._normalize(r) for r in (records or [])]

    def create_surface(
        self, slug: str, title: str, cards: list[dict], options: dict | None = None, public: bool = False
    ) -> dict:
        scope = _scope_descriptor(self.opportunity_id, self.program_id, self.organization_id, public)
        data = {"slug": slug, "title": title, "cards": cards, "options": options or {}, "scope": scope}
        record = self.client.create_record(
            experiment=self._experiment(),
            type=SURFACE_TYPE,
            data=data,
            program_id=self.program_id,
            public=public,
        )
        return self._normalize(record)

    def update_surface(
        self,
        record_id: int,
        slug: str,
        title: str,
        cards: list[dict],
        options: dict | None = None,
        public: bool = False,
    ) -> dict:
        scope = _scope_descriptor(self.opportunity_id, self.program_id, self.organization_id, public)
        data = {"slug": slug, "title": title, "cards": cards, "options": options or {}, "scope": scope}
        record = self.client.update_record(
            record_id=record_id,
            experiment=self._experiment(),
            type=SURFACE_TYPE,
            data=data,
            program_id=self.program_id,
            public=public,
        )
        return self._normalize(record)


def _first_match(records):
    records = list(records or [])
    if not records:
        return None
    records.sort(key=lambda r: r.id)
    return SurfaceDataAccess._normalize(records[0])


def resolve_surface(access_token: str, context: dict, slug: str) -> dict | None:
    """Resolve a surface slug against the viewer's labs_context, then public.

    `context` is a request.labs_context-shaped dict. Tries the present scopes in
    priority order (opp → program → org), issuing a correctly-scoped get_records
    so the prod API's membership check authorizes the read; then falls back to
    the public path. Returns the normalized surface dict or None.
    """
    context = context or {}
    attempts = []
    opp = context.get("opportunity_id")
    prog = context.get("program_id")
    org = context.get("organization_id")
    if opp:
        attempts.append({"opportunity_id": opp})
    if prog:
        attempts.append({"program_id": prog})
    # org context is an int id post-validation; a raw slug is skipped (not an authorizing scope here)
    if isinstance(org, int):
        attempts.append({"organization_id": org})

    for scope in attempts:
        client = LabsRecordAPIClient(access_token, **scope)
        try:
            match = _first_match(client.get_records(type=SURFACE_TYPE, slug=slug))
        except LabsAPIError:
            continue
        if match:
            return match

    public_client = LabsRecordAPIClient(access_token)
    try:
        return _first_match(public_client.get_records(type=SURFACE_TYPE, public=True, slug=slug))
    except LabsAPIError:
        return None
