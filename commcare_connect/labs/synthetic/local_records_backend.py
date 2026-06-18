"""Labs-local LabsRecord storage backend.

Mirrors the 5 CRUD methods of ``LabsRecordAPIClient`` (get_records,
get_record_by_id, create_record, update_record, delete_records) but reads/
writes against the ``LabsLocalRecord`` Django model in the labs DB. Used as
the dispatch target whenever a request is scoped to a labs-only opportunity
that has no real Connect opp behind it.

``LabsRecordAPIClient`` calls into here when ``is_labs_only_opportunity_id``
returns True for the request's scoped opportunity_id. Real opp_ids continue
to flow over HTTP to production Connect.
"""

from __future__ import annotations

from django.db.models import Q

from commcare_connect.labs.models import LocalLabsRecord
from commcare_connect.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR, LabsLocalRecord, SyntheticOpportunity


def is_labs_only_opportunity_id(opportunity_id: int | None) -> bool:
    """Return True if ``opportunity_id`` belongs to a registered labs-only opp.

    Fast path: anything below the reserved floor (10_000) is definitely a real
    Connect opp and skips the DB hit. Above the floor, confirms a matching
    ``SyntheticOpportunity`` row exists with ``labs_only=True``. Disabled rows
    still count — disabling controls whether fixtures are SERVED, not whether
    the opp_id namespace is local.
    """
    if opportunity_id is None or opportunity_id < LABS_ONLY_OPP_ID_FLOOR:
        return False
    return SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id, labs_only=True).exists()


def is_labs_only_program_id(program_id: int | None) -> bool:
    """Return True if ``program_id`` is a labs-only program (reserved >= 10_000 range).

    Mirrors the opp-id check for program-scoped requests — e.g. loading the
    Workflows list with only a synthetic program selected and no opportunity.
    A program is labs-only when some ``labs_only=True`` opp either is filed under
    it explicitly (``program_id`` matches) or, when its ``program_id`` is unset,
    is its own program (``opportunity_id`` matches). Without this, program-scoped
    reads fall through to production Connect and 404 (the user isn't a member of
    the synthetic program).
    """
    if program_id is None or program_id < LABS_ONLY_OPP_ID_FLOOR:
        return False
    return (
        SyntheticOpportunity.objects.filter(labs_only=True)
        .filter(Q(program_id=program_id) | Q(program_id__isnull=True, opportunity_id=program_id))
        .exists()
    )


def get_records(
    *,
    opportunity_id: int | None = None,
    experiment: str | None = None,
    type: str | None = None,
    username: str | None = None,
    program_id: int | None = None,
    organization_id: int | None = None,
    labs_record_id: int | None = None,
    model_class: type[LocalLabsRecord] | None = None,
    public: bool | None = None,
    **data_filters,
) -> list[LocalLabsRecord]:
    # opportunity_id is optional: program-scoped reads (a synthetic program with
    # no opp selected) filter by program_id alone across the program's opps.
    qs = LabsLocalRecord.objects.all()
    if opportunity_id is not None:
        qs = qs.filter(opportunity_id=opportunity_id)
    if experiment is not None:
        qs = qs.filter(experiment=experiment)
    if type is not None:
        qs = qs.filter(type=type)
    if username is not None:
        qs = qs.filter(username=username)
    if program_id is not None:
        qs = qs.filter(program_id=program_id)
    if organization_id is not None and isinstance(organization_id, int):
        qs = qs.filter(organization_id=organization_id)
    if labs_record_id is not None:
        qs = qs.filter(labs_record_id=labs_record_id)
    if public is not None:
        qs = qs.filter(public=public)
    for key, value in data_filters.items():
        # Translate JSONField lookups: data__status="active" → data__status="active"
        # Already in Django ORM-native syntax, just forward.
        qs = qs.filter(**{f"data__{key}": value})
    record_class = model_class if model_class else LocalLabsRecord
    return [record_class(row.to_api_dict()) for row in qs]


def get_record_by_id(
    *,
    record_id: int,
    opportunity_id: int | None = None,
    experiment: str | None = None,
    type: str | None = None,
    model_class: type[LocalLabsRecord] | None = None,
) -> LocalLabsRecord | None:
    # record_id is the global PK; opportunity_id is an optional scoping guard so
    # program-scoped lookups (no opp selected) can still resolve a record.
    qs = LabsLocalRecord.objects.filter(id=record_id)
    if opportunity_id is not None:
        qs = qs.filter(opportunity_id=opportunity_id)
    if experiment is not None:
        qs = qs.filter(experiment=experiment)
    if type is not None:
        qs = qs.filter(type=type)
    row = qs.first()
    if row is None:
        return None
    record_class = model_class if model_class else LocalLabsRecord
    return record_class(row.to_api_dict())


def create_record(
    *,
    opportunity_id: int,
    experiment: str,
    type: str,
    data: dict,
    username: str | None = None,
    program_id: int | None = None,
    organization_id: int | None = None,
    labs_record_id: int | None = None,
    public: bool = False,
) -> LocalLabsRecord:
    row = LabsLocalRecord.objects.create(
        opportunity_id=opportunity_id,
        experiment=experiment,
        type=type,
        data=data or {},
        username=(username or "") if username is not None else "",
        program_id=program_id,
        organization_id=organization_id if isinstance(organization_id, int) else None,
        labs_record_id=labs_record_id,
        public=public,
    )
    return LocalLabsRecord(row.to_api_dict())


def update_record(
    *,
    record_id: int,
    opportunity_id: int,
    experiment: str,
    type: str,
    data: dict,
    username: str | None = None,
    program_id: int | None = None,
    organization_id: int | None = None,
    labs_record_id: int | None = None,
    public: bool | None = None,
) -> LocalLabsRecord:
    try:
        row = LabsLocalRecord.objects.get(id=record_id, opportunity_id=opportunity_id)
    except LabsLocalRecord.DoesNotExist as exc:
        # Match LabsRecordAPIClient's failure mode shape so callers don't branch
        # on backend identity to interpret an "update on missing record" error.
        from commcare_connect.labs.integrations.connect.api_client import LabsAPIError

        raise LabsAPIError(f"Record {record_id} not found") from exc

    row.data = data or {}
    if username is not None:
        row.username = username or ""
    if program_id is not None:
        row.program_id = program_id
    if organization_id is not None and isinstance(organization_id, int):
        row.organization_id = organization_id
    if labs_record_id is not None:
        row.labs_record_id = labs_record_id
    if public is not None:
        row.public = public
    row.save()
    return LocalLabsRecord(row.to_api_dict())


def delete_records(*, record_ids: list[int]) -> None:
    if not record_ids:
        return
    LabsLocalRecord.objects.filter(id__in=record_ids).delete()
