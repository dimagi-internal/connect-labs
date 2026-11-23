"""Who, and whether: which of a program's Connect Workers a playbook may reach now, and why not.

Every start (a person's preview and commit, a workflow action, a schedule, a state event) and
every inbound match judges a worker here, so the rules hold the same whoever starts:

* membership first: a program only ever messages its own Connect Workers, on an opportunity
  they are on, before the playbook's `eligible` rule narrows which of them;
* the channel: consent in the app, and STOP;
* caps: ``threads_per_worker_per_week``, and one open conversation per channel at a time
  (one-way notices excepted);
* quiet hours, in the worker's time zone, hold the opening back until they end rather than
  refusing it.

`candidate` puts these together into a `Candidate`: the worker, and either the reason they are
left out or the exact opening they would get.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from connect_labs.messaging import config, flows, playbooks, service
from connect_labs.messaging.models import Address, AddressPurpose, Direction, Thread, ThreadState
from connect_labs.messaging.service import MessagingError
from connect_labs.workers.models import ConnectWorker, WorkerMembership


@dataclass
class Candidate:
    """One Connect Worker a start would reach, and what would happen to them."""

    username: str
    worker: ConnectWorker | None = None
    opportunity_id: int | None = None
    blocked: str | None = None
    opening: str = ""
    variant: str = ""
    handler: dict = field(default_factory=dict)
    send_at: dt.datetime | None = None
    snapshot: dict = field(default_factory=dict)

    def describe(self) -> dict:
        out = {
            "username": self.username,
            "name": getattr(self.worker, "name", "") or self.username,
            "opportunity_id": self.opportunity_id,
            "eligible": self.blocked is None,
        }
        if self.blocked:
            out["blocked"] = self.blocked
        else:
            out.update({"opening": self.opening, "handler": self.handler})
            if self.variant:
                out["variant"] = self.variant
            if self.send_at:
                out["held_until"] = self.send_at.isoformat()
        if self.snapshot.get("stage"):
            out["stage"] = self.snapshot["stage"]
        return out


# ---------------------------------------------------------------------------
# The playbook and its handler
# ---------------------------------------------------------------------------


def outbound_playbook(program_id, key: str) -> dict:
    """The program's playbook `key`, if labs may start it. Raises `MessagingError` otherwise."""
    playbook = playbooks.get(program_id, key)
    if playbook is None:
        raise MessagingError("not_found", f"Program {program_id} has no messaging playbook {key!r}.")
    if not playbooks.is_enabled(playbook):
        raise MessagingError("playbook_off", f"Playbook {key!r} is turned off.")
    if playbook.get("direction") != "outbound":
        raise MessagingError("invalid", f"Playbook {key!r} is inbound: a Connect Worker starts it, not labs.")
    return playbook


def handler_ref(handler: dict) -> str:
    """A playbook handler's `Thread.handler_ref`: ``<agent>:<skill>``, the flow's key, the human queue."""
    return {
        "agent": f"{handler.get('agent', '')}:{handler.get('skill', '')}",
        "flow": handler.get("key", ""),
        "human": handler.get("queue") or "inbox",
        "none": "",
    }[handler["kind"]]


def flow_of(handler: dict) -> flows.Flow | None:
    return flows.FLOWS.get(handler.get("key")) if handler.get("kind") == "flow" else None


def purpose_of(handler: dict) -> str:
    """The channel a handler's thread goes on: the flow's own (a security code's), else the program's."""
    flow = flow_of(handler)
    return flow.purpose if flow else AddressPurpose.PROGRAM


def is_one_way(handler: dict) -> bool:
    flow = flow_of(handler)
    return handler.get("kind") == "none" or (flow is not None and flow.one_way)


# ---------------------------------------------------------------------------
# Eligibility: membership, then the playbook's rule
# ---------------------------------------------------------------------------


def _compare(value, ops: dict) -> bool:
    for op, target in ops.items():
        if op == "exists":
            if (value is not None) != bool(target):
                return False
            continue
        if value is None:
            return False
        try:
            if op == "eq" and not value == target:
                return False
            if op == "ne" and not value != target:
                return False
            if op == "gt" and not value > target:
                return False
            if op == "gte" and not value >= target:
                return False
            if op == "lt" and not value < target:
                return False
            if op == "lte" and not value <= target:
                return False
            if op == "in" and value not in (target or []):
                return False
        except TypeError:
            return False
    return True


def _facet(facets: dict, path: str):
    value = facets
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _snapshot(membership, facets: dict | None = None) -> dict:
    state = getattr(membership, "state", None) if membership is not None else None
    if state is None:
        return {"opportunity_id": membership.opportunity_id} if membership is not None else {}
    return {
        "opportunity_id": membership.opportunity_id,
        "stage": state.stage,
        "facets": facets if facets is not None else state.facets,
        "computed_at": state.computed_at.isoformat(),
    }


def eligibility(
    playbook: dict, worker: ConnectWorker, program_id, opportunity_id=None
) -> tuple[str | None, dict, int | None]:
    """(why not eligible, or None; the state it was judged on; the opportunity it came from)."""
    rule = playbook.get("eligible") or {}
    memberships = WorkerMembership.objects.filter(worker=worker, program_id=program_id).select_related("state")
    if opportunity_id is not None:
        memberships = memberships.filter(opportunity_id=opportunity_id)
    memberships = list(memberships.order_by("opportunity_id"))
    # Before any rule: a program only ever messages its own Connect Workers, on an opportunity
    # they are on. An empty rule widens WHICH of them, never beyond them, and an opportunity a
    # caller names is checked here because a thread's opportunity decides who may read it.
    if not memberships:
        if opportunity_id is not None:
            return "not on that opportunity in this program (as labs last synced)", {}, opportunity_id
        return "not on an opportunity in this program (as labs last synced)", {}, opportunity_id
    if not rule:
        # Any member may get it; the state is still recorded, from the opportunity named (or the only one).
        chosen = memberships[0] if len(memberships) == 1 else None
        return (
            None,
            _snapshot(chosen),
            opportunity_id if opportunity_id is not None else getattr(chosen, "opportunity_id", None),
        )
    reasons = []
    # Messaging's own facets (facets.py), computed live, only when the rule asks for one.
    live = None
    if any(p.split(".", 1)[0] == "messaging" for p in rule.get("facets") or {}):
        from connect_labs.messaging.facets import messaging_facets

        live = messaging_facets(worker, program_id)
    for membership in memberships:
        state = getattr(membership, "state", None)
        if state is None:
            reasons.append("no state computed yet")
            continue
        if rule.get("stages") and state.stage not in rule["stages"]:
            reasons.append(f"stage is {state.stage}")
            continue
        facets = dict(state.facets or {})
        if live is not None:
            facets["messaging"] = {**(facets.get("messaging") or {}), **live}
        failing = [p for p, ops in (rule.get("facets") or {}).items() if not _compare(_facet(facets, p), ops)]
        if failing:
            reasons.append(f"{', '.join(failing)} not as required")
            continue
        return None, _snapshot(membership, facets if live is not None else None), membership.opportunity_id
    return "not eligible: " + "; ".join(sorted(set(reasons))), {}, opportunity_id


# ---------------------------------------------------------------------------
# The channel, the caps and quiet hours
# ---------------------------------------------------------------------------


def _zone(worker: ConnectWorker, program_id) -> ZoneInfo:
    for name in (worker.timezone, config.program_settings(program_id).get("timezone"), "UTC"):
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def quiet_until(
    playbook: dict, worker: ConnectWorker, program_id, now: dt.datetime | None = None
) -> dt.datetime | None:
    """If it is quiet hours for the worker now, when they end (UTC); else None."""
    quiet = (playbook.get("caps") or {}).get("quiet_hours")
    if not quiet:
        return None
    now = now or timezone.now()
    zone = _zone(worker, program_id)
    local = now.astimezone(zone)
    start = dt.time.fromisoformat(quiet["start"])
    end = dt.time.fromisoformat(quiet["end"])
    t = local.time()
    if start == end:
        return None
    inside = (start <= t < end) if start < end else (t >= start or t < end)
    if not inside:
        return None
    release = dt.datetime.combine(local.date(), end, tzinfo=zone)
    if release <= local:
        release += dt.timedelta(days=1)
    return release.astimezone(dt.UTC)


def _channel_reason(worker: ConnectWorker, program_id, purpose: str) -> str | None:
    address = Address.objects.filter(worker=worker, channel_source=service.channel_source(program_id, purpose)).first()
    if address is None:
        return None
    if not address.consent:
        return "turned this channel off in their app"
    if address.opted_out_at:
        return "asked us to stop (STOP)"
    return None


def _week_cap_reason(playbook: dict, key: str, worker: ConnectWorker, program_id, now) -> str | None:
    cap = (playbook.get("caps") or {}).get("threads_per_worker_per_week")
    if cap is None:
        return None
    started = Thread.objects.filter(
        worker=worker,
        program_id=program_id,
        playbook_key=key,
        direction=Direction.OUTBOUND,
        opened_at__gte=now - dt.timedelta(days=7),
    ).count()
    if started >= cap:
        return f"already started {started} time{'s' if started != 1 else ''} this week (cap {cap})"
    return None


def _busy_reason(worker: ConnectWorker, program_id, purpose: str, handler: dict) -> str | None:
    if is_one_way(handler):
        return None
    open_one = (
        Thread.objects.filter(
            worker=worker,
            state=ThreadState.OPEN,
            address__channel_source=service.channel_source(program_id, purpose),
        )
        .order_by("-opened_at")
        .first()
    )
    if open_one is not None:
        return f"a conversation is already open with them ({open_one.playbook_key or 'thread'} {open_one.pk})"
    return None


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------


def _values(worker: ConnectWorker, program_id, context: dict) -> dict:
    name = worker.name or worker.username
    return {
        **flows.prepare(context or {}),
        "name": name,
        "first_name": name.split()[0] if name else worker.username,
        "program": config.program_settings(program_id).get("channel_name") or "your program",
    }


def candidate(
    playbook: dict, key: str, program_id, username: str, opportunity_id=None, context=None, now=None
) -> Candidate:
    """Judge one Connect Worker for a start of `key`: left out (`blocked`), or what they would get."""
    now = now or timezone.now()
    cand = Candidate(username=username, opportunity_id=opportunity_id)
    worker = ConnectWorker.objects.filter(username__iexact=username, erased_at__isnull=True).first()
    if worker is None:
        cand.blocked = "not a Connect Worker labs knows"
        return cand
    cand.worker = worker
    reason, snapshot, opp = eligibility(playbook, worker, program_id, opportunity_id)
    cand.snapshot, cand.opportunity_id = snapshot, opp
    if reason:
        cand.blocked = reason
        return cand
    variant = playbooks.assign_variant(playbook, key, worker.username) or {}
    cand.variant = variant.get("key", "")
    cand.handler = dict(variant.get("handler") or playbook["handler"])
    purpose = purpose_of(cand.handler)
    cand.blocked = (
        _channel_reason(worker, program_id, purpose)
        or _week_cap_reason(playbook, key, worker, program_id, now)
        or _busy_reason(worker, program_id, purpose, cand.handler)
    )
    if cand.blocked:
        return cand
    values = _values(worker, program_id, context or {})
    flow = flow_of(cand.handler)
    try:
        text = variant.get("opening") or playbook.get("opening")
        cand.opening = playbooks.fill(text, values) if text else flow.opening(values)
    except (flows.FlowError, AttributeError) as e:
        cand.blocked = str(e) if isinstance(e, flows.FlowError) else "no opening to send"
        return cand
    cand.send_at = quiet_until(playbook, worker, program_id, now)
    return cand
