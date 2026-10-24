"""Data spaces: the one thing every read of visit data goes through.

A space is a fixed set of sources (``models.DataSpace``) -- opportunities, programs frozen
to the opportunities they had, or one workflow -- resolved as the caller. Every read on the
Data page and through the ``data_*`` tools takes one (``service`` refuses anything else),
and there are exactly two ways to get one:

* ``generate`` -- an UNSAVED space from a selection, made for this call: each source
  resolved as the caller now, a program to the opportunities of it they hold now. Nothing
  is written. This is what the Data page reads when no space is open, and what a person's
  own saved query reads when it is opened.
* ``open`` -- a SAVED space by id, if the caller holds every one of its sources now.

``from_request`` is the door both surfaces use (a ``space_id`` opens, a selection
generates), and ``save`` writes a generated space's sources as a new saved space. Either
way the sources are resolved here, through ``_resolve``, and nowhere else
(``tests/test_spaces_gate.py``). The design is
docs/superpowers/specs/2026-10-24-data-spaces-design.md (and, for one path,
docs/superpowers/specs/2026-10-24-one-path-through-data-spaces.md); its rules, as this
module keeps them:

1. **You may open a saved space only while you hold every one of its sources, now.**
   ``open`` resolves the space's frozen opportunities as the caller -- refusing anything
   they do not hold -- on every call. Nothing is stored as a grant: there is no sharing
   step and no membership table. A teammate who holds the same sources sees the space;
   someone who loses one opportunity loses all of it.
2. **Programs are frozen to their opportunities.** A program source records the
   opportunities it had when it was added. Opening needs the program held AND every one of
   those opportunities held. Both checks, because holding a program does not mean holding
   all of it: ``scopes.program_ids`` is the programs whose organisation the person belongs
   to (Connect's ``opp_org_program_list``: a program manager, who holds every opportunity of
   it), but a labs-only program counts as held as soon as ONE of its opportunities is
   visible (``scopes.labs_only_program_ids``). Checking every frozen opportunity makes "no
   partial view" true whichever way a program is held. A generated space freezes too, at
   the moment it is made: what one call reads never moves under it.
3. **A space you may not open is ``not_found``** -- the same answer as one that does not
   exist, so its name does not leak. Only "we could not find out what you hold"
   (``scopes_unavailable``) is said as itself: that is a fact about Connect, not the space.

Changing a saved space's sources is the one dangerous write; ``update_sources`` takes only
an already-opened space.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.labs.access.scopes import Caller

from . import scope
from .models import DataSpace, SavedQuery

#: The ScopeError codes that mean "not yours" (or not a well-formed space): all not_found.
_NOT_YOURS = {"not_held", "no_opportunities", "bad_opportunity_ids", "bad_program_ids", "bad_scope"}


class SpaceNotFound(Exception):
    """No such space, or one the caller may not open -- deliberately the same answer."""

    code = "not_found"
    message = "There is no such data space, or you do not hold every one of its sources."


class OutsideSpace(Exception):
    """A call named a saved space AND a selection that is not the space's own sources.
    A space brings its own sources: one held to a space cannot widen or swap what it reads."""

    code = "outside_space"

    def __init__(self, space_id: int):
        self.space_id = space_id
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return (
            f"This call is held to data space {self.space_id}: it reads only the space's sources. "
            "Pass space_id alone (or the space's own sources)."
        )


class SpaceChanged(Exception):
    """The caller holds everything the space froze, but its workflow now reads other
    opportunities. Running it would answer over a different set than its name says, so it
    is refused until someone updates the space's sources."""

    code = "sources_changed"

    def __init__(self, added: list[int], removed: list[int]):
        self.added, self.removed = added, removed
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return (
            "This space's workflow now reads different opportunities than when the space was made "
            f"(added {self.added or 'none'}, removed {self.removed or 'none'}). Update the space's sources."
        )


@dataclass
class Selection:
    """What a space reads, resolved as the caller: opportunities (each held), the programs
    they came from, and -- for a workflow -- the workflow and its pipelines' relations."""

    opportunities: list
    programs: list[dict] = field(default_factory=list)
    workflow: dict | None = None
    relations: list = field(default_factory=list)
    not_offered: list[dict] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return [o.id for o in self.opportunities]

    def as_dict(self) -> dict:
        return {
            "opportunities": [{"id": o.id, "name": o.name, "llo": o.llo} for o in self.opportunities],
            "programs": self.programs,
            "workflow": self.workflow,
            "pipelines": [
                {
                    "relation": r.name,
                    "alias": r.alias,
                    "pipeline_id": r.pipeline_id,
                    "name": r.pipeline_name,
                    "stage": r.stage,
                }
                for r in self.relations
            ],
            "pipelines_not_offered": self.not_offered,
        }


def _resolve(caller: Caller, opportunity_ids=None, *, program_ids=None, workflow: dict | None = None) -> Selection:
    """Resolve sources as the caller, refusing anything they do not hold. Here and only here.

    Either programs and/or opportunities (a program is the opportunities of it the
    caller holds), or one workflow -- whose opportunities are the ones it reads,
    every one held, plus its pipelines as relations. Not both: a workflow's pipeline
    relations cover only its own opportunities, and ``visits`` beside them over a
    different set would answer a different question without saying so.
    """
    if workflow:
        if opportunity_ids or program_ids:
            raise scope.ScopeError(
                "bad_scope", "choose a workflow, or programs and opportunities -- a workflow brings its own"
            )
        from .pipelines import workflow_selection

        try:
            chosen = workflow_selection(
                caller,
                int(workflow["id"]),
                opportunity_id=workflow.get("opportunity_id"),
                program_id=workflow.get("program_id"),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise scope.ScopeError("bad_scope", "workflow needs an id and its opportunity_id or program_id") from e
        return Selection(
            opportunities=chosen.opportunities,
            workflow=chosen.workflow,
            relations=chosen.relations,
            not_offered=chosen.not_offered,
        )
    opps, programs = scope.expand(caller, opportunity_ids or [], program_ids or [])
    return Selection(opportunities=opps, programs=programs)


@dataclass
class OpenSpace:
    """A space the caller may read, saved or generated, with its sources resolved as them, now.

    The only thing ``service`` reads visit data over. Made by ``open`` (saved: ``space`` is
    the stored row) or ``generate`` (unsaved: ``space`` is an unsaved row holding the frozen
    sources, and ``requested`` is the selection it was made from)."""

    space: DataSpace
    selection: Selection
    #: Per program source: opportunities the caller holds in it that the space did not
    #: freeze (the program gained them). Only ones the caller holds, so nothing new leaks.
    new_in_programs: list[dict] = field(default_factory=list)
    #: Only from ``open(..., allow_changed=True)``: how a workflow space's workflow moved
    #: (``{added, removed, message}``). Such a space is shown and its sources may be
    #: updated, but nothing runs over it.
    changed: dict | None = None
    #: A generated space's selection as asked for -- programs as programs, so a link back
    #: to the page (or a personal saved query) asks again rather than for today's freeze.
    requested: dict = field(default_factory=dict)

    @property
    def saved(self) -> bool:
        return self.space.pk is not None

    @property
    def id(self) -> int | None:
        return self.space.pk

    def as_dict(self) -> dict:
        s = self.space
        programs = _program_names(self.selection.opportunities, s.program_ids)
        return {
            "id": s.pk,
            "saved": self.saved,
            "name": s.name,
            "description": s.description,
            "sources": {
                "programs": [
                    {
                        "id": pid,
                        "name": programs.get(pid) or f"Program {pid}",
                        "opportunity_ids": _of_program(self, pid),
                    }
                    for pid in s.program_ids
                ],
                "opportunities": [
                    {"id": o.id, "name": o.name, "llo": o.llo, "program_id": o.program_id}
                    for o in self.selection.opportunities
                ],
                "workflow": (
                    {**s.workflow, "name": (self.selection.workflow or {}).get("name")} if s.workflow else None
                ),
            },
            "opportunity_ids": list(s.opportunity_ids),
            "program_ids": list(s.program_ids),
            "workflow": dict(s.workflow) or None,
            "new_in_programs": self.new_in_programs,
            "changed": self.changed,
            "created_by": getattr(s.created_by, "username", None),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }


def _of_program(opened: OpenSpace, program_id: int) -> list[int]:
    return sorted(o.id for o in opened.selection.opportunities if o.program_id == program_id)


def _program_names(opps, program_ids) -> dict[int, str]:
    names = {o.program_id: o.program_name for o in opps if o.program_id is not None and o.program_name}
    return {pid: names.get(pid, "") for pid in program_ids or []}


def _holds(space: DataSpace, held: scopes.Holdings) -> bool:
    """Rules 1 and 2 over what the caller holds: every program and every frozen opportunity."""
    return all(int(p) in held.program_ids for p in space.program_ids or []) and all(
        int(o) in held.opportunity_ids for o in space.opportunity_ids or []
    )


def open(caller: Caller, space_id, *, allow_changed: bool = False) -> OpenSpace:  # noqa: A001 -- the one gate
    """The saved space, if the caller holds every one of its sources now; else ``SpaceNotFound``.

    Raises ``scope.ScopeError("scopes_unavailable")`` when what the caller holds cannot be
    established (never read as "they hold nothing"), and ``SpaceChanged`` when they hold
    everything but the space's workflow has moved under it -- unless ``allow_changed``
    (showing the space and updating its sources, never running a query), which returns it
    with ``changed`` set.
    """
    try:
        pk = int(space_id)
    except (TypeError, ValueError) as e:
        raise SpaceNotFound() from e
    space = DataSpace.objects.select_related("created_by").filter(pk=pk, archived_at__isnull=True).first()
    if space is None or not space.opportunity_ids:
        raise SpaceNotFound()
    try:
        held = scopes.holdings(caller)
    except scopes.ScopesUnavailable as e:
        raise scope.ScopeError("scopes_unavailable", scopes.UNKNOWABLE) from e
    if not _holds(space, held):
        raise SpaceNotFound()
    try:
        # Every frozen opportunity, through the same resolution every query uses: it
        # refuses one not held (and caps the count), so the space opens only whole.
        frozen = _resolve(caller, list(space.opportunity_ids))
        selection = frozen
        if space.workflow:
            selection = _resolve(caller, workflow=dict(space.workflow))
    except scope.ScopeError as e:
        if e.code in _NOT_YOURS:
            raise SpaceNotFound() from e
        raise
    if space.workflow:
        now, then = set(selection.ids), {int(o) for o in space.opportunity_ids}
        if now != then:
            moved = SpaceChanged(sorted(now - then), sorted(then - now))
            if not allow_changed:
                raise moved
            changed = {"added": moved.added, "removed": moved.removed, "message": moved.message}
            return OpenSpace(space=space, selection=frozen, changed=changed)
    return OpenSpace(space=space, selection=selection, new_in_programs=_new_in_programs(caller, space, held))


def _ints(values) -> list[int]:
    try:
        return sorted({int(v) for v in values or []})
    except (TypeError, ValueError) as e:
        raise scope.ScopeError("bad_opportunity_ids", "opportunity and program ids must be integers") from e


def _workflow_ref(workflow) -> dict:
    """``{id, opportunity_id | program_id}`` -- the scope a workflow is read in."""
    if not isinstance(workflow, dict):
        raise scope.ScopeError("bad_scope", "workflow needs an id and its opportunity_id or program_id")
    try:
        out = {"id": int(workflow["id"])}
        for key in ("opportunity_id", "program_id"):
            if workflow.get(key):
                out[key] = int(workflow[key])
    except (KeyError, TypeError, ValueError) as e:
        raise scope.ScopeError("bad_scope", "workflow needs an id and its opportunity_id or program_id") from e
    return out


def generate(caller: Caller, opportunity_ids=None, *, program_ids=None, workflow=None) -> OpenSpace:
    """An unsaved space over a selection, for this call: each source resolved as the caller
    now (refusing anything not held), a program frozen to the opportunities of it they hold
    now, a workflow to the opportunities it reads now. Nothing is written."""
    selection = _resolve(caller, opportunity_ids, program_ids=program_ids, workflow=workflow)
    if selection.workflow:
        ref = _workflow_ref(workflow)
        sources = {"opportunity_ids": sorted(selection.ids), "program_ids": [], "workflow": ref}
        requested = {"opportunity_ids": [], "program_ids": [], "workflow": ref}
    else:
        sources = {
            "opportunity_ids": sorted(selection.ids),
            "program_ids": sorted(int(p["id"]) for p in selection.programs),
            "workflow": {},
        }
        requested = {"opportunity_ids": _ints(opportunity_ids), "program_ids": _ints(program_ids), "workflow": {}}
    return OpenSpace(space=DataSpace(name="", **sources), selection=selection, requested=requested)


def _names_other_sources(space: DataSpace, opportunity_ids, program_ids, workflow) -> bool:
    """Whether a call naming a saved space also names a selection that is not its own."""
    try:
        return bool(
            (opportunity_ids and set(_ints(opportunity_ids)) != {int(o) for o in space.opportunity_ids})
            or (program_ids and set(_ints(program_ids)) != {int(p) for p in space.program_ids})
            or (
                workflow
                and (
                    not isinstance(workflow, dict) or _ints([workflow.get("id")]) != _ints([space.workflow.get("id")])
                )
            )
        )
    except scope.ScopeError:
        return True


def from_request(caller: Caller, *, space_id=None, opportunity_ids=None, program_ids=None, workflow=None) -> OpenSpace:
    """The space a call reads -- the one door the Data page and the ``data_*`` tools use.

    With ``space_id``: that saved space, through ``open``. Anything else the call names
    must be the space's own sources (``OutsideSpace`` otherwise), so a call held to a space
    cannot widen or swap what it reads. Without one: a space generated from the selection."""
    if space_id not in (None, ""):
        opened = open(caller, space_id)
        if _names_other_sources(opened.space, opportunity_ids, program_ids, workflow):
            raise OutsideSpace(opened.id)
        return opened
    return generate(caller, opportunity_ids, program_ids=program_ids, workflow=workflow)


def _new_in_programs(caller: Caller, space: DataSpace, held: scopes.Holdings) -> list[dict]:
    if not space.program_ids:
        return []
    frozen = {int(o) for o in space.opportunity_ids}
    seen = scope.directory(caller)
    out = []
    for pid in space.program_ids:
        added = sorted(
            o.id for o in seen.values() if o.program_id == pid and o.id in held.opportunity_ids and o.id not in frozen
        )
        if added:
            out.append({"program_id": pid, "opportunity_ids": added})
    return out


def openable(caller: Caller) -> list[dict]:
    """Every saved space the caller may open, grouped by program or opportunity, for the switcher.

    Rules 1 and 2 over one resolution of what the caller holds (``_holds``, which ``open``
    applies too). A workflow space is listed on its frozen sources; opening it also checks
    the workflow, so one whose workflow moved says so when opened."""
    try:
        held = scopes.holdings(caller)
    except scopes.ScopesUnavailable as e:
        raise scope.ScopeError("scopes_unavailable", scopes.UNKNOWABLE) from e
    spaces = [s for s in DataSpace.objects.filter(archived_at__isnull=True).order_by("name") if _holds(s, held)]
    if not spaces:
        return []
    names = scope.directory(caller)
    groups: dict[str, dict] = {}
    for s in spaces:
        if s.program_ids:
            pid = int(s.program_ids[0])
            pname = next((o.program_name for o in names.values() if o.program_id == pid and o.program_name), "")
            key, label = f"program:{pid}", f"Program: {pname or pid}"
        elif len(s.opportunity_ids) == 1:
            oid = int(s.opportunity_ids[0])
            opp = names.get(oid)
            key, label = f"opportunity:{oid}", f"Opportunity: {opp.name if opp else oid}"
        else:
            key, label = "several", "Several opportunities"
        groups.setdefault(key, {"key": key, "label": label, "spaces": []})["spaces"].append(
            {
                "id": s.pk,
                "name": s.name,
                "description": s.description,
                "opportunity_ids": list(s.opportunity_ids),
                "program_ids": list(s.program_ids),
                "workflow": dict(s.workflow) or None,
            }
        )
    return sorted(groups.values(), key=lambda g: (g["key"] == "several", g["label"].lower()))


def _sources(generated: OpenSpace) -> dict:
    if generated.saved:
        raise ValueError("a saved space's sources come from a generated space")
    s = generated.space
    return {
        "opportunity_ids": list(s.opportunity_ids),
        "program_ids": list(s.program_ids),
        "workflow": dict(s.workflow),
    }


def save(caller: Caller, generated: OpenSpace, name: str, description: str = "") -> DataSpace:
    """Keep a generated space: its frozen sources, under a name, for everyone who holds them."""
    return DataSpace.objects.create(name=name, description=description, created_by=caller.user, **_sources(generated))


def update_sources(opened: OpenSpace, generated: OpenSpace) -> DataSpace:
    """Re-freeze an OPEN saved space to a generated space's sources. Callers: the person's
    own session only, after a confirm -- adding a source locks out everyone who does not
    hold it, and who that is cannot be known here."""
    space = opened.space
    for key, value in _sources(generated).items():
        setattr(space, key, value)
    space.save(update_fields=["opportunity_ids", "program_ids", "workflow", "updated_at"])
    return space


def archive(opened: OpenSpace) -> DataSpace:
    space = opened.space
    space.archived_at = timezone.now()
    space.save(update_fields=["archived_at", "updated_at"])
    return space


def queries(opened: OpenSpace):
    """The saved space's saved queries (every version)."""
    return SavedQuery.objects.select_related("owner").filter(space=opened.space)


def query_item(opened: OpenSpace, pk) -> SavedQuery | None:
    try:
        return queries(opened).filter(pk=int(pk)).first()
    except (TypeError, ValueError):
        return None


def reads(space_id) -> list[int] | None:
    """The opportunities a space reads, for the "no user visit data" provenance gate only
    (``mcp/visit_access.py``): it checks every one is generated before a restricted call
    runs. Grants nothing and returns nothing to a caller -- the tool still opens the space
    through ``open``. None for no such space."""
    try:
        pk = int(space_id)
    except (TypeError, ValueError):
        return None
    ids = DataSpace.objects.filter(pk=pk, archived_at__isnull=True).values_list("opportunity_ids", flat=True).first()
    return sorted(int(i) for i in ids) if ids else None
