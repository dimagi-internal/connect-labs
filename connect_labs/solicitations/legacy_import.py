"""Import the solicitations that live as prod LabsRecords into the labs tables.

Run once after deploy, by a staff user, through the MCP tool
``solicitations_import_records`` (a long-running job). Reads prod's
``solicitation``, ``solicitation_response`` and ``solicitation_review`` records
as that user and upserts each by ``legacy_record_id``, so running it again is
safe: it updates what it imported, and leaves alone what people have changed
since (a call's owner and whether it is listed are set only when they are
empty / on first import). Prod records are never written.

- **Owner**: the running organisation of the call's programme
  (`supply_chain.runner.running_org`), else ``default_owner``. With neither,
  the call is imported with no owner, which only staff manage.
- **Listing**: a call that was public on prod stays listed, unless its title or
  programme marks it as a smoke/e2e/test run -- those come across unlisted.
- **Respondent organisation**: the marketplace's exact matching (a contact
  email on a directory record, or an exact normalised organisation name). No
  match leaves ``llo_entity`` empty with the name the firm typed kept.
- **Notified** is imported as False: prod set it on every award without
  sending anything.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from connect_labs.labs.progress import NULL_PROGRESS, safe_call
from connect_labs.solicitations.local_models import (
    AWARD_STAGES,
    LOCAL_ID_FLOOR,
    RESPONSE_STATUSES,
    SOURCE_LABS_RECORD,
    STATUSES,
    Solicitation,
    SolicitationResponse,
    SolicitationReview,
)

logger = logging.getLogger(__name__)

SOLICITATION_TYPE = "solicitation"
RESPONSE_TYPE = "solicitation_response"
REVIEW_TYPE = "solicitation_review"

SMOKE = re.compile(r"smoke|e2e|test run", re.IGNORECASE)

_STATUS_VALUES = {s for s, _ in STATUSES}
_RESPONSE_STATUS_VALUES = {s for s, _ in RESPONSE_STATUSES}


def is_smoke(title: str, program_name: str) -> bool:
    return bool(SMOKE.search(title or "") or SMOKE.search(program_name or ""))


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _list(value):
    return list(value) if isinstance(value, list) else []


def _dict(value):
    return dict(value) if isinstance(value, dict) else {}


def _text(value, limit=None):
    text = "" if value is None else str(value)
    return text[:limit] if limit else text


class _Users:
    """Usernames on prod records -> labs users, where labs knows them."""

    def __init__(self):
        self._cache = {}

    def get(self, username):
        if not username:
            return None
        if username not in self._cache:
            self._cache[username] = get_user_model().objects.filter(username=username).first()
        return self._cache[username]


class _Matcher:
    """The marketplace's exact matching, built once: email on a directory contact, or exact name."""

    def __init__(self):
        from connect_labs.labs.models import LabsOrg
        from connect_labs.marketplace.matching import normalise
        from connect_labs.marketplace.models import OrgContact

        self.normalise = normalise
        self.by_email = {c.email.lower(): c.org for c in OrgContact.objects.select_related("org") if c.email}
        by_name = {}
        for org in LabsOrg.objects.real():
            key = normalise(org.name)
            by_name[key] = None if key in by_name else org  # a collision resolves to neither
        self.by_name = {k: v for k, v in by_name.items() if v}

    def match(self, name: str, email: str):
        for candidate in (e.strip().lower() for e in (email or "").split(",")):
            if candidate and candidate in self.by_email:
                return self.by_email[candidate]
        key = self.normalise(name or "")
        return self.by_name.get(key) if key else None


def _program_id(record):
    pid = _int(getattr(record, "program_id", None))
    if pid is None:
        pid = _int(getattr(record, "experiment", None))
    return pid


def fetch_records(client, program_ids=()) -> dict:
    """Every solicitation, response and review the caller can read on prod, deduplicated by id."""

    def collect(record_type, **kw):
        return {r.id: r for r in client.get_records(type=record_type, **kw)}

    solicitations = collect(SOLICITATION_TYPE, public=True)
    for pid in program_ids or ():
        # Non-public calls are readable only scoped to their programme.
        solicitations.update(collect(SOLICITATION_TYPE, program_id=int(pid)))
    responses = collect(RESPONSE_TYPE, public=True)
    reviews = collect(REVIEW_TYPE, public=True)
    return {"solicitations": solicitations, "responses": responses, "reviews": reviews}


class IdFloorCollision(ValueError):
    """A prod record id at or above `LOCAL_ID_FLOOR`, where local ids live."""


def _refuse_ids_above_floor(records: dict) -> None:
    """Prod ids become /solicitations/<id>/ URLs, resolved before local pks; one at or
    above the floor could shadow (or be shadowed by) a call made in labs."""
    clashes = sorted(
        (kind, record_id)
        for kind, rows in records.items()
        for record_id in (rows or {})
        if _int(record_id) is not None and _int(record_id) >= LOCAL_ID_FLOOR
    )
    if clashes:
        listed = ", ".join(f"{kind} {record_id}" for kind, record_id in clashes[:10])
        raise IdFloorCollision(
            f"Prod record ids at or above the local id floor ({LOCAL_ID_FLOOR:,}) cannot be imported: "
            f"{listed}{' ...' if len(clashes) > 10 else ''}. Their links would collide with calls made in "
            "labs. Raise LOCAL_ID_FLOOR (and the id sequences, as migration 0014 does) before importing."
        )


def import_records(records: dict, *, default_owner=None, dry_run=False, progress=NULL_PROGRESS) -> dict:
    """Upsert fetched prod records into the labs tables. Returns counts.

    With ``dry_run`` everything is computed inside a transaction that is rolled
    back, so the counts are exactly what a real run would do. Refuses
    (`IdFloorCollision`, before writing anything) any prod id at or above
    `LOCAL_ID_FLOOR`.
    """
    _refuse_ids_above_floor(records)
    from connect_labs.supply_chain.runner import running_org

    stats = {
        "solicitations": {"created": 0, "updated": 0, "unlisted_as_test": 0, "unowned": 0},
        "responses": {"created": 0, "updated": 0, "matched_to_org": 0, "orphaned": 0},
        "reviews": {"created": 0, "updated": 0, "orphaned": 0},
        "dry_run": dry_run,
    }
    users = _Users()
    matcher = _Matcher()
    sol_records = sorted(records.get("solicitations", {}).values(), key=lambda r: r.id)
    resp_records = sorted(records.get("responses", {}).values(), key=lambda r: r.id)
    rev_records = sorted(records.get("reviews", {}).values(), key=lambda r: r.id)
    total = len(sol_records) + len(resp_records) + len(rev_records)
    done = 0

    with transaction.atomic():
        imported_calls = {}
        for record in sol_records:
            data = record.data or {}
            call = Solicitation.objects.filter(legacy_record_id=record.id).first()
            created = call is None
            if created:
                call = Solicitation(legacy_record_id=record.id, source=SOURCE_LABS_RECORD)
            program_id = _program_id(record)
            title = _text(data.get("title"), 300) or f"Solicitation {record.id}"
            program_name = _text(data.get("program_name"), 300)
            if call.owner_org_id is None:
                call.owner_org = (running_org(program_id) if program_id else None) or default_owner
            if created:
                smoke = is_smoke(title, program_name)
                call.listed = bool(getattr(record, "public", False)) and not smoke
                stats["solicitations"]["unlisted_as_test"] += int(smoke)
            call.program_id = program_id
            call.title = title
            call.program_name = program_name
            call.description = _text(data.get("description"))
            call.scope_of_work = _text(data.get("scope_of_work"))
            call.solicitation_type = (
                data.get("solicitation_type") if data.get("solicitation_type") in ("eoi", "rfp") else "rfp"
            )
            call.status = data.get("status") if data.get("status") in _STATUS_VALUES else "closed"
            call.application_deadline = _date(data.get("application_deadline"))
            call.expected_start_date = _date(data.get("expected_start_date"))
            call.expected_end_date = _date(data.get("expected_end_date"))
            call.estimated_scale = _text(data.get("estimated_scale"), 200)
            call.contact_email = _text(data.get("contact_email"), 320)
            call.questions = _list(data.get("questions"))
            call.evaluation_criteria = _list(data.get("evaluation_criteria"))
            call.plans = _list(data.get("plans"))
            call.fund_id = _int(data.get("fund_id"))
            call.connect_opportunity_id = _int(data.get("connect_opportunity_id"))
            call.source_program_id = _int(data.get("source_program_id"))
            call.source_group_id = _int(data.get("source_group_id"))
            call.source_plan_ids = [i for i in (_int(x) for x in _list(data.get("source_plan_ids"))) if i is not None]
            if data.get("criteria_locked"):
                call.criteria_locked_at = (
                    _datetime(data.get("criteria_locked_at")) or call.criteria_locked_at or timezone.now()
                )
            call.created_by = users.get(data.get("created_by"))
            call.save()
            imported_calls[record.id] = call
            stats["solicitations"]["created" if created else "updated"] += 1
            stats["solicitations"]["unowned"] += int(call.owner_org_id is None)
            done += 1
            safe_call(progress, done, total, f"solicitation {record.id}")

        imported_responses = {}
        for record in resp_records:
            data = record.data or {}
            parent_id = _int(getattr(record, "labs_record_id", None)) or _int(data.get("solicitation_id"))
            call = imported_calls.get(parent_id) or Solicitation.objects.filter(legacy_record_id=parent_id).first()
            done += 1
            if call is None:
                stats["responses"]["orphaned"] += 1
                continue
            response = SolicitationResponse.objects.filter(legacy_record_id=record.id).first()
            created = response is None
            if created:
                response = SolicitationResponse(legacy_record_id=record.id)
            response.solicitation = call
            response.responses = _dict(data.get("responses"))
            response.status = data.get("status") if data.get("status") in _RESPONSE_STATUS_VALUES else "submitted"
            response.submitted_by_name = _text(data.get("submitted_by_name"), 200)
            response.submitted_by_email = _text(data.get("submitted_by_email"), 500)
            response.org_name = _text(data.get("org_name"), 300)
            response.llo_entity_name = _text(data.get("llo_entity_name"), 300)
            response.org_id = _text(data.get("org_id"), 120)
            response.submission_date = _datetime(data.get("submission_date"))
            response.selected_plan_ids = _list(data.get("selected_plan_ids"))
            response.selected_plan_names = _list(data.get("selected_plan_names"))
            response.ai_proposed_scores = _dict(data.get("ai_proposed_scores"))
            response.reward_budget = _int(data.get("reward_budget"))
            response.awarded_at = _datetime(data.get("awarded_at"))
            stage = data.get("award_stage")
            response.award_stage = stage if stage in AWARD_STAGES else "verification"
            response.submitted_by = users.get(data.get("submitted_by_username") or getattr(record, "username", None))
            if response.llo_entity_id is None:
                response.llo_entity = matcher.match(
                    response.org_name or response.llo_entity_name, response.submitted_by_email
                )
            stats["responses"]["matched_to_org"] += int(response.llo_entity_id is not None)
            try:
                with transaction.atomic():
                    response.save()
            except IntegrityError:
                # A second prod draft by one person on one call: labs holds one
                # draft per person (`uniq_draft_per_respondent`), so keep this
                # one without the person, as an unattributed draft.
                response.submitted_by = None
                response.save()
            imported_responses[record.id] = response
            stats["responses"]["created" if created else "updated"] += 1
            safe_call(progress, done, total, f"response {record.id}")

        for record in rev_records:
            data = record.data or {}
            parent_id = _int(getattr(record, "labs_record_id", None)) or _int(data.get("response_id"))
            response = (
                imported_responses.get(parent_id)
                or SolicitationResponse.objects.filter(legacy_record_id=parent_id).first()
            )
            done += 1
            if response is None:
                stats["reviews"]["orphaned"] += 1
                continue
            review = SolicitationReview.objects.filter(legacy_record_id=record.id).first()
            created = review is None
            if created:
                review = SolicitationReview(legacy_record_id=record.id)
            review.response = response
            reviewer_name = _text(data.get("reviewer_username"), 200)
            review.reviewer_name = reviewer_name
            review.criteria_scores = _dict(data.get("criteria_scores"))
            review.score = _int(data.get("score"))
            review.recommendation = _text(data.get("recommendation"), 32)
            review.notes = _text(data.get("notes"))
            review.tags = _text(data.get("tags"), 500)
            review.review_date = _datetime(data.get("review_date")) or review.review_date or timezone.now()
            reviewer = users.get(reviewer_name)
            clash = (
                reviewer is not None
                and SolicitationReview.objects.filter(response=response, reviewer=reviewer)
                .exclude(pk=review.pk)
                .exists()
            )
            review.reviewer = None if clash else reviewer
            try:
                with transaction.atomic():
                    review.save()
            except IntegrityError:
                review.reviewer = None
                review.save()
            stats["reviews"]["created" if created else "updated"] += 1
            safe_call(progress, done, total, f"review {record.id}")

        if dry_run:
            transaction.set_rollback(True)
        else:
            raise_sequences_above_legacy_ids()
    return stats


def raise_sequences_above_legacy_ids() -> None:
    """Keep every local id above every imported prod id (only ever raises a sequence)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for model in (Solicitation, SolicitationResponse, SolicitationReview):
            table = model._meta.db_table
            highest = model.objects.exclude(legacy_record_id=None).order_by("-legacy_record_id").first()
            floor = max(LOCAL_ID_FLOOR, (highest.legacy_record_id if highest else 0) + 1)
            cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", [table])
            (sequence,) = cursor.fetchone()
            if not sequence:
                continue
            cursor.execute(f"SELECT last_value, is_called FROM {sequence}")  # noqa: S608 -- name from pg
            last_value, is_called = cursor.fetchone()
            current = last_value if is_called else last_value - 1
            if floor > current:
                cursor.execute("SELECT setval(%s, %s, true)", [sequence, floor])
