"""Saving and opening scenarios: the one place the rules live, for the pages and for an agent.

* A scenario is saved by name, per person. Saving a name you already have replaces it only when asked
  (``overwrite``), so a click never loses work.
* It is private until its owner shares it. A shared one is read-only to everyone else; only the owner replaces or
  deletes it. A scenario someone else owns that you may not see is "not found": the answer for one that does not exist.
* What is stored is the scenario's canonical link, validated as it is saved (``Scenario.from_params``), so nothing that
  could not be opened is kept. Opening re-reads it with the models as they are now (``models_changed`` says when the
  specs have moved since it was saved).
"""

from __future__ import annotations

from django.db.models import Q
from django.urls import reverse

from connect_labs.effectiveness import engine, explore
from connect_labs.effectiveness.models import SavedScenario
from connect_labs.effectiveness.scenario import Scenario

MAX_NAME = 200


class SavedError(Exception):
    """A request that cannot be done, in words the person can act on. ``status`` is the HTTP answer for it."""

    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


def signed_in(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def _need_user(user) -> None:
    if not signed_in(user):
        raise SavedError("Sign in to save or open scenarios.", 403)


def stamp(scenario: Scenario) -> dict:
    """Which model specs a scenario is being saved against."""
    return {
        "commit": engine.source().get("commit"),
        "versions": {m: engine.spec(m).get("version") for m in scenario.models},
    }


def models_changed(saved_against: dict, scenario: Scenario) -> list[str]:
    """The programmes of a saved scenario whose model has moved since it was saved."""
    now = stamp(scenario)
    if (saved_against or {}).get("commit") != now["commit"]:
        before = (saved_against or {}).get("versions", {})
        return [m for m in scenario.models if before.get(m) != now["versions"][m]]
    return []


def _visible(user):
    if not signed_in(user):
        return SavedScenario.objects.filter(shared=True)
    return SavedScenario.objects.filter(Q(owner=user) | Q(shared=True))


def scenario_of(record: SavedScenario) -> Scenario:
    """The scenario a record describes, read with today's models. Raises ``explore.ExploreError`` when a model input
    it changed no longer exists."""
    return Scenario.from_params(record.params)


def _owner_label(record: SavedScenario) -> str:
    o = record.owner
    return getattr(o, "name", "") or o.get_username()


def as_dict(record: SavedScenario, viewer) -> dict:
    try:
        changed = models_changed(record.saved_against, scenario_of(record))
    except explore.ExploreError:
        changed = list(engine.MODELS)
    return {
        "id": record.pk,
        "name": record.name,
        "note": record.note,
        "params": record.params,
        "shared": record.shared,
        "mine": signed_in(viewer) and record.owner_id == viewer.pk,
        "owner": _owner_label(record),
        # Opens it on the workspace by id: follows the record, where ``params`` is the link as it was saved.
        "open_path": f"{reverse('cea:workspace')}?scenario={record.pk}",
        "updated_at": record.updated_at.isoformat(),
        "models_changed": changed,
    }


def listing(user) -> dict:
    """The viewer's own scenarios, then the ones others have shared."""
    rows = list(_visible(user).select_related("owner"))
    mine = [as_dict(r, user) for r in rows if signed_in(user) and r.owner_id == user.pk]
    others = [as_dict(r, user) for r in rows if not (signed_in(user) and r.owner_id == user.pk)]
    return {"mine": mine, "shared": others}


def get(user, pk: int) -> SavedScenario:
    record = _visible(user).select_related("owner").filter(pk=pk).first()
    if record is None:
        raise SavedError("That scenario is not available to you.", 404)
    return record


def save(user, name: str, params: dict, *, note: str = "", shared: bool = False, overwrite: bool = False):
    """Keep a scenario under ``name`` for ``user``. Returns ``(record, created)``."""
    _need_user(user)
    name = (name or "").strip()
    if not name:
        raise SavedError("Give the scenario a name.")
    if len(name) > MAX_NAME:
        raise SavedError(f"A name is at most {MAX_NAME} characters.")
    try:
        scenario = Scenario.from_params({k: str(v) for k, v in (params or {}).items()})
    except explore.ExploreError as exc:
        raise SavedError(exc.message) from None
    existing = SavedScenario.objects.filter(owner=user, name=name).first()
    if existing and not overwrite:
        raise SavedError(f"You already have a scenario called {name!r}.", 409, id=existing.pk)
    fields = {
        "note": (note or "").strip(),
        "params": scenario.to_params(),
        "saved_against": stamp(scenario),
        "shared": bool(shared),
    }
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.save()
        return existing, False
    return SavedScenario.objects.create(owner=user, name=name, **fields), True


def delete(user, pk: int) -> None:
    _need_user(user)
    record = get(user, pk)
    if record.owner_id != user.pk:
        raise SavedError("Only the person who saved a scenario can delete it.", 403)
    record.delete()
