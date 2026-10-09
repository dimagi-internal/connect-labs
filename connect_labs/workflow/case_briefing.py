"""A coaching conversation about ONE CASE, briefed from the case's state as of the run.

A case state is registry data (``semantic/case_states.py``): a bool property of the
case, computed as of the report date, with ``case_state:`` meta saying what it means,
what the facts are, how to picture it and how a coach should talk about it. A saved
run holds every case's states in its case index; this module reads one case's row --
from the run, or evaluated as of the run when the run stores no case list -- and its
visits, and writes the coach's briefing. Nothing here knows what a programme is.

The briefing is a contract with the coach bot (ACE ``lib/coach-briefing.ts``
``renderCaseBriefing``)::

    BRIEFING (system text — do not show to the worker)
    Programme: <programme>
    Worker: <worker display name, or username>
    Case: <case display name>
    About this case: <the registry's case_about line>
    Topic: <case state label> [<case state property name>]
    What it means: <the property's means>
    How to talk about it: <case_state.coach.approach>
    The step to agree: <case_state.coach.next_steps>
    What it does not tell you: <case_state.coach.limits>
    What the data shows: <the case state's facts, filled from the case>
    Earlier coaching on this case: <d Mon yyyy> — <label>; agreed: <step | none>   (only when given)
    Visits, oldest first:
    - <d Mon yyyy>: <each case_series line>; ...
    Follow your conversation steps from the opening.
"""

from __future__ import annotations

import logging
from typing import Any

from connect_labs.semantic import case_states
from connect_labs.workflow import coach_briefing

logger = logging.getLogger(__name__)

COACH_LINES = (
    ("What it means", None),
    ("How to talk about it", "approach"),
    ("The step to agree", "next_steps"),
    ("What it does not tell you", "limits"),
)


class CaseBriefingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def guidance_problems(state: dict) -> list[str]:
    """What a case state lacks for a conversation: its meaning and all three coach keys."""
    missing = [] if (state.get("means") or "").strip() else ["means"]
    coach = state.get("coach") or {}
    missing += [
        f"case_state.coach.{k}" for k in ("approach", "next_steps", "limits") if not (coach.get(k) or "").strip()
    ]
    return missing


def earlier_line(earlier: dict | None, states: list[dict]) -> str | None:
    """The follow-up line, from what the CALLER worked out (Labs looks nothing up)."""
    if not isinstance(earlier, dict) or not earlier.get("date"):
        return None
    when = case_states._as_date(earlier.get("date"))
    label = str(earlier.get("label") or "").strip()
    label = next((s["label"] for s in states if s["name"] == label), label) or "a coaching conversation"
    agreed = str(earlier.get("agreed") or "").strip() or "none"
    shown = case_states.day_long(when) if when else earlier["date"]
    return f"Earlier coaching on this case: {shown} — {label}; agreed: {agreed}"


def render_case_briefing(
    *,
    programme: str,
    worker: str,
    case_name: str,
    about: str,
    state: dict,
    facts: str,
    visit_lines: list[str],
    earlier: str | None = None,
) -> str:
    coach = state.get("coach") or {}
    lines = [
        coach_briefing.HEADER,
        f"Programme: {programme}",
        f"Worker: {worker}",
        f"Case: {case_name}",
        f"About this case: {about or 'not recorded'}",
        f"Topic: {state['label']} [{state['name']}]",
        f"What it means: {state['means'].strip()}",
        f"How to talk about it: {coach['approach'].strip()}",
        f"The step to agree: {coach['next_steps'].strip()}",
        f"What it does not tell you: {coach['limits'].strip()}",
        f"What the data shows: {facts or 'not recorded'}",
        *([earlier] if earlier else []),
        "Visits, oldest first:",
        *visit_lines,
        coach_briefing.FOOTER,
    ]
    return "\n".join(lines)


def is_case_briefing(text: str | None) -> bool:
    return coach_briefing.is_briefing(text) and "\nCase: " in (text or "") and "\nTopic: " in (text or "")


def case_briefing_summary(text: str) -> dict:
    """A case briefing in plain parts, for the person confirming it (display only)."""
    out: dict[str, Any] = {}
    prefixes = (
        ("Case: ", "case"),
        ("Topic: ", "topic"),
        ("What it means: ", "means"),
        ("What the data shows: ", "facts"),
        ("Earlier coaching on this case: ", "earlier"),
    )
    for line in (text or "").splitlines():
        for prefix, key in prefixes:
            if line.startswith(prefix):
                out[key] = line[len(prefix) :].strip()
    topic = out.get("topic") or ""
    if topic.endswith("]") and "[" in topic:
        out["topic"], out["case_state"] = topic[: topic.rindex("[")].strip(), topic[topic.rindex("[") + 1 : -1]
    return out


# ---------------------------------------------------------------------------
# A case, as of a run
# ---------------------------------------------------------------------------


class CaseSource:
    """One run's cases, read as the person: each case's row (its states and what
    presents them) and its visits, as of the run's report date."""

    def __init__(self, user, wda, run, definition, *, request=None):
        self.user, self.wda, self.run, self.definition, self.request = user, wda, run, definition, request
        self.as_of = getattr(run, "period_end", None)
        self._ctx: dict[int, dict] = {}
        self._rows: dict[tuple[int, str], dict | None] = {}
        self._stored = self._stored_cases()

    def _stored_cases(self) -> dict | None:
        """The run's stored case index, when it is a completed run that stored one."""
        from connect_labs.workflow.agent_sharing import graded_payload

        if not (getattr(self.run, "is_completed", False) and getattr(self.run, "snapshot", None)):
            return None
        payload = graded_payload(self.run.snapshot) or {}
        cases, catalog = payload.get("cases"), payload.get("caseStateCatalog")
        if not cases or not catalog:
            return None
        return {"cases": cases, "catalog": catalog, "display": payload.get("display") or {}}

    def context(self, opportunity_id: int) -> dict:
        from connect_labs.workflow.snapshot_builders import case_context

        if opportunity_id not in self._ctx:
            self._ctx[opportunity_id] = case_context(
                self.definition,
                opportunity_id=opportunity_id,
                access_token=getattr(self.wda, "access_token", None),
                request=self.request,
                program_id=getattr(self.run, "program_id", None),
            )
        return self._ctx[opportunity_id]

    def catalog(self, opportunity_id: int) -> list[dict]:
        if self._stored:
            return self._stored["catalog"]
        return case_states.catalog(self.context(opportunity_id)["props_doc"])

    def props_doc(self, opportunity_id: int) -> dict:
        return self.context(opportunity_id)["props_doc"]

    def label_field(self, opportunity_id: int) -> str | None:
        if self._stored:
            return ((self._stored["display"].get("entity") or {}).get("label_field")) or None
        display = self.context(opportunity_id)["full_registry"].get("display") or {}
        return ((display.get("entity") or {}).get("label_field")) or None

    def case_name(self, opportunity_id: int, row: dict, fallback: str = "") -> str:
        """What the case is called: the registry's ``case_name`` (its mother's and baby's
        names, say), else its label field, else ``fallback``."""
        label_field = self.label_field(opportunity_id)
        label = str((row.get(label_field) if label_field else None) or "") or fallback
        return case_states.case_display_name(self.props_doc(opportunity_id), row, label) or label

    def programme(self, opportunity_id: int) -> str:
        display = (
            self._stored["display"]
            if self._stored
            else (self.context(opportunity_id)["full_registry"].get("display") or {})
        )
        title = display.get("title")
        return (
            title.strip()
            if isinstance(title, str) and title.strip()
            else (getattr(self.definition, "name", "") or "this programme")
        )

    def case(self, opportunity_id: int, entity_id: str) -> dict | None:
        key = (int(opportunity_id), str(entity_id))
        if key not in self._rows:
            row = None
            if self._stored:
                row = next(
                    (
                        c
                        for c in self._stored["cases"]
                        if str(c.get("entity_id")) == key[1] and int(c.get("opportunity_id") or 0) == key[0]
                    ),
                    None,
                )
            if row is None:
                from connect_labs.workflow.snapshot_builders import case_rows

                rows = case_rows(self.context(key[0]), opportunity_id=key[0], entity_ids=[key[1]], as_of=self.as_of)
                row = rows[0] if rows else None
            self._rows[key] = row
        return self._rows[key]

    def visits(self, opportunity_id: int, entity_id: str) -> list[dict]:
        from connect_labs.workflow.snapshot_builders import case_visit_rows

        return case_visit_rows(
            self.context(int(opportunity_id)),
            opportunity_id=int(opportunity_id),
            entity_id=str(entity_id),
            as_of=self.as_of,
        )

    def series(self, opportunity_id: int) -> list[dict]:
        return case_states.case_series(self.props_doc(opportunity_id))


# ---------------------------------------------------------------------------
# A run's cases, by case state, per worker (the `workflow_run_cases` read)
# ---------------------------------------------------------------------------


def cases_view(
    cases: list[dict],
    catalog: list[dict],
    *,
    case_state: str | None = None,
    worker_keys: list[str] | None = None,
    names: dict[str, str] | None = None,
    label_field: str | None = None,
    per_worker: int = 5,
    props_doc: dict | None = None,
) -> dict:
    """Per worker, the cases in each case state (most urgent first, most recent
    evidence first within one), with each case's facts and evidence as of the run, and
    the counts. ``case_state`` keeps one; ``worker_keys`` some workers."""
    from connect_labs.workflow.agent_sharing import worker_key

    names = names or {}
    wanted = [s for s in catalog if not case_state or s["name"] == case_state]
    workers: dict[str, dict] = {}
    totals = {s["name"]: 0 for s in wanted}
    for c in cases:
        held = [s for s in case_states.true_case_states(c, catalog) if s in wanted]
        if not held:
            continue
        key = worker_key(int(c.get("opportunity_id") or 0), str(c.get("username") or ""))
        if worker_keys and key not in worker_keys:
            continue
        state = held[0]
        totals[state["name"]] += 1
        w = workers.setdefault(
            key, {"key": key, "name": names.get(key) or c.get("username"), "counts": {}, "cases": []}
        )
        w["counts"][state["name"]] = w["counts"].get(state["name"], 0) + 1
        w["cases"].append(
            {
                "entity_id": c.get("entity_id"),
                "name": case_states.case_display_name(
                    props_doc or {}, c, (c.get(label_field) if label_field else None) or c.get("entity_id")
                ),
                "case_state": state["name"],
                "label": state["label"],
                "facts": case_states.facts(state, c),
                "date": str(c.get(state["date"]))[:10] if state.get("date") and c.get(state["date"]) else None,
                "case_states": [s["name"] for s in held],
                "evidence": {e: c.get(e) for e in state.get("evidence") or []},
            }
        )
    order = {s["name"]: i for i, s in enumerate(catalog)}
    for w in workers.values():
        # most urgent case state first; most recent evidence first within one
        w["cases"].sort(key=lambda x: (order.get(x["case_state"], 99), -_ordinal(x["date"])))
        w["cases"] = w["cases"][:per_worker]
    out_workers = sorted(
        workers.values(), key=lambda w: (min(order.get(k, 99) for k in w["counts"]), -sum(w["counts"].values()))
    )
    return {
        "case_states": [
            {k: s.get(k) for k in ("name", "label", "means", "tone", "priority", "date", "evidence", "coach")}
            for s in wanted
        ],
        "counts": totals,
        "workers": out_workers,
    }


def _ordinal(date: str | None) -> int:
    d = case_states._as_date(date) if date else None
    return d.toordinal() if d else 0


def run_cases(user, wda, run, definition, *, request=None, worker_keys=None, case_state=None, per_worker=5) -> dict:
    """``cases_view`` over a run: its stored case index (a completed run that stored
    one), else every case of its opportunities evaluated as of its report date."""
    from connect_labs.workflow.actions import run_roster
    from connect_labs.workflow.agent_sharing import split_worker_key
    from connect_labs.workflow.snapshot_builders import case_rows
    from connect_labs.workflow.visit_cache import workflow_opportunity_ids

    source = CaseSource(user, wda, run, definition, request=request)
    opps = workflow_opportunity_ids(definition, getattr(run, "opportunity_id", None))
    if worker_keys:
        opps = sorted({split_worker_key(k)[0] for k in worker_keys} & set(opps))
    if not opps:
        raise CaseBriefingError("no_opportunities", "There is no opportunity of this workflow to read cases in.")
    if source._stored:
        cases, origin = source._stored["cases"], "stored"
    else:
        only = split_worker_key(worker_keys[0])[1] if worker_keys and len(worker_keys) == 1 else None
        cases, origin = [], "live"
        for opp in opps:
            cases += case_rows(source.context(opp), opportunity_id=opp, username=only, as_of=source.as_of)
    catalog = source.catalog(opps[0])
    if not catalog:
        raise CaseBriefingError("no_case_states", "This workflow's registry declares no case states.")
    roster = run_roster(wda, run, definition)
    view = cases_view(
        cases,
        catalog,
        case_state=case_state,
        worker_keys=worker_keys,
        names={k: w["name"] for k, w in roster.items()},
        label_field=source.label_field(opps[0]),
        per_worker=per_worker,
        props_doc=source.props_doc(opps[0]),
    )
    return {"as_of": str(source.as_of)[:10] if source.as_of else None, "source": origin, **view}
