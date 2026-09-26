"""Run one write operation as a recorded, attributed, idempotent OperationCall.

`call_operation` hands every write here. The call row, the handler's writes
(and the Revisions they produce, attributed to the call through
`write_context`) and the stored result commit together or not at all.

Idempotency rides on the caller's `source.ref` together with a digest of the
payload: the same ref for the same operation with the same payload in the
same program runs once, and every later call gets the first call's stored
result back, marked `"replayed": True`. The same ref with a DIFFERENT payload
-- one email quoting two products -- is a second, ordinary write; both keep
the ref for provenance. A pre-check lookup
answers the common case cheaply; the unique constraint on OperationCall is
what makes it hold under a race, where the loser's insert fails and it
returns the winner's result instead. See
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.3-§3.4.
"""

import hashlib
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from connect_labs.supply_chain.history.context import current_overrides, write_context
from connect_labs.supply_chain.history.models import OperationCall

# A stored result over this many bytes of JSON is replaced by a summary: a
# list operation's result can be arbitrarily large, and the call table is not
# where a caller should expect to find it again.
RESULT_LIMIT = 64 * 1024


def payload_digest(payload) -> str:
    """sha256 of `payload` as canonical JSON (sorted keys, no whitespace).

    Taken of the validated payload with `source` removed and before provenance
    stamping, so two callers forwarding the same evidence produce one digest.
    """
    canonical = json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ids(rows):
    return [row.get("id") for row in rows if isinstance(row, dict) and "id" in row]


def _summary(result):
    if isinstance(result, list):
        return {"count": len(result), "ids": _ids(result)}
    if isinstance(result, dict):
        summary = {"id": result["id"]} if "id" in result else {}
        for key, value in result.items():
            if isinstance(value, list):
                summary[key] = {"count": len(value), "ids": _ids(value)}
        return summary
    return {}


def stored_result(result):
    """`result` as the call row keeps it: wire JSON, or a summary when too large."""
    wire = json.loads(json.dumps(result, cls=DjangoJSONEncoder))
    if len(json.dumps(wire).encode()) > RESULT_LIMIT:
        return {"truncated": True, "summary": _summary(wire)}
    return wire


def _channel(access, channel):
    if channel:
        return channel
    if getattr(access, "request", None) is not None:
        return "web"
    return "command" if getattr(access, "user", None) is None else "web"


def _actor(access):
    user = getattr(access, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def _replay(call, when=None):
    """The stored result, with the replay noted on the call it answered from.

    A queryset update, as for `result`: the row is append-only to `save()`,
    and a replay changes nothing but that it happened.
    """
    OperationCall.objects.filter(pk=call.pk).update(
        replay_count=F("replay_count") + 1, last_replayed_at=when or timezone.now()
    )
    result = call.result
    if isinstance(result, dict):
        return {**result, "replayed": True}
    return result


def _is_agent(actor):
    email = (getattr(actor, "email", "") or "").lower()
    return bool(email) and email in {e.lower() for e in settings.LABS_AGENT_ACCOUNT_EMAILS}


def run_recorded(operation, access, payload, source, channel, *, digest="", actor=None, acting_org_id=None, then=None):
    """Run one write as an OperationCall; return its result, or a replay.

    Deduplication needs a program. The unique constraint treats a NULL
    program_id as distinct from every other, so a lookup on `IS NULL` would
    disagree with it -- sequential duplicates would replay across every
    unscoped caller while concurrent ones both wrote. An unscoped write
    therefore still records its source, for provenance, and never replays.

    A replay returns the result as it was STORED: wire JSON through
    DjangoJSONEncoder, so a date comes back as its ISO string rather than a
    `date`, plus `"replayed": True` when the result is a dict. A result over
    RESULT_LIMIT is stored, and so replayed, as its summary.

    `actor` and `acting_org_id` attribute the call for a trusted in-process
    caller acting for a partner (the market, an update link), whose data
    access runs as SYSTEM. They are keyword-only and no adapter passes them:
    an MCP or HTTP caller can never claim to be someone else. `then(result)`
    runs inside the call -- same transaction, same write context -- so a
    follow-up save the caller owes (marking a quote as the supplier's own) is
    attributed to this call too; if it returns a value, that replaces the result.

    Raises PermissionError if seed overrides are active for a different
    program than the one this write targets.
    """
    overrides = current_overrides()
    program_id = getattr(access, "program_id", None)
    if overrides and overrides.get("program_id") != program_id:
        raise PermissionError(
            f"seed overrides were opened for program {overrides.get('program_id')!r}, "
            f"not the program {program_id!r} this write targets"
        )
    actor = overrides.get("actor") or actor or _actor(access)
    fields = dict(
        program_id=program_id,
        operation=operation.name,
        actor=actor,
        actor_is_agent=_is_agent(actor),
        channel=overrides.get("channel") or _channel(access, channel),
        acting_org_id=acting_org_id,
        source_ref=(source or {}).get("ref", ""),
        source_excerpt=(source or {}).get("excerpt", ""),
        payload_digest=digest,
    )
    if "recorded_at" in overrides:
        fields["recorded_at"] = overrides["recorded_at"]
    deduplicates = bool(fields["source_ref"]) and program_id is not None
    key = dict(program_id=program_id, operation=operation.name, source_ref=fields["source_ref"], payload_digest=digest)

    if deduplicates:
        existing = OperationCall.objects.filter(**key).first()
        if existing is not None:
            return _replay(existing, overrides.get("recorded_at"))
    try:
        with transaction.atomic():
            call = OperationCall.objects.create(**fields)
            with write_context(call):
                result = operation.handler(access, **payload)
                if then is not None:
                    followed = then(result)
                    if followed is not None:
                        result = followed
            # A queryset update, not save(): OperationCall is append-only, and
            # the result is only known once the handler has run.
            OperationCall.objects.filter(pk=call.pk).update(result=stored_result(result))
            return result
    except IntegrityError as error:
        if not deduplicates:
            raise
        # Either a concurrent call with the same ref won the race, or the
        # handler hit a constraint of its own. Only the first has a winner to
        # return; the second must surface unchanged.
        # Accepted narrow race: a handler's own IntegrityError that coincides
        # with a concurrent winner is answered with the winner's result.
        try:
            winner = OperationCall.objects.get(**key)
        except (OperationCall.DoesNotExist, OperationCall.MultipleObjectsReturned):
            raise error from None
        return _replay(winner, overrides.get("recorded_at"))
