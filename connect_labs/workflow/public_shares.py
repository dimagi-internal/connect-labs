"""Public links to a completed run: the frozen copy and how long it lives.

A visitor has no Connect token, so the run is copied into the labs database when the
link is made and nothing is read from Connect afterwards. That copy is outside every
ACL, which is why who may make one is checked where it is made
(``public_share_views.py``) and the copy is deleted when the share ends.

Expiry is checked on every read (``find_usable``). ``sweep_expired`` removes the data;
it never decides whether a link works.

Design: docs/superpowers/specs/2026-11-02-public-run-share-links-design.md
"""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, time, timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F
from django.utils import timezone

from connect_labs.audit_trail import service as audit
from connect_labs.audit_trail.models import Action
from connect_labs.workflow.models import PublicRunShare

OFFERED_DAYS = (1, 7, 30, 90)
DEFAULT_DAYS = 7
MAX_DAYS = 365
RESOURCE_TYPE = "public_run_share"
#: What a render may not be handed: these only act, and a visitor can do neither.
_CONFIG_LEFT_OUT = ("actions", "agent")
_DEFINITION_KEPT = ("name", "description", "statuses", "opportunity_ids", "multi_opp")
_MAX_TOKEN_LENGTH = 64


def resolve_expiry(days, until, now: datetime | None = None) -> datetime:
    """When a link chosen in the dialog ends. Raises ``ValueError`` for anything not offered."""
    now = now or timezone.now()
    if until not in (None, ""):
        if not isinstance(until, str):
            raise ValueError("Choose a date.")
        try:
            day = date.fromisoformat(until)
        except ValueError as e:
            raise ValueError("Choose a date.") from e
        if day >= date.max:
            raise ValueError("A link can last at most a year.")
        expires = timezone.make_aware(
            datetime.combine(day + timedelta(days=1), time.min), timezone.get_current_timezone()
        )
        if expires <= now:
            raise ValueError("Choose a date that has not passed.")
        if expires > now + timedelta(days=MAX_DAYS + 1):
            raise ValueError(f"A link can last at most {MAX_DAYS} days.")
        return expires
    if days in (None, ""):
        days = DEFAULT_DAYS
    if isinstance(days, bool) or days not in OFFERED_DAYS:
        raise ValueError(f"A link can last {', '.join(str(d) for d in OFFERED_DAYS)} days, or to a date.")
    return now + timedelta(days=days)


def copy_payload(definition, run, render_code: str) -> dict:
    """Everything the public page draws, frozen. ``run.snapshot`` is already decoded by
    the API client, which also fetches a large snapshot's child record."""
    from connect_labs.workflow.templates import with_inherited_config_flags

    data = with_inherited_config_flags(definition.data, definition.template_type)
    config = {k: v for k, v in (data.get("config") or {}).items() if k not in _CONFIG_LEFT_OUT}
    snapshot = run.snapshot or {}
    payload = {
        "renderCode": render_code,
        "definition": {"id": definition.id, **{k: data[k] for k in _DEFINITION_KEPT if k in data}, "config": config},
        "instance": {
            "id": run.id,
            "definition_id": definition.id,
            "opportunity_id": run.opportunity_id,
            "program_id": run.program_id,
            "opportunity_ids": snapshot.get("opportunity_ids") or data.get("opportunity_ids") or [],
            "name": run.name,
            "status": "completed",
            "state": run.state,
            "period_start": run.period_start,
            "period_end": run.period_end,
            "completed_at": run.completed_at,
            "snapshot": snapshot,
        },
        # A render that reads the bare props, not `view`, draws the same thing.
        "workers": snapshot.get("workers") or [],
        "pipelines": snapshot.get("pipelines") or {},
    }
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def create(user, definition, run, render_code: str, expires_at: datetime) -> PublicRunShare:
    share = PublicRunShare.objects.create(
        token=secrets.token_urlsafe(32),
        definition_id=definition.id,
        run_id=run.id,
        opportunity_id=run.opportunity_id,
        program_id=run.program_id,
        workflow_name=(definition.name or "")[:500],
        created_by=user,
        expires_at=expires_at,
        payload=copy_payload(definition, run, render_code),
    )
    _audit(Action.CREATE, share, {"expires_at": expires_at.isoformat()}, user=user)
    return share


def find_usable(token) -> PublicRunShare | None:
    if not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    return PublicRunShare.objects.filter(
        token=token, ended_at__isnull=True, expires_at__gt=timezone.now(), payload__isnull=False
    ).first()


def live_for_run(run_id: int, opportunity_id, program_id):
    return (
        PublicRunShare.objects.filter(
            run_id=run_id,
            opportunity_id=opportunity_id,
            program_id=program_id,
            ended_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .select_related("created_by")
        .defer("payload")
        .order_by("-created_at")
    )


def note_view(share: PublicRunShare) -> None:
    PublicRunShare.objects.filter(pk=share.pk).update(last_viewed_at=timezone.now(), view_count=F("view_count") + 1)
    _audit(Action.READ, share, None)


def end(share: PublicRunShare, reason: str, user=None) -> bool:
    """Delete the copy and record why. A share already ended stays as it ended."""
    changed = PublicRunShare.objects.filter(pk=share.pk, ended_at__isnull=True).update(
        ended_at=timezone.now(), ended_reason=reason, ended_by=user, payload=None
    )
    if changed:
        _audit(Action.DELETE, share, {"reason": str(reason)}, user=user)
    return bool(changed)


def end_for_run(run_id: int, *, opportunity_id, program_id, user=None) -> int:
    """A person deleted this run. Scoped, because labs-only records number themselves."""
    shares = PublicRunShare.objects.filter(
        run_id=run_id, opportunity_id=opportunity_id, program_id=program_id, ended_at__isnull=True
    ).defer("payload")
    return sum(end(s, PublicRunShare.EndedReason.SOURCE_DELETED, user) for s in shares)


def end_for_definition(definition_id: int, *, opportunity_id, program_id, user=None) -> int:
    """A person deleted this workflow, which removes its runs in whatever scope they sit,
    so a program-owned workflow's opportunity-scoped runs go with it."""
    shares = PublicRunShare.objects.filter(definition_id=definition_id, ended_at__isnull=True).defer("payload")
    if program_id is None:
        shares = shares.filter(opportunity_id=opportunity_id)
    return sum(end(s, PublicRunShare.EndedReason.SOURCE_DELETED, user) for s in shares)


def sweep_expired() -> int:
    shares = PublicRunShare.objects.filter(ended_at__isnull=True, expires_at__lte=timezone.now()).defer("payload")
    return sum(end(s, PublicRunShare.EndedReason.EXPIRED) for s in shares)


def _audit(action: str, share: PublicRunShare, metadata: dict | None, user=None) -> None:
    """Never the token: the audit trail is append-only, so it could not be taken back out."""
    audit.record(
        action,
        resource_type=RESOURCE_TYPE,
        resource_id=share.pk,
        opportunity_id=share.opportunity_id,
        program_id=share.program_id,
        metadata={"run_id": share.run_id, "definition_id": share.definition_id, **(metadata or {})},
        user=user,
    )
