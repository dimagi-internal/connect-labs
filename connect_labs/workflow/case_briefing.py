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

import hashlib
import logging
import time
from typing import Any

from django.core.cache import cache

from connect_labs.semantic import case_states
from connect_labs.workflow import coach_briefing

logger = logging.getLogger(__name__)

#: How long one person's evaluated case list of an opportunity, as of a run's report
#: date, is kept. A run's report date is fixed, so the answer changes only when its
#: visits do (a synthetic regeneration, a late sync); half an hour bounds that.
RUN_CASES_SECONDS = 30 * 60
RUN_CASES_FILL_SECONDS = 5 * 60
RUN_CASES_WAIT_SECONDS = 3 * 60

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
        #: Seconds spent per stage of a read, for the answer's `timing` (where a slow one goes).
        self.timings: dict[str, float] = {}

    def _timed(self, stage: str, fn):
        started = time.monotonic()
        try:
            return fn()
        finally:
            self.timings[stage] = round(self.timings.get(stage, 0.0) + time.monotonic() - started, 2)

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
        if opportunity_id not in self._ctx:
            self._ctx[opportunity_id] = self._resolve([opportunity_id])
        return self._ctx[opportunity_id]

    def _resolve(self, opportunity_ids: list[int]) -> dict:
        """``case_context`` over these opportunities, the first one the anchor."""
        from connect_labs.workflow.snapshot_builders import case_context

        return self._timed(
            "resolve",
            lambda: case_context(
                self.definition,
                opportunity_id=opportunity_ids[0],
                opportunity_ids=opportunity_ids if len(opportunity_ids) > 1 else None,
                access_token=getattr(self.wda, "access_token", None),
                request=self.request,
                program_id=getattr(self.run, "program_id", None),
            ),
        )

    def catalog(self, opportunity_id: int) -> list[dict]:
        if self._stored:
            return self._stored["catalog"]
        return case_states.catalog(self.props_doc(opportunity_id))

    def props_doc(self, opportunity_id: int) -> dict:
        return self.registry_meta(opportunity_id)["props_doc"]

    def label_field(self, opportunity_id: int) -> str | None:
        if self._stored:
            return ((self._stored["display"].get("entity") or {}).get("label_field")) or None
        display = self.registry_meta(opportunity_id)["display"]
        return ((display.get("entity") or {}).get("label_field")) or None

    def case_name(self, opportunity_id: int, row: dict, fallback: str = "") -> str:
        """What the case is called: the registry's ``case_name`` (its mother's and baby's
        names, say), else its label field, else ``fallback``."""
        label_field = self.label_field(opportunity_id)
        label = str((row.get(label_field) if label_field else None) or "") or fallback
        return case_states.case_display_name(self.props_doc(opportunity_id), row, label) or label

    def programme(self, opportunity_id: int) -> str:
        display = self._stored["display"] if self._stored else self.registry_meta(opportunity_id)["display"]
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
            if row is None and not self._stored:
                # The opportunity's case list, when a `workflow_run_cases` read kept it:
                # the preview that follows a case read then costs no evaluation.
                kept = cache.get(self.cases_key(key[0]))
                if kept:
                    row = next((c for c in kept if str(c.get("entity_id")) == key[1]), None)
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

    # -- one evaluation per opportunity, kept per person (the `workflow_run_cases` read) --

    def _key(self, *parts) -> str:
        """A cache key for this person's read of this workflow as of this run's report date.
        Per person, because the read resolves the workflow's pipelines as them; the
        definition's version is in it, so an edited workflow is read afresh."""
        who = getattr(self.user, "pk", None) or getattr(self.user, "username", "")
        base = (who, self.definition.id, getattr(self.definition, "version", None), str(self.as_of or "")[:10])
        return "run-cases:" + hashlib.sha256(repr((*base, *parts)).encode()).hexdigest()

    def cases_key(self, opportunity_id: int) -> str:
        return self._key("cases", int(opportunity_id))

    def registry_meta(self, opportunity_id: int) -> dict:
        """What presenting a case needs from the registry -- its props document and display
        -- kept like the cases, so a warm read resolves nothing."""
        key = self._key("meta")
        meta = cache.get(key)
        if meta is None:
            ctx = self.context(opportunity_id)
            meta = {"props_doc": ctx["props_doc"], "display": ctx["full_registry"].get("display") or {}}
            cache.set(key, meta, RUN_CASES_SECONDS)
        return meta

    def opportunity_cases(self, opportunity_ids: list[int], username: str | None = None) -> list[dict]:
        """Every case of these opportunities as of the run (one worker's, with
        ``username``). Each opportunity's list is evaluated once and kept; the ones not
        kept are evaluated TOGETHER, in one pass. One worker of an opportunity not kept is
        evaluated alone (a worker's visits are a fraction of the opportunity's)."""
        from connect_labs.workflow.snapshot_builders import case_rows

        opps = [int(o) for o in opportunity_ids]
        kept = cache.get_many([self.cases_key(o) for o in opps]) or {}
        out: list[dict] = []
        missing: list[int] = []
        for o in opps:
            rows = kept.get(self.cases_key(o))
            if rows is None:
                missing.append(o)
            else:
                out += [c for c in rows if not username or str(c.get("username")) == username]
        if not missing:
            return out
        if username and len(missing) == 1:
            ctx = self.context(missing[0])
            return out + self._timed(
                "evaluate", lambda: case_rows(ctx, opportunity_id=missing[0], username=username, as_of=self.as_of)
            )
        fresh = self._evaluate_once(missing)
        return out + [c for c in fresh if not username or str(c.get("username")) == username]

    def _evaluate_once(self, missing: list[int]) -> list[dict]:
        """Evaluate ``missing`` and keep each opportunity's list. Two reads of the same set
        at once (a page agent retrying, two people on one page) evaluate it once: the
        second waits for the first's answer, and evaluates itself only if that never
        comes (a cache that is down, a fill that failed)."""
        from connect_labs.workflow.snapshot_builders import case_rows_of

        keys = [self.cases_key(o) for o in missing]
        claim = self._key("fill", tuple(missing))
        claimed = cache.add(claim, 1, RUN_CASES_FILL_SECONDS)
        if not claimed:
            deadline = time.monotonic() + RUN_CASES_WAIT_SECONDS
            while time.monotonic() < deadline and cache.get(claim) is not None:
                time.sleep(1.0)
                kept = cache.get_many(keys) or {}
                if len(kept) == len(keys):
                    return [c for k in keys for c in kept[k]]
        try:
            ctx = self._many_context(missing)
            fresh = self._timed("evaluate", lambda: case_rows_of(ctx, opportunity_ids=missing, as_of=self.as_of))
            by_opp: dict[int, list[dict]] = {}
            for c in fresh:
                by_opp.setdefault(int(c.get("opportunity_id") or 0), []).append(c)
            # An opportunity with no case is not kept: its raw visits may simply be cold
            # (they expire), and a kept empty list would hide them once they are back.
            cache.set_many(
                {self.cases_key(o): rows for o, rows in by_opp.items() if rows and o in missing}, RUN_CASES_SECONDS
            )
            return fresh
        finally:
            if claimed:
                cache.delete(claim)

    def _many_context(self, opportunity_ids: list[int]) -> dict:
        if len(opportunity_ids) == 1:
            return self.context(opportunity_ids[0])
        return self._resolve(opportunity_ids)

    def worker_names(self, roster_reader, opportunity_ids: list[int]) -> dict[str, str]:
        """``{worker key: display name}`` of these opportunities, kept like the cases (the
        roster is a Connect export call per opportunity)."""
        key = self._key("names", tuple(sorted(int(o) for o in opportunity_ids)))
        names = cache.get(key)
        if names is None:
            roster = self._timed("roster", lambda: roster_reader(opportunity_ids))
            names = {k: w["name"] for k, w in roster.items()}
            if names:
                cache.set(key, names, RUN_CASES_SECONDS)
        return names


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


#: In the summary, how many of each opportunity's workers are listed when the read spans
#: several opportunities (all of them when it is one), and how long a fact line runs.
SUMMARY_WORKERS_PER_OPPORTUNITY = 3
SUMMARY_WORKERS_MAX = 100
SUMMARY_FACTS_CHARS = 160


def summary_view(view: dict, opportunity_ids: list[int]) -> dict:
    """``cases_view`` (with ``per_worker=1``) folded small: per opportunity, the counts and
    its most urgent workers; per worker, the counts and their ONE most urgent case. The
    case states keep what an agent names them by; their coaching guidance comes with the
    full case lists (``detail: cases``)."""
    order = {s["name"]: i for i, s in enumerate(view["case_states"])}
    opps: dict[int, dict] = {
        int(o): {"opportunity_id": int(o), "workers_with_cases": 0, "counts": {}, "most_urgent_workers": []}
        for o in opportunity_ids
    }
    limit = SUMMARY_WORKERS_MAX if len(opportunity_ids) == 1 else SUMMARY_WORKERS_PER_OPPORTUNITY
    for w in view["workers"]:  # most urgent worker first
        try:
            opp = int(str(w["key"]).split("::", 1)[0])
        except ValueError:
            continue
        o = opps.setdefault(
            opp, {"opportunity_id": opp, "workers_with_cases": 0, "counts": {}, "most_urgent_workers": []}
        )
        o["workers_with_cases"] += 1
        for k, n in w["counts"].items():
            o["counts"][k] = o["counts"].get(k, 0) + n
        if len(o["most_urgent_workers"]) >= limit:
            continue
        top = (w.get("cases") or [None])[0]
        o["most_urgent_workers"].append(
            {
                "key": w["key"],
                "name": w.get("name"),
                "counts": w["counts"],
                "most_urgent_case": top
                and {
                    "entity_id": top.get("entity_id"),
                    "name": top.get("name"),
                    "case_state": top.get("case_state"),
                    "date": top.get("date"),
                    "facts": _clip(top.get("facts"), SUMMARY_FACTS_CHARS),
                },
            }
        )
    for o in opps.values():
        o["counts"] = dict(sorted(o["counts"].items(), key=lambda kv: order.get(kv[0], 99)))
        o["more_workers"] = max(0, o["workers_with_cases"] - len(o["most_urgent_workers"]))
    return {
        "detail": "summary",
        "case_states": [{k: s.get(k) for k in ("name", "label", "tone", "priority")} for s in view["case_states"]],
        "counts": view["counts"],
        "opportunities": sorted(opps.values(), key=lambda o: o["opportunity_id"]),
    }


def _clip(text: str | None, n: int) -> str | None:
    if not text or len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def run_cases(
    user,
    wda,
    run,
    definition,
    *,
    request=None,
    worker_keys=None,
    case_state=None,
    per_worker=5,
    opportunity_ids=None,
    detail="cases",
) -> dict:
    """``cases_view`` over a run: its stored case index (a completed run that stored
    one), else the cases of its opportunities evaluated as of its report date.

    Only what is asked is evaluated: ``opportunity_ids`` (a subset of the run's own; one
    outside it is refused) and ``worker_keys`` narrow the opportunities read BEFORE any
    evaluation. ``detail: summary`` folds the answer to counts and each worker's one most
    urgent case (``summary_view``)."""
    from connect_labs.workflow.actions import run_roster
    from connect_labs.workflow.agent_sharing import split_worker_key
    from connect_labs.workflow.visit_cache import workflow_opportunity_ids

    started = time.monotonic()
    source = CaseSource(user, wda, run, definition, request=request)
    run_opps = [int(o) for o in workflow_opportunity_ids(definition, getattr(run, "opportunity_id", None))]
    opps = list(run_opps)
    if opportunity_ids:
        asked = {int(o) for o in opportunity_ids}
        outside = sorted(asked - set(run_opps))
        if outside:
            raise CaseBriefingError(
                "outside_run",
                f"Opportunit{'y' if len(outside) == 1 else 'ies'} {', '.join(map(str, outside))} "
                f"{'is' if len(outside) == 1 else 'are'} not part of this run (its opportunities: "
                f"{', '.join(map(str, run_opps))}).",
            )
        opps = [o for o in opps if o in asked]
    if worker_keys:
        opps = sorted({split_worker_key(k)[0] for k in worker_keys} & set(opps))
    if not opps:
        raise CaseBriefingError("no_opportunities", "There is no opportunity of this workflow to read cases in.")
    if source._stored:
        wanted = set(opps)
        cases = [c for c in source._stored["cases"] if int(c.get("opportunity_id") or 0) in wanted]
        origin = "stored"
    else:
        only = split_worker_key(worker_keys[0])[1] if worker_keys and len(worker_keys) == 1 else None
        cases, origin = source.opportunity_cases(opps, username=only), "live"
    catalog = source.catalog(opps[0])
    if not catalog:
        raise CaseBriefingError("no_case_states", "This workflow's registry declares no case states.")
    names = source.worker_names(lambda ids: run_roster(wda, run, definition, opportunity_ids=ids), opps)
    summary = detail == "summary"
    view = cases_view(
        cases,
        catalog,
        case_state=case_state,
        worker_keys=worker_keys,
        names=names,
        label_field=source.label_field(opps[0]),
        per_worker=1 if summary else per_worker,
        props_doc=source.props_doc(opps[0]),
    )
    if summary:
        view = summary_view(view, opps)
    else:
        view = {"detail": "cases", **view}
    return {
        "as_of": str(source.as_of)[:10] if source.as_of else None,
        "source": origin,
        "opportunity_ids": opps,
        **view,
        "timing": {**source.timings, "total": round(time.monotonic() - started, 2)},
    }
