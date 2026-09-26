"""Run one write operation as a recorded, attributed, idempotent OperationCall.

`call_operation` hands every write here. The call row, the handler's writes
(and the Revisions they produce, attributed to the call through
`write_context`) and the stored result commit together or not at all.

Idempotency rides on the caller's `source.ref`: the same ref for the same
operation in the same program runs once, and every later call gets the first
call's stored result back, marked `"replayed": True`. A pre-check lookup
answers the common case cheaply; the unique constraint on OperationCall is
what makes it hold under a race, where the loser's insert fails and it
returns the winner's result instead. See
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.3-§3.4.
"""

import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction

from connect_labs.supply_chain.history.context import current_overrides, write_context
from connect_labs.supply_chain.history.models import OperationCall


def _channel(access, channel):
    if channel:
        return channel
    if getattr(access, "request", None) is not None:
        return "web"
    return "command" if getattr(access, "user", None) is None else "web"


def _actor(access):
    user = getattr(access, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def _replay(call):
    result = call.result
    if isinstance(result, dict):
        return {**result, "replayed": True}
    return result


def _is_agent(actor):
    email = (getattr(actor, "email", "") or "").lower()
    return bool(email) and email in {e.lower() for e in settings.LABS_AGENT_ACCOUNT_EMAILS}


def run_recorded(operation, access, payload, source, channel):
    overrides = current_overrides()
    actor = overrides.get("actor") or _actor(access)
    fields = dict(
        program_id=getattr(access, "program_id", None),
        operation=operation.name,
        actor=actor,
        actor_is_agent=_is_agent(actor),
        channel=overrides.get("channel") or _channel(access, channel),
        source_ref=(source or {}).get("ref", ""),
        source_excerpt=(source or {}).get("excerpt", ""),
    )
    if "recorded_at" in overrides:
        fields["recorded_at"] = overrides["recorded_at"]
    key = dict(program_id=fields["program_id"], operation=operation.name, source_ref=fields["source_ref"])

    if fields["source_ref"]:
        existing = OperationCall.objects.filter(**key).first()
        if existing is not None:
            return _replay(existing)
    try:
        with transaction.atomic():
            call = OperationCall.objects.create(**fields)
            with write_context(call):
                result = operation.handler(access, **payload)
            # A queryset update, not save(): OperationCall is append-only, and
            # the result is only known once the handler has run.
            OperationCall.objects.filter(pk=call.pk).update(
                result=json.loads(json.dumps(result, cls=DjangoJSONEncoder))
            )
            return result
    except IntegrityError as error:
        if not fields["source_ref"]:
            raise
        # Either a concurrent call with the same ref won the race, or the
        # handler hit a constraint of its own. Only the first has a winner to
        # return; the second must surface unchanged.
        try:
            winner = OperationCall.objects.get(**key)
        except OperationCall.DoesNotExist:
            raise error from None
        return _replay(winner)
